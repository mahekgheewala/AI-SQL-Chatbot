"""
Phase 8 — Agent Coordinator
============================
Lightweight orchestration loop that sits above the existing backend pipeline.

Responsibilities (and ONLY these):
  1. Understand the user request.
  2. Decide which tool should handle it.
  3. Execute the selected tool.
  4. Decide whether another tool is needed.
  5. Return the final ChatResponse payload.

The coordinator NEVER:
  - Opens a PostgreSQL connection.
  - Generates SQL.
  - Runs validation.
  - Updates session memory directly.
  - Refreshes metadata or routing summaries directly.

All real work happens inside the tool functions in tools.py, which themselves
delegate to the existing Phase 3 → Phase 7.1 pipeline.

Phase 8.1 additions (optimization only, no architecture changes):
  OPT-1: Retry wrapper on the planner Gemini call (rate-limit resilience).
  OPT-2: Immediate loop exit after successful SQL tool execution — no second
          planner pass needed when the outcome is already determined.
  OPT-3: Deterministic keyword dispatch for obvious utility commands — skips
          Planner AI entirely for requests like "show databases", "list tables".
"""

import json
import re
import os
from typing import Optional

import google.generativeai as genai

from agent.prompts import AGENT_SYSTEM_PROMPT
from agent.gemini_retry import call_with_retry          # Phase 8.1 OPT-1
from agent import gemini_metrics                        # Phase 8.5 instrumentation
from agent.tools import (
    create_database,
    create_table,
    execute_sql,
    generate_report,
    get_schema_info,
    list_databases,
    list_tables,
    summarize_results,
)
from state.metadata_store import get_metadata

# ─── Guarantee genai.configure() has been called ─────────────────────────────
# gemini_service calls genai.configure(api_key=...) at module load time.
# Importing it here ensures the API key is registered before we create any
# GenerativeModel instance in this module.
import ai.gemini_service  # noqa: F401 — side-effect import only

# ─── Planner model (dedicated instance — never bleeds SQL-gen instructions) ──
_planner_model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=AGENT_SYSTEM_PROMPT,
    generation_config=genai.types.GenerationConfig(
        temperature=0.0,
        response_mime_type="application/json",
    ),
)

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

_UTILITY_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"^\s*(show|list|display)\s+(all\s+)?(database|db)s?\b",
        re.IGNORECASE,
    ), "list_databases"),
    (re.compile(
        r"^\s*(show|list|display)\s+(all\s+)?tables?\b",
        re.IGNORECASE,
    ), "list_tables"),
]

_SCHEMA_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"\b(describe\s+(the\s+)?(schema|table|column)s?\b"
        r"|list\s+(all\s+)?columns\b"
        r"|show\s+(all\s+)?columns\b"
        r"|table\s+structure\b"
        r"|schema\s+of\s+table\b"
        r"|schema\s+of\s+\w+(\s+(table|tbl))?\b"
        r"|columns\s+(of|in|exist\s+in)\s+table\b"
        r"|columns\s+(of|in|exist\s+in)\s+\w+(\s+(table|tbl))?\b"
        r"|explain\s+table\b"
        r"|explain\s+(the\s+)?(schema|structure|columns)\b)\b"
        r"|^\s*(show\s+columns|show\s+schema)\b",
        re.IGNORECASE,
    ), "get_schema_info"),
]

_RAW_SQL_RULES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE)\b",
            re.IGNORECASE,
        ),
        "execute_sql",
    ),
]

_DDL_RULES: list[tuple[re.Pattern, str]] = [
    # create database
    (re.compile(r"^\s*(create|make|build)\s+(database|db)\b", re.IGNORECASE), "create_database"),
    # create table
    (re.compile(r"^\s*(create|make|build)\s+table\b", re.IGNORECASE), "create_table"),
    # drop database / table
    (re.compile(r"^\s*(drop)\s+(database|db|table)\b", re.IGNORECASE), "execute_sql"),
    # alter / rename table
    (re.compile(r"^\s*(alter|rename|modify)\s+table\b", re.IGNORECASE), "execute_sql"),
    # add/drop/rename column (NL or explicit)
    (re.compile(r"\b(add|drop|rename|delete|remove|new|modify)\s+(\w+\s+)?column\b", re.IGNORECASE), "execute_sql"),
]

_DML_RULES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"^\s*(insert|update|delete|truncate)\b",
            re.IGNORECASE,
        ),
        "execute_sql",
    ),
]

_QUERY_RULES: list[tuple[re.Pattern, str]] = [
    # NL query select equivalents (Raw SQL SELECT is handled by _RAW_SQL_RULES)
    (re.compile(r"^\s*(show|list|display|find|count|search|get)\b", re.IGNORECASE), "execute_sql"),
    (re.compile(r"^\s*how\s+many\b", re.IGNORECASE), "execute_sql"),
]


def _print_dispatch_trace(message: str, rule_group: str, matched_regex: str, tool_name: str) -> None:
    """Print a structured dispatch trace block to stdout. No side effects."""
    print("====================================")
    print("DISPATCH TRACE")
    print("====================================")
    print(f"Message:\n  {message}")
    print(f"\nMatched Rule Group:\n  {rule_group}")
    print(f"\nMatched Regex:\n  {matched_regex}")
    print(f"\nSelected Tool:\n  {tool_name}")
    print("====================================\n")


def _print_dispatch_no_match(message: str) -> None:
    """Print a structured no-match / planner-fallback trace block."""
    print("====================================")
    print("DISPATCH TRACE")
    print("====================================")
    print(f"Message:\n  {message}")
    print("\nMatched Rule:\n  NONE")
    print("\nPlanner Fallback:\n  TRUE")
    print("====================================\n")


def _deterministic_dispatch(message: str) -> Optional[str]:
    """
    Phase 8.1 OPT-3 + Phase 8.4 + Phase 8.8 + Phase 8.10: keyword pre-filter.
    
    Order of evaluation:
    0. SWITCH_DB_RULE  (bypasses formatting guard)
    1. UTILITY_RULES   (bypasses formatting guard)
    2. SCHEMA_RULES    (bypasses formatting guard)
    3. RAW_SQL_RULES   (bypasses formatting guard)
    4. DDL_RULES       (bypasses formatting guard)
    5. DML_RULES       (bypasses formatting guard)
    6. Formatting Guard (suppresses query rules if formatting keyword is matched)
    7. QUERY_RULES     (subject to formatting guard)
    """
    # 0. Switch database rule
    if _SWITCH_DB_RULE.match(message):
        _print_dispatch_trace(
            message,
            "SWITCH_DB_RULES",
            _SWITCH_DB_RULE.pattern,
            "switch_database",
        )
        return "switch_database"

    # 1. Utility rules
    for pattern, tool_name in _UTILITY_RULES:
        if pattern.search(message):
            _print_dispatch_trace(message, "UTILITY_RULES", pattern.pattern, tool_name)
            return tool_name

    # 2. Schema rules
    for pattern, tool_name in _SCHEMA_RULES:
        if pattern.search(message):
            _print_dispatch_trace(message, "SCHEMA_RULES", pattern.pattern, tool_name)
            return tool_name

    # 3. Raw SQL rules
    for pattern, tool_name in _RAW_SQL_RULES:
        if pattern.search(message):
            _print_dispatch_trace(message, "RAW_SQL_RULES", pattern.pattern, tool_name)
            return tool_name

    # 4. DDL rules
    for pattern, tool_name in _DDL_RULES:
        if pattern.search(message):
            _print_dispatch_trace(message, "DDL_RULES", pattern.pattern, tool_name)
            return tool_name

    # 5. DML rules
    for pattern, tool_name in _DML_RULES:
        if pattern.search(message):
            _print_dispatch_trace(message, "DML_RULES", pattern.pattern, tool_name)
            return tool_name

    # 6. Formatting guard — suppresses QUERY_RULES so the Planner can chain SQL+format
    if _FORMATTING_KEYWORDS.search(message):
        _print_dispatch_no_match(message)
        print("[Phase 8.10] Formatting keyword detected — bypassing deterministic dispatch.")
        return None

    # 7. Query rules
    for pattern, tool_name in _QUERY_RULES:
        if pattern.search(message):
            _print_dispatch_trace(message, "QUERY_RULES", pattern.pattern, tool_name)
            return tool_name

    # No rule matched
    _print_dispatch_no_match(message)
    return None


def _format_schema_context_for_planner(router_db: Optional[str], session: dict) -> str:
    """
    Builds a compact schema context string for the planner prompt.
    Mirrors context_builder.py but produces a shorter token footprint for routing decisions.
    """
    meta     = get_metadata()
    active   = router_db or meta.get("selected_db") or session.get("selected_database") or "None"
    dbs      = meta.get("databases", [])
    routing  = meta.get("routing_summaries", {})

    lines = [
        f"ACTIVE DATABASE: {active}",
        f"AVAILABLE DATABASES: {', '.join(dbs) if dbs else 'None'}",
    ]

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

    # Handle CREATE DATABASE / CREATE TABLE within execute_sql or their wrapper tools
    if tool_name == "create_database" or op == "CREATE DATABASE":
        sql = tool_result.get("sql", "") or ""
        parts = sql.strip().split()
        db_name = parts[2].strip('";') if len(parts) >= 3 else db
        return f"✅ Database **{db_name}** has been successfully created."

    if tool_name == "create_table" or op == "CREATE TABLE":
        sql = tool_result.get("sql", "") or ""
        from db.executor import extract_table_name
        tbl_name = extract_table_name(sql) or "the table"
        return f"✅ Table **{tbl_name}** has been successfully created."

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
) -> dict:
    """
    Main agent orchestration loop.

    Parameters:
        user_message — current user input
        router_db    — database resolved by Phase 7 Router AI (may be None)
        session      — Phase 6 session memory dict
        session_id   — UUID from frontend (for session updates)
        history      — last N conversation turns
        target_db    — 4-level fallback chain result from chat.py

    Returns:
        A dict with all fields needed to populate a ChatResponse:
            reply, intent, database, sql, question,
            valid, risk_level, requires_confirmation, blocked_reason,
            execution, refresh_databases, refresh_tables, refresh_schema,
            formatted_text (str | None) — populated by formatting tools
    """

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
    print("PHASE 8 AGENT COORDINATOR")
    print("====================================")
    print(f"User Message: {user_message}")
    print(f"Router DB   : {router_db or 'None'}")
    print(f"Target DB   : {target_db or 'None'}")

    # ── Phase 8.1 OPT-3: Deterministic keyword dispatch ──────────────────────
    # Check for obvious utility commands before invoking Planner AI.
    keyword_tool = _deterministic_dispatch(user_message)
    first_tool_override: Optional[str] = keyword_tool  # used in step 0 if set
    if keyword_tool:
        gemini_metrics.record_bypass("planner")
        print(f"[Phase 8.4] Planner bypass recorded — keyword dispatched to '{keyword_tool}'.")

    for step in range(_MAX_TOOL_CALLS):

        # ── Determine which tool to run this step ─────────────────────────────
        if first_tool_override and step == 0:
            # OPT-3: skip Planner AI, dispatch directly
            tool_name   = first_tool_override
            tool_input  = None
            thought     = f"[Keyword dispatch] → {tool_name}"
            print(f"\n[Step {step + 1}] Keyword Dispatch: {tool_name}")
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

            planner_prompt = (
                f"{schema_context}\n\n"
                f"{history_block}"
                f"{trace_block}"
                f"USER REQUEST: {user_message}"
            )

            # Determine Planner Trigger Reason
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

            print(f"Planner Trigger Reason: {planner_trigger_reason}")

            # ── Phase 8.1 OPT-1: Call planner with rate-limit retry ───────────
            print(f"\n[Step {step + 1}] Calling planner model…")
            try:
                # Phase 8.5: record this Gemini call in the metrics tracker
                gemini_metrics.record_call("planner")
                raw_response, success, rate_limit_msg = call_with_retry(
                    fn=lambda: _planner_model.generate_content(planner_prompt),
                    label="Planner",
                )
                if not success:
                    print(f"[Phase 8] Planner rate limit exhausted: {rate_limit_msg}")
                    response_payload["reply"] = rate_limit_msg
                    break

                if raw_response and hasattr(raw_response, "usage_metadata") and raw_response.usage_metadata:
                    gemini_metrics.record_tokens(
                        "planner",
                        raw_response.usage_metadata.prompt_token_count,
                        raw_response.usage_metadata.candidates_token_count
                    )

                plan = json.loads(raw_response.text)
            except Exception as e:
                print(f"[Phase 8] Planner error: {e}")
                response_payload["reply"] = "I encountered an internal planning error. Please try again."
                break

            tool_name  = plan.get("tool", "final_response")
            tool_input = plan.get("tool_input")
            thought    = plan.get("thought", "")

            print(f"[Step {step + 1}] Thought     : {thought}")
            print(f"[Step {step + 1}] Tool        : {tool_name}")
            print(f"[Step {step + 1}] Tool Input  : {tool_input or '—'}")

        # ── Route to the selected tool ────────────────────────────────────────
        tool_result: dict | str | None = None

        if tool_name == "final_response":
            response_payload["reply"] = tool_input or "I couldn't determine a response."
            break

        elif tool_name == "switch_database":
            match = _SWITCH_DB_RULE.match(user_message)
            if match:
                db_name = match.group("db_name")
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
                else:
                    db_list = ", ".join(dbs) if dbs else "None"
                    response_payload["reply"] = f"Database '{db_name}' does not exist. Available databases: {db_list}"
            else:
                response_payload["reply"] = "Could not parse database name."
            break

        elif tool_name == "execute_sql":
            instruction = tool_input or user_message
            tool_result = execute_sql(
                instruction=instruction,
                target_db=target_db,
                session=session,
                session_id=session_id,
                history=history,
            )

        elif tool_name == "get_schema_info":
            tool_result = get_schema_info(target_db=target_db, session=session, message=user_message)

        elif tool_name == "summarize_results":
            if last_execution_result and last_execution_result.get("execution"):
                tool_result = summarize_results(last_execution_result["execution"])
            else:
                print("[Phase 8.4] summarize_results called without data. Rewriting to execute_sql")
                tool_name = "execute_sql"
                tool_result = execute_sql(
                    instruction=user_message,
                    target_db=target_db,
                    session=session,
                    session_id=session_id,
                    history=history,
                )

        elif tool_name == "generate_report":
            if last_execution_result and last_execution_result.get("execution"):
                tool_result = generate_report(last_execution_result["execution"])
            else:
                print("[Phase 8.4] generate_report called without data. Rewriting to execute_sql")
                tool_name = "execute_sql"
                tool_result = execute_sql(
                    instruction=user_message,
                    target_db=target_db,
                    session=session,
                    session_id=session_id,
                    history=history,
                )

        elif tool_name == "create_database":
            tool_result = create_database(
                instruction=tool_input or user_message,
                target_db=target_db,
                session=session,
                session_id=session_id,
                history=history,
            )

        elif tool_name == "create_table":
            tool_result = create_table(
                instruction=tool_input or user_message,
                target_db=target_db,
                session=session,
                session_id=session_id,
                history=history,
            )

        elif tool_name == "list_databases":
            tool_result = list_databases()

        elif tool_name == "list_tables":
            tool_result = list_tables(target_db=target_db, session=session)

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

                # ── Loop termination: RATE_LIMITED ────────────────────────────────
                if intent == "RATE_LIMITED":
                    response_payload["reply"] = tool_result.get("question") or (
                        "Gemini API rate limit reached. Please wait a few seconds and try again."
                    )
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

    print(f"\n[Phase 8] Final Reply: {response_payload['reply'][:120]}")
    print("====================================\n")

    return response_payload
