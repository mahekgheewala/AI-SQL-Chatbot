"""
Agent Coordinator
==================
Lightweight orchestration loop that sits above the existing backend pipeline.

Responsibilities (and ONLY these):
  1. Decide which tool should handle the request.
  2. Execute the selected tool.
  3. Decide whether another tool is needed.
  4. Return the final ChatResponse payload.

The coordinator NEVER:
  - Opens a PostgreSQL connection.
  - Generates SQL.
  - Runs validation.
  - Updates session memory directly.
  - Refreshes metadata or routing summaries directly.
  - Re-classifies or re-grounds the user's message — that already happened
    once, in agent.universal_gateway.decide(), before this function was ever
    called. The caller passes the resulting GatewayDecision in as `decision`;
    its `.semantic_frame` (already grounded) is the only source of "can this
    be dispatched to a tool without an LLM call" here.

All real work happens inside the tool functions in tools.py, which themselves
delegate to the existing execution/validation pipeline.
"""

import json
import re
import os
import time
from typing import Optional

import google.generativeai as genai

from agent.prompts import AGENT_SYSTEM_PROMPT
from agent.gemini_retry import call_with_retry          # Phase 8.1 OPT-1
from agent import gemini_metrics                        # Phase 8.5 instrumentation
from utils.logging_config import logger_ai
from agent.tools import (
    create_database,
    create_table,
    drop_database,
    drop_table,
    alter_table,
    rename_table,
    execute_sql,
    generate_report,
    get_schema_info,
    list_databases,
    list_tables,
    summarize_results,
    ExecutionContext,
    build_execution_context,
)
from state.metadata_store import get_metadata

# Import centralized model manager
from ai import model_manager
from agent.local_planner import plan as local_plan

UTILITY_TOOL_INTENTS = {
    "switch_database": "SWITCH_DATABASE",
    "list_tables": "LIST_TABLES",
    "list_databases": "LIST_DATABASES",
    "get_schema_info": "GET_SCHEMA",
    "find_table_database": "FIND_TABLE_DATABASE",
}

# Terminal tools end the loop immediately after execution
_TERMINAL_TOOLS = {"summarize_results", "generate_report", "final_response"}

# Maximum tool invocations per user request (Phase 8 spec)
_MAX_TOOL_CALLS = 3

# ─── Phase 8.4 OPT-3: Formatting keyword guard ──────────────────────────────
# If ANY of these words appear anywhere in the message, deterministic dispatch is
# suppressed so the Planner can handle execute_sql → format chaining.
_FORMATTING_KEYWORDS = re.compile(
    r"\b(summarize|summary|report|generate\s+report|format"
    r"|analysis|analyze|insight|insights|trend|trends)\b",
    re.IGNORECASE,
)

_VISUALIZATION_KEYWORDS = re.compile(
    r"\b(plot|chart|graph|visualize|visualization|histogram|scatter|pie|box|distribution)\b",
    re.IGNORECASE,
)

# ─── Phase 8.10: Deterministic dispatch rules ────────────────────────────────
# Phase 4.5 Finding 2: Generalised switch-DB rule.
# Matches (all case-insensitive, trailing semicolon optional):
#   use my_name
#   use database my_name
#   use db my_name
#   switch to my_name
#   switch database my_name
#   switch to database my_name
#   select database my_name
_SWITCH_DB_RULE = re.compile(
    r"^\s*(?:use|switch(?:\s+to)?|select\s+database)\s+(?:database\s+|db\s+)?(?P<db_name>[a-zA-Z0-9_-]+)\s*;?\s*$",
    re.IGNORECASE,
)

_SHOW_TABLE_RULE = re.compile(
    r"^\s*(?:show|display|list)\s+table\s+(?P<table_name>[a-zA-Z0-9_-]+)\s*;?\s*$",
    re.IGNORECASE,
)

_FIND_TABLE_DB_RULE = re.compile(
    r"^\s*(?:"
    r"which\s+database\s+contains"
    r"|which\s+db\s+contains"
    r"|which\s+database\s+has"
    r"|which\s+db\s+has"
    r"|in\s+which\s+database\s+is"
    r"|in\s+which\s+db\s+is"
    r"|where\s+is(?:\s+the)?(?:\s+table)?"
    r"|where\s+can\s+i\s+find(?:\s+the)?(?:\s+table)?"
    r")\s+(?P<table_name>[a-zA-Z0-9_-]+)\s*;?\s*$",
    re.IGNORECASE
)

# ─── Narrow utility patterns without a semantic_frame capability yet ─────────
# "which database contains X" / "show table X" have no equivalent capability
# in agent/capabilities.py, so they're kept as small standalone checks rather
# than folded into the frame. (Follow-up: promote these to real capabilities.)

def _find_table_location_tool(message: str) -> Optional[str]:
    if _FIND_TABLE_DB_RULE.match(message):
        return "find_table_database"
    if _SHOW_TABLE_RULE.match(message):
        return "get_schema_info"
    return None


# ─── Tool selection from the gateway's already-grounded SemanticFrame ───────
# agent.universal_gateway.decide() has already run interpret_message() exactly
# once (before this coordinator was ever invoked) and produced a fully-grounded
# frame — nothing here re-parses or re-grounds the message.

_SEMANTIC_TOOL_MAP: dict = {
    "create_database": "create_database",
    "create_table": "create_table",
    "add_column": "alter_table",
    "drop_column": "alter_table",
    "alter_column": "alter_table",
    "drop_table": "drop_table",
    "drop_database": "drop_database",
    "rename_table": "rename_table",
    "list_tables": "list_tables",
    "list_databases": "list_databases",
    "describe_table": "get_schema_info",
    "switch_database": "switch_database",
    "retrieve": "execute_sql",
    "raw_sql": "execute_sql",
    # Capabilities below intentionally have NO tool mapping — they fall through
    # to the planner / visualization pipeline (charts, sample data, chat).
    "visualize": None,
    "add_sample_data": None,
    "general_conversation": None,
    "understanding_failed": None,
}


def _tool_from_frame(frame, pipeline_hint: Optional[str], user_message: str) -> dict:
    """Map the gateway's already-grounded SemanticFrame to a tool call.

    Returns {"tool", "tool_input", "legacy_intent"}. When nothing maps — a
    capability intentionally has no direct tool (visualize/add_sample_data),
    or a retrieve frame needs aggregation/filter reasoning the deterministic
    SQL builder doesn't support — tool/tool_input are None and the caller
    falls through to the planner.
    """
    result = {"tool": None, "tool_input": None, "legacy_intent": None}
    if frame is None:
        return result

    # Defensive: a frame with unresolved required roles is not actually
    # grounded (the gateway should already have turned this into a
    # clarification before the coordinator was ever reached) — never dispatch
    # a tool call from an incomplete frame.
    if frame.missing_required:
        return result

    # Visualization/analytics pipelines own their own request handling.
    if pipeline_hint in ("VISUALIZATION", "ANALYTICS_ENGINE"):
        return result

    cap_id = frame.capability_id
    tool_name = _SEMANTIC_TOOL_MAP.get(cap_id)
    if tool_name is None:
        return result

    result["legacy_intent"] = frame.legacy_intent

    # switch_database is handled inline in the coordinator loop below.
    if cap_id == "switch_database":
        result["tool"] = tool_name
        result["tool_input"] = frame.database
        return result

    if cap_id == "retrieve":
        from agent.semantic_frame import frame_to_query_intent
        from agent.deterministic_sql_builder import build_sql, is_deterministically_executable
        query_intent = frame_to_query_intent(frame)
        if query_intent is None:
            return result
        executable, _reason = is_deterministically_executable(query_intent, target_table=frame.table)
        if not executable:
            return result  # needs LocalPlanner SQL reasoning
        try:
            result["tool"] = tool_name
            result["tool_input"] = build_sql(query_intent, target_table=frame.table)
        except Exception:
            result["tool"] = None
        return result

    from agent.semantic_frame import instruction_for_frame
    result["tool"] = tool_name
    result["tool_input"] = instruction_for_frame(frame, user_message)
    return result


def _format_schema_context_for_planner(target_db: Optional[str], session: dict) -> str:
    """
    Builds a compact schema context string for the planner prompt.
    Mirrors context_builder.py but produces a shorter token footprint for routing decisions.
    """
    from state.metadata_store import get_metadata, sync_database_context
    active = target_db or session.get("selected_database") or "None"
    if active and active != "None":
        sync_database_context(active)
    
    meta       = get_metadata()
    dbs        = meta.get("databases", [])
    routing    = meta.get("routing_summaries", {})
    raw_schema = meta.get("schema", {})
    fks        = meta.get("foreign_keys", {})

    lines = [
        f"ACTIVE DATABASE: {active}",
        f"AVAILABLE DATABASES: {', '.join(dbs) if dbs else 'None'}",
    ]

    if raw_schema:
        lines.append("ACTIVE DATABASE TABLE SCHEMAS:")
        for tbl, cols in raw_schema.items():
            lines.append(f"  {tbl}: [{', '.join(cols)}]")

    if fks:
        lines.append("ACTIVE DATABASE FOREIGN KEYS (VERIFIED JOIN RELATIONSHIPS):")
        for tbl, fk_list in fks.items():
            for fk in fk_list:
                lines.append(f"  {tbl}.{fk['column']} REFERENCES {fk['foreign_table']}.{fk['foreign_column']}")
    else:
        lines.append("ACTIVE DATABASE FOREIGN KEYS (VERIFIED JOIN RELATIONSHIPS): None")

    if routing:
        lines.append("DATABASE → TABLES INDEX:")
        for db, tables in routing.items():
            lines.append(f"  {db}: [{', '.join(tables) if tables else 'no tables'}]")

    session_db  = session.get("selected_database")
    session_tbl = session.get("selected_table")
    last_intent = session.get("last_successful_intent")
    if session_db or session_tbl or last_intent:
        lines.append("SESSION MEMORY:")
        if session_db:   lines.append(f"  Selected Database : {session_db}")
        if session_tbl:  lines.append(f"  Selected Table    : {session_tbl}")
        if last_intent:  lines.append(f"  Last Intent       : {last_intent}")

    return "\n".join(lines)


def _trace_summary(tool_name: str, tool_input: Optional[str], tool_result: dict | str) -> str:
    """
    Produces a one-line summary of a tool execution for the planning trace.
    Raw result rows are NEVER included — only metadata summaries.
    This keeps token growth bounded across multi-step executions.
    """
    if isinstance(tool_result, str):
        # Formatting tool output — truncate to first 120 chars
        preview = tool_result[:120].replace("\n", " ")
        return f"Tool: {tool_name} | Input: {tool_input or '—'} | Output: {preview}…"

    # dict result from execute_sql / create_* / get_schema_info / list_*
    execution = tool_result.get("execution")
    intent    = tool_result.get("intent", "")
    risk      = tool_result.get("risk_level", "")

    if tool_result.get("intent") in {"NEEDS_CLARIFICATION", "UNKNOWN"}:
        question = tool_result.get("question", "")
        return f"Tool: {tool_name} | Intent: {intent} | Question: {question}"

    if not tool_result.get("valid", True):
        reason = tool_result.get("failure_reason") or tool_result.get("blocked_reason", "")
        return f"Tool: {tool_name} | INVALID | Risk: {risk} | Reason: {reason}"

    if tool_result.get("requires_confirmation"):
        return f"Tool: {tool_name} | AWAITING CONFIRMATION | Risk: {risk}"

    if execution:
        if execution.get("success"):
            op    = execution.get("operation", "")
            count = execution.get("row_count", 0)
            return f"Tool: {tool_name} | Intent: {intent} | {op} succeeded | {count} row(s)"
        else:
            error = str(execution.get("error", "unknown error"))[:100]
            return f"Tool: {tool_name} | EXECUTION FAILED | {error}"

    # get_schema_info / list_* tools
    if "databases" in tool_result:
        count = len(tool_result["databases"])
        return f"Tool: {tool_name} | {count} database(s) returned"
    if "tables" in tool_result:
        count = len(tool_result.get("tables", []))
        return f"Tool: {tool_name} | {count} table(s) returned"
    if "schema" in tool_result:
        count = len(tool_result.get("schema", {}))
        return f"Tool: {tool_name} | Schema info returned | {count} table(s)"

    return f"Tool: {tool_name} | Completed"


def _build_success_reply(tool_name: str, tool_result: dict) -> str:
    """
    Phase 8.1 OPT-2 + Phase 8.10: Build a direct success/failure reply for SQL tools 
    without invoking a second Planner pass.
    """
    exec_res = tool_result.get("execution", {}) or {}
    op       = exec_res.get("operation", "")
    db       = tool_result.get("target_db", "")

    if exec_res and not exec_res.get("success", True):
        error = exec_res.get("error", "unknown error")
        return f"❌ SQL execution failed: {error}"

    # Handle CREATE/DROP/ALTER DATABASE / TABLE within execute_sql or their wrapper tools
    if tool_name == "create_database" or op == "CREATE DATABASE":
        sql = tool_result.get("sql", "") or ""
        parts = sql.strip().split()
        db_name = parts[2].strip('";') if len(parts) >= 3 else db
        return f"✅ Database **{db_name}** has been successfully created."

    if tool_name == "drop_database" or op == "DROP DATABASE":
        sql = tool_result.get("sql", "") or ""
        parts = sql.strip().split()
        db_name = parts[2].strip('";') if len(parts) >= 3 else db
        return f"✅ Database **{db_name}** has been successfully dropped."

    if tool_name == "create_table" or op == "CREATE TABLE":
        sql = tool_result.get("sql", "") or ""
        from db.executor import extract_table_name
        tbl_name = extract_table_name(sql) or "the table"
        return f"✅ Table **{tbl_name}** has been successfully created."

    if tool_name == "drop_table" or op == "DROP TABLE":
        sql = tool_result.get("sql", "") or ""
        from db.executor import extract_table_name
        tbl_name = extract_table_name(sql) or "the table"
        return f"✅ Table **{tbl_name}** has been successfully dropped."

    if tool_name == "alter_table" or op == "ALTER TABLE":
        return f"✅ Table has been successfully altered."

    if tool_name == "rename_table" or op == "RENAME TABLE":
        return f"✅ Table has been successfully renamed."

    if tool_name == "execute_sql":
        row_count = exec_res.get("row_count", 0)
        if op in {"SELECT", "QUERY"}:
            return f"Query executed successfully — {row_count} row(s) returned."
        if op in {"INSERT"}:
            return f"✅ {row_count} row(s) inserted successfully."
        if op in {"UPDATE"}:
            return f"✅ {row_count} row(s) updated successfully."
        if op in {"DELETE"}:
            return f"✅ {row_count} row(s) deleted successfully."
        return f"✅ Operation {op} completed successfully."

    return f"✅ SQL executed successfully ({op})."


def run(
    user_message: str,
    router_db: Optional[str],
    session: dict,
    session_id: Optional[str],
    history: list,
    target_db: Optional[str],
    bypass_planner: bool = False,
    pipeline_hint: Optional[str] = None,
    decision: Optional[object] = None,
) -> dict:
    """
    Main agent orchestration loop.

    Parameters:
        user_message - current user input
        router_db    - database resolved by the router (may be None)
        session      - session memory dict
        session_id   - UUID from frontend (for session updates)
        history      - last N conversation turns
        target_db    - fallback-chain database resolution from chat.py
        bypass_planner - if True, bypasses planner and invokes execute_sql directly
        pipeline_hint - Optional pipeline signal ("ANALYTICS_ENGINE", "VISUALIZATION", "STANDARD_SQL")
        decision     - the GatewayDecision already computed once by
                       agent.universal_gateway.decide() for this request. Its
                       .understanding.intent and .semantic_frame are used
                       directly — this function does not re-classify or
                       re-ground the message. Callers without one yet (a
                       direct/manual invocation, e.g. some tests) may omit it;
                       the coordinator computes it itself in that case only.

    Returns:
        A dict with all fields needed to populate a ChatResponse:
            reply, intent, database, sql, question,
            valid, risk_level, requires_confirmation, blocked_reason,
            execution, refresh_databases, refresh_tables, refresh_schema,
            formatted_text (str | None) - populated by formatting tools
    """
    if decision is not None:
        understanding = decision.understanding
        frame = decision.semantic_frame
    else:
        from agent.universal_gateway import process_universal_semantic_gateway
        understanding = process_universal_semantic_gateway(
            user_message=user_message,
            raw_message=user_message,
            database_context=target_db,
            session=session
        )
        frame = understanding.semantic_frame
    user_intent = understanding.intent

    schema_context = _format_schema_context_for_planner(router_db, session)
    execution_trace: list[str] = []
    last_execution_result: Optional[dict] = None

    # Accumulated response fields — defaults match ChatResponse defaults
    response_payload: dict = {
        "reply":                 "",
        "intent":                "UNKNOWN",
        "database":              target_db,
        "sql":                   None,
        "question":              None,
        "valid":                 True,
        "risk_level":            "SAFE",
        "requires_confirmation": False,
        "blocked_reason":        None,
        "execution":             None,
        "refresh_databases":     False,
        "refresh_tables":        False,
        "refresh_schema":        False,
        "formatted_text":        None,   # populated by summarize_results / generate_report
    }

    print("\n====================================")
    print("AGENT COORDINATOR")
    print("====================================")
    print(f"User Message: {user_message}")
    print(f"Router DB   : {router_db or 'None'}")
    print(f"Target DB   : {target_db or 'None'}")

    # ── Tool selection ────────────────────────────────────────────────────
    # The gateway already grounded this request exactly once; map its frame
    # to a tool directly instead of re-parsing the message here. The narrow
    # find-table-location check has no semantic_frame capability yet (see
    # _find_table_location_tool's docstring) so it's tried first and the
    # frame-derived tool (when present) wins over it.
    first_tool_override: Optional[str] = _find_table_location_tool(user_message)
    first_tool_instruction: Optional[str] = None

    frame_dispatch = _tool_from_frame(frame, pipeline_hint, user_message)
    if frame_dispatch["tool"]:
        first_tool_override = frame_dispatch["tool"]
        first_tool_instruction = frame_dispatch["tool_input"]
        if frame_dispatch["legacy_intent"]:
            user_intent = frame_dispatch["legacy_intent"]

    if first_tool_override:
        gemini_metrics.record_bypass("planner")
        print(f"[Coordinator] Planner bypass recorded — request routed directly to '{first_tool_override}'.")

    # ── Phase 2: Local Planner (Qwen 3) ──────────────────────────────────────
    planning_doc = None
    if not first_tool_override:
        intent_meta = session.get("last_intent_meta", {})
        planning_doc = local_plan(
            user_message=user_message,
            intent_meta=intent_meta,
            schema_context=schema_context,
            history=history,
        )
        plan_inner = planning_doc.get("planning_document", {}) if planning_doc else {}
        
        # Override user_intent with the Planner's detected intent if available
        if plan_inner and "user_intent" in plan_inner:
            planner_detected_intent = plan_inner.get("user_intent")
            if planner_detected_intent and planner_detected_intent != "Unknown":
                user_intent = planner_detected_intent
                
        if plan_inner.get("clarification_required"):
            response_payload["intent"] = "NEEDS_CLARIFICATION"
            response_payload["reply"] = plan_inner.get("clarification_question")
            response_payload["question"] = plan_inner.get("clarification_question")
            response_payload["valid"] = True
            response_payload["risk_level"] = None
            
            db_context = plan_inner.get("database_context") or {}
            options = db_context.get("required_schema_objects") or []
            
            response_payload["clarification_data"] = {
                "type": "MISSING_DATABASE" if "database" in user_message.lower() else "AMBIGUOUS_TABLE",
                "original_request": user_message,
                "options": options,
                "target_db": target_db,
                "question": plan_inner.get("clarification_question"),
            }
            return response_payload

    for step in range(_MAX_TOOL_CALLS):

        # ── Determine which tool to run this step ─────────────────────────────
        if first_tool_override and step == 0:
            # OPT-3 / Phase 10.6: skip Planner AI, dispatch directly (keyword or semantic)
            tool_name   = first_tool_override
            tool_input  = first_tool_instruction
            thought     = f"[Direct dispatch] → {tool_name}"
            print(f"\n[Step {step + 1}] Direct Dispatch: {tool_name}")
        else:
            # ── Build planning prompt ─────────────────────────────────────────
            trace_block = ""
            if execution_trace:
                trace_block = "EXECUTION TRACE (previous steps this request):\n"
                trace_block += "\n".join(f"  Step {i+1}: {t}" for i, t in enumerate(execution_trace))
                trace_block += "\n\n"

            history_block = ""
            if history:
                history_lines = ["CONVERSATION HISTORY:"]
                for msg in history[-6:]:
                    if hasattr(msg, "role"):
                        role = "User" if msg.role == "user" else "Assistant"
                        text = msg.text
                    else:
                        role = "User" if msg.get("role") == "user" else "Assistant"
                        text = msg.get("text", "")
                    history_lines.append(f"  {role}: {text}")
                history_block = "\n".join(history_lines) + "\n\n"

            # ── Build executor prompt (incorporating Local Planner's Planning Document)
            executor_prompt = (
                f"You are the Gemini Executor for an AI SQL Assistant.\n"
                f"You have been provided with a high-level Planning Document compiled by the Local Planner.\n"
                f"Your task is to EXECUTE this plan. You must decide which tool should be called next, generate the correct tool arguments, and handle SQL generation.\n\n"
                f"EXECUTION RULES:\n"
                f"1. SINGLE-TABLE PREFERENCE: Prefer querying a single table if it contains enough columns to provide a meaningful answer. Do NOT generate unnecessary JOINs.\n"
                f"2. FOREIGN-KEY JOIN GROUNDING: You may ONLY generate JOIN clauses between tables if an explicit Foreign Key relationship is listed under ACTIVE DATABASE FOREIGN KEYS in CONTEXT. NEVER join tables on non-key columns (e.g. NEVER join ON e.salary = s.salary or ON e.name = s.name).\n"
                f"3. UNCONNECTED TABLES & LIMITATION EXPLANATION: If the user request requires columns from multiple tables (e.g. employee names and department) but NO Foreign Key relationship exists between those tables, query the primary table containing the filter criteria (e.g. SELECT department, salary, hire_date FROM salaries WHERE department = 'Engineering') and include a clear note that individual employee names cannot be linked to departments in this schema.\n"
                f"4. DIRECT FILTERING: Filter directly on table columns (e.g. WHERE department = 'Engineering') without attempting invalid or fabricated joins.\n"
                f"5. FOLLOW-UP CONTEXT PRESERVATION: When the user message is a follow-up query (e.g. 'Now just the ones hired after 2022.'), you MUST preserve and combine active filter conditions from previous turns in CONVERSATION HISTORY (e.g. department = 'Engineering') with the new filter conditions (e.g. hire_date > '2022-12-31') in your WHERE clause.\n\n"
                f"PLANNING DOCUMENT:\n"
                f"{json.dumps(planning_doc, indent=2) if planning_doc else 'None'}\n\n"
                f"CONTEXT:\n"
                f"{schema_context}\n\n"
                f"{history_block}"
                f"{trace_block}"
                f"USER REQUEST: {user_message}\n\n"
                f"Return ONLY a JSON object with this exact structure:\n"
                f"{{\n"
                f"  \"thought\": \"Detailed reasoning of your execution decision aligning with the Planning Document\",\n"
                f"  \"tool\": \"Name of the tool to invoke\",\n"
                f"  \"tool_input\": \"Input parameters or arguments for the tool\"\n"
                f"}}"
            )

            # Determine Executor Trigger Reason
            if _FORMATTING_KEYWORDS.search(user_message):
                if any(w in user_message.lower() for w in ["summarize", "summary"]):
                    planner_trigger_reason = "SUMMARY_REQUEST"
                elif any(w in user_message.lower() for w in ["report"]):
                    planner_trigger_reason = "REPORT_REQUEST"
                else:
                    planner_trigger_reason = "FORMATTING_REQUEST"
            elif step > 0:
                planner_trigger_reason = "MULTI_STEP_REQUEST"
            else:
                planner_trigger_reason = "UNKNOWN_INTENT"

            print(f"Executor Trigger Reason: {planner_trigger_reason}")

            # ── Call Gemini Executor with rate-limit retry ───────────────────
            print(f"\n[Step {step + 1}] Calling executor model…")
            start_time = time.perf_counter()
            try:
                # Record call under 'planner' key to maintain compatibility with metrics/dashboards
                gemini_metrics.record_call("planner")
                raw_response, success, rate_limit_msg = call_with_retry(
                    fn=lambda: model_manager.PLANNER_MODEL.generate_content(executor_prompt),
                    label="Executor",
                )
                duration_ms = (time.perf_counter() - start_time) * 1000

                if not success:
                    print(f"[Phase 10.4.x] Planner call failed. Attempting capability fallback...")
                    try:
                        logger_ai.error(
                            f"Planner call failed: rate limit exhausted - {rate_limit_msg}",
                            extra={
                                "category": "ai",
                                "operation_type": "PLANNER",
                                "intent": "PLANNING_STEP",
                                "success": False,
                                "execution_time_ms": duration_ms,
                                "error": rate_limit_msg,
                                "model": model_manager.current_model_name(),
                                "step": step + 1
                            }
                        )
                    except Exception:
                        pass
                    
                    # Dynamic capability-based fallback
                    from agent.ddl_parser import parse_simple_ddl
                    parsed_ddl = parse_simple_ddl(user_message, session)
                    if parsed_ddl and parsed_ddl.deterministic and parsed_ddl.sql:
                        print(f"[Phase 10.4.x] Fallback: Deterministic DDL parser can handle: {parsed_ddl.operation}")
                        tool_res = None
                        if parsed_ddl.operation == "CREATE_DATABASE":
                            tool_res = create_database(user_message)
                        elif parsed_ddl.operation == "DROP_DATABASE":
                            tool_res = drop_database(user_message)
                        elif parsed_ddl.operation == "CREATE_TABLE":
                            tool_res = create_table(user_message)
                        elif parsed_ddl.operation == "DROP_TABLE":
                            tool_res = drop_table(user_message)
                        elif parsed_ddl.operation == "ALTER_TABLE":
                            tool_res = alter_table(user_message)
                        elif parsed_ddl.operation == "RENAME_TABLE":
                            tool_res = rename_table(user_message)
                        
                        if tool_res and isinstance(tool_res, dict) and tool_res.get("execution", {}).get("success"):
                            response_payload["reply"] = _build_success_reply(parsed_ddl.operation.lower(), tool_res)
                            response_payload["sql"] = parsed_ddl.sql
                            break

                    msg_lower = user_message.lower()
                    if "database" in msg_lower or "db" in msg_lower:
                        response_payload["reply"] = "I can create the database if you provide a simple name. Example: Create database company_db"
                    elif "report" in msg_lower or "summary" in msg_lower:
                        response_payload["reply"] = "I can generate the report once the AI service becomes available. Meanwhile you can still run SQL queries normally."
                    else:
                        response_payload["reply"] = "The AI planner is temporarily busy. Meanwhile you can still run SQL queries normally."
                    break

                prompt_tokens = 0
                completion_tokens = 0
                cost = 0.0
                if raw_response and hasattr(raw_response, "usage_metadata") and raw_response.usage_metadata:
                    prompt_tokens = raw_response.usage_metadata.prompt_token_count
                    completion_tokens = raw_response.usage_metadata.candidates_token_count
                    gemini_metrics.record_tokens(
                        "planner",
                        prompt_tokens,
                        completion_tokens
                    )
                    cost = gemini_metrics.estimate_cost(prompt_tokens, completion_tokens)

                plan = json.loads(raw_response.text)
                tool_name  = plan.get("tool", "final_response")
                tool_input = plan.get("tool_input")
                thought    = plan.get("thought", "")

                log_extra = {
                    "category": "ai",
                    "operation_type": "PLANNER",
                    "intent": "PLANNING_STEP",
                    "success": True,
                    "execution_time_ms": duration_ms,
                    "prompt_length": len(executor_prompt),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "estimated_cost": cost,
                    "model": model_manager.current_model_name(),
                    "step": step + 1,
                    "thought": thought,
                    "tool_name": tool_name,
                    "tool_input": tool_input
                }
                if os.getenv("LOG_FULL_PROMPTS", "false").lower() == "true":
                    log_extra["prompt"] = executor_prompt
                    log_extra["response_text"] = raw_response.text

                try:
                    logger_ai.info(f"AI Executor completed step {step + 1} with tool '{tool_name}'", extra=log_extra)
                except Exception:
                    pass

            except Exception as e:
                duration_ms = (time.perf_counter() - start_time) * 1000
                print(f"[Phase 8] Executor error: {e}")
                try:
                    logger_ai.error(
                        f"Executor call failed: Unexpected error - {str(e)}",
                        exc_info=True,
                        extra={
                            "category": "ai",
                            "operation_type": "PLANNER",
                            "intent": "PLANNING_STEP",
                            "success": False,
                            "execution_time_ms": duration_ms,
                            "error": str(e),
                            "model": model_manager.current_model_name(),
                            "step": step + 1
                        }
                    )
                except Exception:
                    pass
                response_payload["reply"] = "I encountered an internal planning error. Please try again."
                break

            tool_name  = plan.get("tool", "final_response")
            tool_input = plan.get("tool_input")
            thought    = plan.get("thought", "")

            print(f"[Step {step + 1}] Thought     : {thought}")
            print(f"[Step {step + 1}] Tool        : {tool_name}")
            print(f"[Step {step + 1}] Tool Input  : {tool_input or '—'}")

        # ── Route to the selected tool ────────────────────────────────────────
        if tool_name in UTILITY_TOOL_INTENTS:
            response_payload["intent"] = UTILITY_TOOL_INTENTS[tool_name]

        tool_result: dict | str | None = None

        if tool_name == "final_response":
            response_payload["reply"] = tool_input or "I couldn't determine a response."
            break

        elif tool_name == "switch_database":
            match = _SWITCH_DB_RULE.match(user_message)
            db_name = match.group("db_name") if match else (tool_input or "").strip()
            if db_name:
                meta = get_metadata()
                dbs = meta.get("databases", [])
                if db_name in dbs:
                    from state.metadata_store import sync_database_context
                    from state.session_store import update_session
                    sync_database_context(db_name)
                    if session_id:
                        update_session(session_id, selected_database=db_name)
                    response_payload["reply"] = f"Switched active database to {db_name}."
                    response_payload["database"] = db_name
                    response_payload["intent"] = "DATABASE_SWITCH"
                else:
                    db_list = ", ".join(dbs) if dbs else "None"
                    response_payload["reply"] = f"Database '{db_name}' does not exist. Available databases: {db_list}"
            else:
                response_payload["reply"] = "Could not parse database name."
            break

        elif tool_name == "execute_sql":
            instruction = tool_input or user_message
            context = build_execution_context(tool_name, instruction, user_intent=user_intent)
            tool_result = execute_sql(
                instruction=instruction,
                target_db=target_db,
                session=session,
                session_id=session_id,
                history=history,
                execution_context=context,
                planning_doc=planning_doc,
                pipeline_hint=pipeline_hint,
            )

        elif tool_name == "get_schema_info":
            tool_result = get_schema_info(target_db=target_db, session=session, message=tool_input or user_message)

        elif tool_name == "summarize_results":
            if last_execution_result and last_execution_result.get("execution"):
                tool_result = summarize_results(last_execution_result["execution"])
            else:
                print("[Phase 8.4] summarize_results called without data. Rewriting to execute_sql")
                tool_name = "execute_sql"
                context = build_execution_context(tool_name, user_message, user_intent=user_intent)
                tool_result = execute_sql(
                    instruction=user_message,
                    target_db=target_db,
                    session=session,
                    session_id=session_id,
                    history=history,
                    execution_context=context,
                    planning_doc=planning_doc,
                )

        elif tool_name == "generate_report":
            if last_execution_result and last_execution_result.get("execution"):
                tool_result = generate_report(last_execution_result["execution"])
            else:
                print("[Phase 8.4] generate_report called without data. Rewriting to execute_sql")
                tool_name = "execute_sql"
                context = build_execution_context(tool_name, user_message, user_intent=user_intent)
                tool_result = execute_sql(
                    instruction=user_message,
                    target_db=target_db,
                    session=session,
                    session_id=session_id,
                    history=history,
                    execution_context=context,
                    planning_doc=planning_doc,
                )

        elif tool_name == "create_database":
            tool_result = create_database(
                instruction=tool_input or user_message,
                target_db=target_db,
                session=session,
                session_id=session_id,
                history=history,
                user_intent=user_intent,
            )

        elif tool_name == "drop_database":
            tool_result = drop_database(
                instruction=tool_input or user_message,
                target_db=target_db,
                session=session,
                session_id=session_id,
                history=history,
                user_intent=user_intent,
            )

        elif tool_name == "create_table":
            tool_result = create_table(
                instruction=tool_input or user_message,
                target_db=target_db,
                session=session,
                session_id=session_id,
                history=history,
                user_intent=user_intent,
            )

        elif tool_name == "drop_table":
            tool_result = drop_table(
                instruction=tool_input or user_message,
                target_db=target_db,
                session=session,
                session_id=session_id,
                history=history,
                user_intent=user_intent,
            )

        elif tool_name == "alter_table":
            tool_result = alter_table(
                instruction=tool_input or user_message,
                target_db=target_db,
                session=session,
                session_id=session_id,
                history=history,
                user_intent=user_intent,
            )

        elif tool_name == "rename_table":
            tool_result = rename_table(
                instruction=tool_input or user_message,
                target_db=target_db,
                session=session,
                session_id=session_id,
                history=history,
                user_intent=user_intent,
            )

        elif tool_name == "list_databases":
            tool_result = list_databases()

        elif tool_name == "list_tables":
            tool_result = list_tables(target_db=target_db, session=session)

        elif tool_name == "find_table_database":
            from agent.tools import find_table_database
            tool_result = find_table_database(message=user_message)

        else:
            print(f"[Phase 8] Unknown tool '{tool_name}' — treating as final_response.")
            response_payload["reply"] = tool_input or "I couldn't determine a response."
            break

        # ── Record trace summary (never raw rows) ─────────────────────────────
        trace_entry = _trace_summary(tool_name, tool_input, tool_result)
        execution_trace.append(trace_entry)
        print(f"[Step {step + 1}] Trace      : {trace_entry}")

        # ── Populate response_payload from dict tool results ─────────────────
        if isinstance(tool_result, dict):
            last_execution_result = tool_result  # may be needed by formatting tools

            # ── Check tool results: classify by intent / tool type ──────────────────
            intent = tool_result.get("intent", "UNKNOWN")

            # ── Phase 8.9: Unified Clarification Layer ────────────────────────
            if intent == "NEEDS_CLARIFICATION":
                response_payload["intent"] = "NEEDS_CLARIFICATION"
                response_payload["question"] = tool_result.get("question")
                response_payload["reply"] = tool_result.get("question") or "Could you clarify your request?"
                if "clarification_data" in tool_result:
                    response_payload["clarification_data"] = tool_result["clarification_data"]
                if "pending_operation" in tool_result:
                    response_payload["pending_operation"] = tool_result["pending_operation"]
                print("[Phase 8.9] Loop terminated: NEEDS_CLARIFICATION")
                break
            
            # Non-SQL tools (list_databases, list_tables, get_schema_info)
            # They write their results to response_payload["reply"] and break the loop immediately.
            if tool_name == "list_databases":
                dbs = tool_result.get("databases", [])
                response_payload["reply"] = (
                    f"Available databases: {', '.join(dbs)}." if dbs
                    else "No databases found."
                )
                print(f"[Phase 8.3] Utility tool '{tool_name}' completed. Returning response directly.")
                break
            elif tool_name == "list_tables":
                tbls = tool_result.get("tables", [])
                db   = tool_result.get("active_db", "selected database")
                response_payload["reply"] = (
                    f"Tables in {db}: {', '.join(tbls)}." if tbls
                    else f"No tables found in {db}."
                )
                print(f"[Phase 8.3] Utility tool '{tool_name}' completed. Returning response directly.")
                break
            elif tool_name == "get_schema_info":
                schema = tool_result.get("schema", {})
                table_schemas = tool_result.get("table_schemas", {})
                active_db = tool_result.get("active_db", "")
                if schema:
                    if len(schema) == 1:
                        # Single table detail formatting
                        tbl = list(schema.keys())[0]
                        cols = schema[tbl]
                        lines = [f"Schema for table **{tbl}**:"]
                        for col in cols:
                            col_type = table_schemas.get(tbl, {}).get(col, "unknown")
                            lines.append(f"* {col} ({col_type})")
                        response_payload["reply"] = "\n".join(lines)
                    else:
                        lines = [f"Database schema for **{active_db}**:"]
                        for tbl, cols in schema.items():
                            col_details = []
                            for col in cols:
                                col_type = table_schemas.get(tbl, {}).get(col)
                                if col_type:
                                    col_details.append(f"{col} ({col_type})")
                                else:
                                    col_details.append(col)
                            lines.append(f"- **{tbl}**: {', '.join(col_details)}")
                        response_payload["reply"] = "\n".join(lines)
                else:
                    response_payload["reply"] = "No schema information available."
                print(f"[Phase 8.3] Utility tool '{tool_name}' completed. Returning response directly.")
                break

            elif tool_name == "find_table_database":
                table = tool_result.get("table_name")
                dbs = tool_result.get("databases", [])
                if not dbs:
                    response_payload["reply"] = f"Table '{table}' was not found."
                elif len(dbs) == 1:
                    response_payload["reply"] = f"Table '{table}' exists in database: {dbs[0]}."
                else:
                    db_list = "\n".join(f"* {db}" for db in dbs)
                    response_payload["reply"] = f"Table '{table}' exists in:\n\n{db_list}"
                print(f"[Phase 8.3] Utility tool '{tool_name}' completed. Returning response directly.")
                break

            # SQL tools (execute_sql, create_database, create_table)
            else:
                response_payload["intent"]  = intent
                response_payload["sql"]     = tool_result.get("sql")
                response_payload["question"] = tool_result.get("question")
                response_payload["valid"]   = tool_result.get("valid", True)
                response_payload["risk_level"] = tool_result.get("risk_level", "SAFE")
                response_payload["requires_confirmation"] = tool_result.get("requires_confirmation", False)
                response_payload["blocked_reason"] = tool_result.get("blocked_reason")
                if "clarification_data" in tool_result:
                    response_payload["clarification_data"] = tool_result["clarification_data"]
                if "pending_operation" in tool_result:
                    response_payload["pending_operation"] = tool_result["pending_operation"]

                exec_res  = tool_result.get("execution")
                target_db_resolved = tool_result.get("target_db") or target_db
                response_payload["database"] = target_db_resolved

                if exec_res:
                    response_payload["execution"] = exec_res
                    # Mirror refresh flags from chat.py
                    op = exec_res.get("operation", "")
                    if exec_res.get("success"):
                        if op == "CREATE DATABASE":
                            response_payload["refresh_databases"] = True
                        elif op in {"CREATE", "ALTER", "DROP"}:
                            response_payload["refresh_tables"] = True
                            response_payload["refresh_schema"] = True

                    # Populate reply for direct executions when Planner is bypassed
                    response_payload["reply"] = _build_success_reply(tool_name, tool_result)

                # ── Loop termination: Successful terminal operations ──────────────
                if exec_res and exec_res.get("success"):
                    op_upper = op.upper()
                    is_terminal_op = (
                        op_upper.startswith("CREATE DATABASE")
                        or op_upper.startswith("DROP DATABASE")
                        or op_upper.startswith("CREATE TABLE")
                        or op_upper.startswith("DROP TABLE")
                        or op_upper.startswith("ALTER TABLE")
                        or op_upper.startswith("RENAME TABLE")
                        or op_upper.startswith("RENAME")
                        or op_upper.startswith("ALTER")
                        or op_upper.startswith("SELECT")
                        or op_upper.startswith("QUERY")
                    )
                    if is_terminal_op:
                        print(f"[Phase 8.1] Terminal successful SQL operation '{op}' detected, breaking loop immediately.")
                        break

                # ── Loop termination: RATE_LIMITED ────────────────────────────────
                if intent == "RATE_LIMITED":
                    msg_lower = user_message.lower()
                    if "database" in msg_lower or "db" in msg_lower:
                        response_payload["reply"] = "I can create the database if you provide a simple name. Example: Create database company_db"
                    elif "report" in msg_lower or "summary" in msg_lower:
                        response_payload["reply"] = "I can generate the report once the AI service becomes available. Meanwhile you can still run SQL queries normally."
                    else:
                        response_payload["reply"] = "The AI generator is temporarily busy. Meanwhile you can still run SQL queries normally."
                    print("[Phase 8] Loop terminated: RATE_LIMITED")
                    break

                # ── Loop termination: NEEDS_CLARIFICATION ─────────────────────────
                if intent == "NEEDS_CLARIFICATION":
                    response_payload["reply"] = tool_result.get("question") or "Could you clarify your request?"
                    print("[Phase 8] Loop terminated: NEEDS_CLARIFICATION")
                    break

                # ── Loop termination: UNKNOWN intent ──────────────────────────────
                if intent == "UNKNOWN":
                    response_payload["reply"] = "I'm sorry, I can only assist with valid database operations."
                    print("[Phase 8] Loop terminated: UNKNOWN intent")
                    break

                # ── Loop termination: blocked / invalid ───────────────────────────
                if not tool_result.get("valid", True):
                    reason = tool_result.get("blocked_reason") or tool_result.get("failure_reason", "")
                    prefix = "🚫" if tool_result.get("risk_level") == "BLOCKED" else "❌"
                    response_payload["reply"] = f"{prefix} {reason}"
                    print(f"[Phase 8] Loop terminated: validation failure ({reason})")
                    break

                # ── Loop termination: HIGH_RISK / CRITICAL_RISK confirmation ──────
                if tool_result.get("requires_confirmation"):
                    rl = tool_result.get("risk_level", "")
                    if rl == "CRITICAL_RISK":
                        response_payload["reply"] = (
                            "🚨 CRITICAL OPERATION — This action cannot be undone. "
                            "Type CONFIRM to proceed."
                        )
                    else:
                        response_payload["reply"] = (
                            "⚠️ HIGH RISK — This operation modifies existing data or schema. "
                            "Please confirm to proceed."
                        )
                    print(f"[Phase 8] Loop terminated: {rl} confirmation required")
                    break

        # ── Populate response_payload from string tool results (formatters) ──
        elif isinstance(tool_result, str):
            response_payload["formatted_text"] = tool_result
            response_payload["reply"] = tool_result
            print(f"[Phase 8] Loop terminated: {tool_name} (terminal formatting tool)")
            break

        # ── Terminal tool check ───────────────────────────────────────────────
        if tool_name in _TERMINAL_TOOLS:
            print(f"[Phase 8] Loop terminated: terminal tool '{tool_name}'")
            break

        # ── Phase 8.1 OPT-3: If keyword dispatch was used at step 0, break immediately ──
        if first_tool_override and step == 0:
            print(f"[Phase 8.1] Keyword dispatch tool '{tool_name}' completed, breaking loop immediately.")
            break

    else:
        # Loop exhausted without a break
        print("[Phase 8] Max tool calls reached.")
        if not response_payload["reply"]:
            response_payload["reply"] = "I reached the maximum number of steps. Please refine your request."

    try:
        print(f"\n[Phase 8] Final Reply: {response_payload['reply'][:120]}")
    except UnicodeEncodeError:
        safe_reply = response_payload['reply'][:120].encode('ascii', errors='replace').decode('ascii')
        print(f"\n[Phase 8] Final Reply (ASCII-safe): {safe_reply}")
    print("====================================\n")

    return response_payload
