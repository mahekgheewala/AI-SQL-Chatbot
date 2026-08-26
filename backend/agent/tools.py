"""
Phase 8 — Tool Implementations
================================
Eight thin wrapper functions that delegate all real work to the existing
backend pipeline (Phase 3 → Phase 7.1).

Strict rules enforced here:
  * No tool directly opens a PostgreSQL connection.
  * No tool runs its own SQL generation.
  * No tool implements its own validation.
  * No tool duplicates session memory or metadata refresh logic.
  * summarize_results and generate_report are pure formatting tools —
    they receive data and return text. They never touch the database.
"""


import json
import os
import re
import time
from typing import Optional
from dataclasses import dataclass

import google.generativeai as genai
from utils.logging_config import logger_ai
from utils.config_loader import init_env, get_superdb_name
from ai import model_manager
from ai.context_builder import build_schema_context

init_env()

from agent.gemini_retry import call_with_retry          # Phase 4.5: retry for formatters
from agent.prompts import SUMMARIZE_PROMPT, REPORT_PROMPT
from db.executor import execute_sql as _execute_sql, requires_superdb, extract_table_name
from state.metadata_store import (
    get_metadata,
    refresh_routing_summaries,
)
from state.session_store import update_session
from validation.sql_validator import validate as validate_sql
from agent import gemini_metrics  # Phase 8.5: call instrumentation
from agent.ddl_parser import parse_simple_ddl

# ─── Phase 4.5 Finding 1: Raw SQL intent mapper ───────────────────────────────
# Maps the leading SQL keyword of a raw statement to the authoritative intent
# string expected by safety_checker.py.  All entries must exist in one of
# SAFE_INTENTS / HIGH_RISK_INTENTS / CRITICAL_RISK_INTENTS.
_RAW_SQL_PATTERN = re.compile(
    r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE)\b",
    re.IGNORECASE,
)


def _raw_sql_intent(sql: str) -> Optional[str]:
    """
    Return the correct intent string for a raw SQL statement, or None if the
    statement does not start with a recognised SQL keyword.

    Resolution rules (must match safety_checker.py sets exactly):
      SELECT               -> QUERY
      INSERT               -> INSERT
      UPDATE               -> UPDATE
      DELETE               -> DELETE
      CREATE DATABASE ...  -> CREATE_DATABASE
      CREATE TABLE ...     -> CREATE_TABLE
      CREATE (other)       -> CREATE_TABLE   (SAFE fallback)
      ALTER TABLE ADD ...  -> ADD_COLUMN
      ALTER TABLE DROP ... -> DROP_COLUMN
      ALTER (other)        -> ADD_COLUMN     (SAFE fallback)
      DROP DATABASE ...    -> DROP_DATABASE
      DROP TABLE ...       -> DROP_TABLE
      DROP (other)         -> DROP_TABLE     (conservative)
      TRUNCATE             -> TRUNCATE
    """
    m = _RAW_SQL_PATTERN.match(sql)
    if not m:
        return None
    kw = m.group(1).upper()
    upper_sql = sql.strip().upper()

    if kw == "SELECT":
        return "QUERY"
    if kw == "INSERT":
        return "INSERT"
    if kw == "UPDATE":
        return "UPDATE"
    if kw == "DELETE":
        return "DELETE"
    if kw == "TRUNCATE":
        return "TRUNCATE"
    if kw == "CREATE":
        if re.match(r"^\s*CREATE\s+DATABASE\b", upper_sql):
            return "CREATE_DATABASE"
        if re.match(r"^\s*CREATE\s+TABLE\b", upper_sql):
            return "CREATE_TABLE"
        return "CREATE_TABLE"   # safe fallback
    if kw == "ALTER":
        if re.search(r"\bRENAME\s+TO\b", upper_sql):
            return "RENAME_TABLE"
        if re.search(r"\bRENAME\b", upper_sql):
            return "RENAME_COLUMN"
        if re.search(r"\bADD\b", upper_sql):
            return "ADD_COLUMN"
        if re.search(r"\bDROP\b", upper_sql):
            return "DROP_COLUMN"
        if re.search(r"\bALTER\s+COLUMN\b|\bTYPE\b|\bSET\s+DATA\s+TYPE\b", upper_sql):
            return "MODIFY_COLUMN"
        return "ADD_COLUMN"     # safe fallback
    if kw == "DROP":
        if re.match(r"^\s*DROP\s+DATABASE\b", upper_sql):
            return "DROP_DATABASE"
        if re.match(r"^\s*DROP\s+TABLE\b", upper_sql):
            return "DROP_TABLE"
        return "DROP_TABLE"     # conservative fallback
    return None


@dataclass
class ExecutionContext:
    operation_type: str
    resource_scope: str  # "SERVER", "DATABASE", "TABLE", or "NONE"
    requires_database_context: bool
    requires_superdb: bool
    requires_confirmation: bool = False
    allow_auto_routing: bool = True
    supports_clarification: bool = True


def build_execution_context(tool_name: str, instruction: str, user_intent: Optional[str] = None) -> ExecutionContext:
    """
    Constructs an ExecutionContext cleanly based on tool_name, instruction content, and user_intent,
    raising an explicit ValueError if it cannot be determined.
    """
    from agent.sql_detector import is_raw_sql

    instruction_clean = instruction.strip()
    is_sql, _ = is_raw_sql(instruction_clean)

    # 0. If user_intent is explicitly passed from classification layer, use it to dictate attributes
    if user_intent and user_intent != "UNKNOWN":
        if user_intent == "CREATE_DATABASE":
            return ExecutionContext(
                operation_type="CREATE_DATABASE",
                resource_scope="SERVER",
                requires_database_context=False,
                requires_superdb=True,
                requires_confirmation=False,
                allow_auto_routing=False,
                supports_clarification=True
            )
        if user_intent == "DROP_DATABASE":
            return ExecutionContext(
                operation_type="DROP_DATABASE",
                resource_scope="SERVER",
                requires_database_context=False,
                requires_superdb=True,
                requires_confirmation=True,
                allow_auto_routing=False,
                supports_clarification=True
            )
        if user_intent == "CREATE_TABLE":
            return ExecutionContext(
                operation_type="CREATE_TABLE",
                resource_scope="DATABASE",
                requires_database_context=True,
                requires_superdb=False,
                requires_confirmation=False,
                allow_auto_routing=True,
                supports_clarification=True
            )
        if user_intent == "DROP_TABLE":
            return ExecutionContext(
                operation_type="DROP_TABLE",
                resource_scope="DATABASE",
                requires_database_context=True,
                requires_superdb=False,
                requires_confirmation=True,
                allow_auto_routing=True,
                supports_clarification=True
            )
        if user_intent in {"ALTER_TABLE", "RENAME_TABLE"}:
            return ExecutionContext(
                operation_type=user_intent,
                resource_scope="DATABASE",
                requires_database_context=True,
                requires_superdb=False,
                requires_confirmation=False,
                allow_auto_routing=True,
                supports_clarification=True
            )
        if user_intent == "LIST_DATABASES":
            return ExecutionContext(
                operation_type="LIST_DATABASES",
                resource_scope="SERVER",
                requires_database_context=False,
                requires_superdb=False,
                requires_confirmation=False,
                allow_auto_routing=False,
                supports_clarification=False
            )
        if user_intent == "LIST_TABLES":
            return ExecutionContext(
                operation_type="LIST_TABLES",
                resource_scope="DATABASE",
                requires_database_context=True,
                requires_superdb=False,
                requires_confirmation=False,
                allow_auto_routing=True,
                supports_clarification=True
            )
        if user_intent == "GET_SCHEMA":
            return ExecutionContext(
                operation_type="GET_SCHEMA",
                resource_scope="DATABASE",
                requires_database_context=True,
                requires_superdb=False,
                requires_confirmation=False,
                allow_auto_routing=True,
                supports_clarification=True
            )
        if user_intent in {"QUERY", "INSERT", "UPDATE", "DELETE", "STANDARD_QUERY"}:
            req_confirm = user_intent in {"DELETE", "TRUNCATE"}
            return ExecutionContext(
                operation_type=user_intent,
                resource_scope="DATABASE",
                requires_database_context=True,
                requires_superdb=False,
                requires_confirmation=req_confirm,
                allow_auto_routing=True,
                supports_clarification=True
            )

    # 1. Check if the tool_name maps to database operations
    if tool_name == "create_database":
        return ExecutionContext(
            operation_type="CREATE_DATABASE",
            resource_scope="SERVER",
            requires_database_context=False,
            requires_superdb=True,
            requires_confirmation=False,
            allow_auto_routing=False,
            supports_clarification=True
        )

    # 2. Check for CREATE TABLE
    if tool_name == "create_table":
        return ExecutionContext(
            operation_type="CREATE_TABLE",
            resource_scope="DATABASE",
            requires_database_context=True,
            requires_superdb=False,
            requires_confirmation=False,
            allow_auto_routing=True,
            supports_clarification=True
        )

    # 3. Check for list / schema cache-only tools
    if tool_name == "list_databases":
        return ExecutionContext(
            operation_type="LIST_DATABASES",
            resource_scope="SERVER",
            requires_database_context=False,
            requires_superdb=False,
            requires_confirmation=False,
            allow_auto_routing=False,
            supports_clarification=False
        )
    if tool_name == "list_tables":
        return ExecutionContext(
            operation_type="LIST_TABLES",
            resource_scope="DATABASE",
            requires_database_context=True,
            requires_superdb=False,
            requires_confirmation=False,
            allow_auto_routing=True,
            supports_clarification=True
        )
    if tool_name == "get_schema_info":
        return ExecutionContext(
            operation_type="GET_SCHEMA",
            resource_scope="DATABASE",
            requires_database_context=True,
            requires_superdb=False,
            requires_confirmation=False,
            allow_auto_routing=True,
            supports_clarification=True
        )

    # 4. If it's natural language instruction, check for DB creation/deletion pattern
    if not is_sql:
        instr_lower = instruction_clean.lower()
        is_create_db = bool(re.search(r"\b(create|make|build|generate|add|setup|new)\s+(?:(?:a|an|the|new|another)\s+){0,2}(?:database|db)\b", instr_lower))
        is_drop_db = bool(re.search(r"\b(drop|delete|remove|destroy)\s+(?:(?:a|an|the|old|our)\s+){0,2}(?:database|db)\b", instr_lower))

        if is_drop_db:
            return ExecutionContext(
                operation_type="DROP_DATABASE",
                resource_scope="SERVER",
                requires_database_context=False,
                requires_superdb=True,
                requires_confirmation=True,
                allow_auto_routing=False,
                supports_clarification=True
            )
        if is_create_db:
            return ExecutionContext(
                operation_type="CREATE_DATABASE",
                resource_scope="SERVER",
                requires_database_context=False,
                requires_superdb=True,
                requires_confirmation=False,
                allow_auto_routing=False,
                supports_clarification=True
            )

    # 5. Check for raw SQL intents
    if is_sql:
        raw_intent = _raw_sql_intent(instruction_clean)
        if raw_intent is not None:
            req_confirm = raw_intent in {"DROP_TABLE", "TRUNCATE", "DELETE", "DROP_DATABASE"}
            is_super = (raw_intent in {"CREATE_DATABASE", "DROP_DATABASE"} or requires_superdb(instruction_clean))
            is_server = (raw_intent in {"CREATE_DATABASE", "DROP_DATABASE"})
            return ExecutionContext(
                operation_type=raw_intent,
                resource_scope="SERVER" if is_server else "DATABASE",
                requires_database_context=not is_server,
                requires_superdb=is_super,
                requires_confirmation=req_confirm,
                allow_auto_routing=True,
                supports_clarification=True
            )

    # 6. Default mapping for execute_sql natural-language operations (always database-scoped)
    if tool_name in {"execute_sql", "summarize_results", "generate_report"}:
        return ExecutionContext(
            operation_type="UNKNOWN",
            resource_scope="DATABASE",
            requires_database_context=True,
            requires_superdb=False,
            requires_confirmation=False,
            allow_auto_routing=True,
            supports_clarification=True
        )

    raise ValueError(f"Could not build ExecutionContext for tool '{tool_name}' and instruction '{instruction[:50]}'")






# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_cached_schema(target_db: Optional[str]) -> None:
    """
    Ensure the metadata store contains the schema/tables of target_db.
    If target_db is not cached, fetch its schema and tables dynamically
    and cache them, tracking the database name via cached_schema_db.
    """
    if not target_db:
        return
    
    from state.metadata_store import sync_database_context
    sync_database_context(target_db)


def _build_context(session: dict, target_db: Optional[str], target_table: Optional[str] = None, pipeline_hint: Optional[str] = None) -> str:
    """Build the dynamic schema context exactly as chat.py does."""
    meta = get_metadata()
    ctx = build_schema_context(
        current_db=target_db,
        current_table=target_table,
        available_dbs=meta.get("databases", []),
        raw_schema=meta.get("schema", {}),
        session=session,
    )
    if pipeline_hint == "ANALYTICS_ENGINE":
        ctx += "\n\n[DATA REQUIREMENT HINT: UNAGGREGATED SOURCE DATA REQUIRED]\nProduce a raw SELECT query without GROUP BY or aggregate functions (SUM, AVG, COUNT, MIN, MAX) so downstream pandas analysis can calculate exact figures on unaggregated rows."
    elif pipeline_hint == "VISUALIZATION":
        ctx += "\n\n[DATA REQUIREMENT HINT: VISUALIZATION DATASET REQUIRED]\nProduce a SELECT query suitable for rendering charts."
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1 — execute_sql
# ─────────────────────────────────────────────────────────────────────────────

def execute_sql(
    instruction: str,
    target_db: Optional[str],
    session: dict,
    session_id: Optional[str],
    history: list,
    execution_context: Optional[ExecutionContext] = None,
    planning_doc: Optional[dict] = None,
    pipeline_hint: Optional[str] = None,
) -> dict:
    """
    Thin wrapper around the existing SQL generation + validation + execution
    pipeline (Phases 3 → 5).

    Returns a dict with keys:
        intent, sql, question, execution_database,
        valid, risk_level, requires_confirmation, blocked_reason,
        failure_reason, execution (ExecutionResult dict | None)
    """
    import re
    meta = get_metadata()

    # 1. Resolve/Build ExecutionContext if not provided
    if execution_context is None:
        execution_context = build_execution_context("execute_sql", instruction)

    # 2. Print Execution Context Log Block
    print("\n====================================")
    print("EXECUTION CONTEXT")
    print("====================================")
    print(f"\nOperation:\n{execution_context.operation_type}")
    print(f"\nExecution Scope:\n{execution_context.resource_scope}")
    print(f"\nRequires Active Database:\n{'YES' if execution_context.requires_database_context else 'NO'}")
    print(f"\nRequires Super Database:\n{'YES' if execution_context.requires_superdb else 'NO'}")
    print("\n====================================\n")

    # ── Check for column-less CREATE TABLE ────────────────────────────────────
    create_table_match = re.match(
        r"^\s*(?:create|make|build)\s+table\s+(?P<table_name>[a-zA-Z0-9_\"'\`]+)\s*;?\s*$",
        instruction.strip(),
        re.IGNORECASE
    )
    if create_table_match:
        tbl_name = create_table_match.group("table_name").strip('"`\'')
        final_db = target_db or meta.get("selected_db") or session.get("selected_database")
        if not final_db:
            dbs = sorted(meta.get("databases", []))
            db_list = "\n".join(f"* {db}" for db in dbs)
            return {
                "intent": "NEEDS_CLARIFICATION",
                "sql": None,
                "question": (
                    "Which database should I use?\n\n"
                    f"{db_list}"
                ),
                "clarification_data": {
                    "type": "MISSING_DATABASE",
                    "target_db": None,
                    "options": dbs,
                    "original_request": instruction,
                    "metadata": {},
                    "attempts": 0
                },
                "execution_database": None,
                "target_db": None,
                "valid": True,
                "risk_level": None,
                "requires_confirmation": False,
                "blocked_reason": None,
                "failure_reason": None,
                "execution": None,
            }
        
        return {
            "intent": "NEEDS_CLARIFICATION",
            "sql": None,
            "question": f"What columns would you like in table {tbl_name}?\n\nExample:\n* id INTEGER\n* name TEXT",
            "clarification_data": {
                "type": "CREATE_TABLE_COLUMNS",
                "table_name": tbl_name,
                "target_db": final_db,
                "original_request": instruction,
                "metadata": {},
                "attempts": 0
            },
            "execution_database": None,
            "target_db": final_db,
            "valid": True,
            "risk_level": None,
            "requires_confirmation": False,
            "blocked_reason": None,
            "failure_reason": None,
            "execution": None,
        }

    # 3. If active database is required but target_db is None, prompt for database selection
    if execution_context.requires_database_context and not target_db:
        dbs = sorted(meta.get("databases", []))
        db_list = "\n".join(f"* {db}" for db in dbs)
        return {
            "intent": "NEEDS_CLARIFICATION",
            "sql": None,
            "question": (
                "Which database should I use?\n\n"
                f"{db_list}"
            ),
            "clarification_data": {
                "type": "MISSING_DATABASE",
                "target_db": None,
                "options": dbs,
                "original_request": instruction,
                "metadata": {},
                "attempts": 0
            },
            "execution_database": None,
            "target_db": None,
            "valid": True,
            "risk_level": None,
            "requires_confirmation": False,
            "blocked_reason": None,
            "failure_reason": None,
            "execution": None,
        }

    from agent.sql_detector import is_raw_sql
    is_sql, _ = is_raw_sql(instruction)
    raw_intent = _raw_sql_intent(instruction.strip()) if is_sql else None
    is_admin_cmd = execution_context.requires_superdb

    if not is_admin_cmd:
        _ensure_cached_schema(target_db)

    planner_table = None
    planner_context_block = ""
    if planning_doc and isinstance(planning_doc, dict):
        plan_inner = planning_doc.get("planning_document", {}) if isinstance(planning_doc.get("planning_document"), dict) else planning_doc
        if not plan_inner.get("clarification_required", True):
            db_context = plan_inner.get("database_context", {}) or {}
            schema_objects = db_context.get("required_schema_objects", []) or []
            if schema_objects:
                planner_table = schema_objects[0]

            req_elements = db_context.get("required_metadata_elements", []) or []
            print(f"[SCHEMA RESOLUTION] Source: Groq planning_document | Tables: {schema_objects or 'None'} | Columns: {req_elements}")
            goal = plan_inner.get("goal", "")
            steps = plan_inner.get("execution_steps", []) or []

            tables_str = ", ".join(schema_objects) if schema_objects else "None"
            elements_str = ", ".join(req_elements) if req_elements else "None"
            steps_str = " -> ".join(steps) if steps else "None"
            planner_context_block = (
                f"\n\n[LOCAL PLANNER RESOLVED CONTEXT]\n"
                f"Resolved Target Tables: {tables_str}\n"
                f"Resolved Elements/Columns: {elements_str}\n"
                f"Goal: {goal}\n"
                f"Plan Steps: {steps_str}\n"
                f"Instruction: Use the resolved table and column references above directly for SQL generation.\n"
            )

    dynamic_context = _build_context(session, target_db if not is_admin_cmd else None, target_table=planner_table, pipeline_hint=pipeline_hint)
    if planner_context_block:
        dynamic_context += planner_context_block


    # ── Phase 4.5 Finding 1: Raw SQL fast-path ────────────────────────────────
    # If the instruction is already a raw SQL statement, bypass Gemini entirely.
    # Determine the intent deterministically from the leading keyword, then run
    # the normal validation → execution pipeline unchanged.
    if raw_intent is not None:
        intent             = raw_intent
        sql                = instruction.strip()
        question           = None
        execution_database = None
    else:
        intent             = "SQL_RETRIEVAL" if instruction.strip().upper().startswith("SELECT") else "DATABASE_MODIFICATION"
        sql                = instruction.strip()
        question           = None
        execution_database = None

    if intent == "UNKNOWN" and execution_context and execution_context.operation_type != "UNKNOWN":
        intent = execution_context.operation_type

    # Build base result
    result = {
        "intent": intent,
        "sql": sql,
        "question": question,
        "execution_database": execution_database,
        "target_db": target_db,
        "valid": True,
        "risk_level": None,
        "requires_confirmation": False,
        "blocked_reason": None,
        "failure_reason": None,
        "execution": None,
    }

    # ── Short-circuit: no SQL or non-actionable intent ───────────────────────
    # RATE_LIMITED is a Phase 8.1 sentinel returned when the Gemini API quota
    # is exhausted after one retry. Pass it straight back so the coordinator
    # can display a user-friendly message without hitting validation or executor.
    if intent in {"NEEDS_CLARIFICATION", "UNKNOWN", "RATE_LIMITED"} or sql is None:
        return result

    # Close the raw-SQL else branch started above
    # (Python does not require explicit close — this comment marks the logical join point)

    # ── Phase 4: Validate ─────────────────────────────────────────────────────
    raw_schema = meta.get("schema", {})
    validation = validate_sql(intent, sql, raw_schema)

    result["valid"]                = validation["valid"]
    result["risk_level"]           = validation["risk_level"]
    result["requires_confirmation"] = validation["requires_confirmation"]
    result["blocked_reason"]       = validation["blocked_reason"]
    result["failure_reason"]       = validation.get("failure_reason")

    # If requires confirmation, populate clarification_data
    if validation["requires_confirmation"]:
        final_exec_db = target_db or execution_database
        result["clarification_data"] = {
            "type": "CONFIRMATION",
            "target_db": final_exec_db,
            "original_request": instruction,
            "metadata": {
                "sql": sql,
                "intent": intent,
            },
            "attempts": 0
        }

    # Stop if blocked, invalid, or needs user confirmation
    if not validation["valid"] or validation["requires_confirmation"]:
        return result

    # ── Phase 5: Execute ──────────────────────────────────────────────────────
    # Phase 6.1 Bridge: resolve the final execution database.
    # Priority:
    #   1. requires_superdb(sql) — force administrative commands to DB_SUPERDB
    #   2. target_db  — resolved upstream by Router AI / explicit DB / session memory
    #   3. execution_database — returned by Gemini SQL generator as a semantic fallback
    if execution_context.requires_superdb:
        exec_db = get_superdb_name()
    else:
        exec_db = target_db or execution_database

    if not exec_db:
        result["valid"] = False
        result["failure_reason"] = "No target database resolved."
        return result

    if not target_db and execution_database and not execution_context.requires_superdb:
        print(f"\n[Phase 6.1 Bridge]")
        print(f"  Target DB      : None")
        print(f"  execution_database: {execution_database}")
        print(f"  Using execution_database fallback: {execution_database}\n")

    execution_result = _execute_sql(sql, exec_db, intent=intent)
    result["execution"] = execution_result

    # ── Phase 6: Update session ONLY on success ──────────────────────────────
    if execution_result.get("success") and session_id:
        op        = execution_result.get("operation", "")
        sql_parts = sql.strip().split()
        kwargs: dict = {
            "last_successful_intent": intent,
            "add_operation": op,
        }
        if op == "CREATE DATABASE" and len(sql_parts) >= 3:
            kwargs["selected_database"] = sql_parts[2].strip('";')
        elif op in {"CREATE", "ALTER", "CREATE TABLE", "ALTER TABLE"} and len(sql_parts) >= 3 and sql_parts[1].upper() == "TABLE":
            if "RENAME" not in sql.upper():
                new_tbl = extract_table_name(sql)
                if new_tbl:
                    kwargs["selected_table"] = new_tbl
        update_session(session_id, **kwargs)

        # Handle DROP TABLE cleanup
        if op == "DROP" and "TABLE" in sql.upper():
            match = re.search(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?([a-zA-Z0-9_\"'`]+)", sql, re.IGNORECASE)
            if match:
                dropped_table = match.group(1).strip('`"\'')
                from state.session_store import clear_session_table
                clear_session_table(session_id, dropped_table)

        # Handle ALTER TABLE RENAME TO
        elif op == "ALTER" and "TABLE" in sql.upper() and "RENAME" in sql.upper():
            match = re.search(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([a-zA-Z0-9_\"'`]+)\s+RENAME\s+TO\s+([a-zA-Z0-9_\"'`]+)", sql, re.IGNORECASE)
            if match:
                old_table = match.group(1).strip('`"\'')
                new_table = match.group(2).strip('`"\'')
                from state.session_store import rename_session_table
                rename_session_table(session_id, old_table, new_table)

        # Handle DROP DATABASE session cleanup
        elif op == "DROP DATABASE" or (op == "DROP" and "DATABASE" in sql.upper()):
            from db.executor import _clean_sql
            cleaned_sql = _clean_sql(sql)
            match = re.search(r"DROP\s+DATABASE\s+(?:IF\s+EXISTS\s+)?([a-zA-Z0-9_\"'`]+)", cleaned_sql, re.IGNORECASE)
            if match:
                dropped_db = match.group(1).strip('`"\'')
                meta = get_metadata()
                routing_summaries = meta.get("routing_summaries", {})
                db_tables = []
                for d_name, t_names in routing_summaries.items():
                    if d_name.lower() == dropped_db.lower():
                        db_tables = t_names
                        break
                from state.session_store import clear_session_database
                clear_session_database(session_id, dropped_db, db_tables)

    # ── Phase 7 & Phase 8.2: Metadata Cache Synchronization ───────────────────
    if execution_result.get("success"):
        op = execution_result.get("operation", "")
        from state.metadata_store import refresh_metadata_after_ddl
        # Use exec_db (the database the SQL actually ran against), not the raw
        # target_db parameter — target_db is often None when the database was
        # resolved via session/execution_database fallback rather than passed
        # explicitly, which silently skipped the schema-cache refresh below.
        refresh_metadata_after_ddl(sql, exec_db, op, session_id=session_id)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Tool 2 — get_schema_info
# ─────────────────────────────────────────────────────────────────────────────

def get_schema_info(target_db: Optional[str], session: dict, message: Optional[str] = None) -> dict:
    """
    Returns database structure information from the in-memory metadata cache.
    Never queries PostgreSQL directly.
    """
    import re
    meta = get_metadata()
    routing_summaries = meta.get("routing_summaries", {})

    # Try to extract table name from user message if provided
    table_name = None
    if message:
        # Gather all table names across all databases in routing index
        all_tables = set()
        for tables_list in routing_summaries.values():
            for t in tables_list:
                all_tables.add(t.lower())
                
        words = re.findall(r"\b[a-zA-Z0-9_-]+\b", message)
        for word in words:
            if word.lower() in all_tables:
                # Resolve to the actual casing from summaries
                for tables_list in routing_summaries.values():
                    for t in tables_list:
                        if t.lower() == word.lower():
                            table_name = t
                            break
                    if table_name:
                        break
            if table_name:
                break

    # If table_name is specified, check for location ambiguity
    if table_name and not target_db:
        matching_dbs = []
        for db, tables in routing_summaries.items():
            if any(t.lower() == table_name.lower() for t in tables):
                matching_dbs.append(db)
        
        if len(matching_dbs) > 1:
            db_options = sorted(matching_dbs)
            db_list = "\n".join(f"* {db}" for db in db_options)
            return {
                "intent": "NEEDS_CLARIFICATION",
                "question": (
                    f"Table '{table_name}' exists in:\n\n{db_list}\n\n"
                    "Which database would you like to inspect?"
                ),
                "clarification_data": {
                    "type": "AMBIGUOUS_TABLE_LOCATION",
                    "target_db": None,
                    "options": db_options,
                    "original_request": message,
                    "metadata": {
                        "table_name": table_name
                    },
                    "attempts": 0
                }
            }
        elif len(matching_dbs) == 1:
            # Auto-route to that single database context for this lookup
            target_db = matching_dbs[0]

    active_db = (
        target_db
        or meta.get("selected_db")
        or session.get("selected_database")
    )

    if not active_db:
        dbs = sorted(meta.get("databases", []))
        db_list = "\n".join(f"* {db}" for db in dbs)
        return {
            "intent": "NEEDS_CLARIFICATION",
            "question": (
                "Which database should I use?\n\n"
                f"{db_list}"
            ),
            "clarification_data": {
                "type": "MISSING_DATABASE",
                "target_db": None,
                "options": dbs,
                "original_request": message or "Describe schema",
                "metadata": {},
                "attempts": 0
            }
        }

    _ensure_cached_schema(active_db)
    schema = meta.get("schema", {})
    tables = meta.get("tables", [])
    table_schemas = meta.get("table_schemas", {})

    # Filter by specific table name if matching
    if table_name:
        filtered_schema = {}
        filtered_table_schemas = {}
        for tbl, cols in schema.items():
            if tbl.lower() == table_name.lower():
                filtered_schema[tbl] = cols
                filtered_table_schemas[tbl] = table_schemas.get(tbl, {})
        return {
            "active_db": active_db,
            "tables": [table_name] if table_name in tables else tables,
            "schema": filtered_schema,
            "table_schemas": filtered_table_schemas,
            "routing_summaries": routing_summaries,
        }

    return {
        "active_db": active_db,
        "tables": tables,
        "schema": schema,
        "table_schemas": table_schemas,
        "routing_summaries": routing_summaries,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tool 3 — summarize_results
# ─────────────────────────────────────────────────────────────────────────────

def summarize_results(execution_result: dict) -> str:
    """
    Pure formatting tool: converts already-executed query rows into natural language.
    Never generates SQL. Never validates. Never touches PostgreSQL.

    Args:
        execution_result: The 'execution' dict from a prior execute_sql call.
                          Expected keys: columns, rows.

    Returns:
        A natural-language string summary.
    """
    columns = execution_result.get("columns", [])
    rows    = execution_result.get("rows", [])

    if not columns:
        return "No data was returned from the query."

    data_payload = json.dumps({"columns": columns, "rows": rows[:50]}, default=str)  # cap rows for token safety
    prompt = f"{SUMMARIZE_PROMPT}\n\nDATA:\n{data_payload}"

    start_time = time.perf_counter()
    try:
        # Phase 8.5: record this Gemini call in the metrics tracker
        # Phase 4.5 Finding 4: use the same retry wrapper as router/planner/sql_generator
        gemini_metrics.record_call("summarizer")
        response, success, rate_limit_msg = call_with_retry(
            fn=lambda: model_manager.FORMATTER_MODEL.generate_content(prompt),
            label="Summarizer",
        )
        duration_ms = (time.perf_counter() - start_time) * 1000

        if not success:
            print(f"[Phase 4.5] Summarizer rate limit exhausted after retry.")
            try:
                logger_ai.error(
                    f"AI Summarization failed: rate limit exhausted - {rate_limit_msg}",
                    extra={
                        "category": "ai",
                        "operation_type": "SUMMARY",
                        "intent": "SUMMARIZE_RESULTS",
                        "success": False,
                        "execution_time_ms": duration_ms,
                        "error": rate_limit_msg,
                        "model": model_manager.current_model_name()
                    }
                )
            except Exception:
                pass
            return f"Summarisation unavailable: {rate_limit_msg}"

        prompt_tokens = 0
        completion_tokens = 0
        cost = 0.0
        if response and hasattr(response, "usage_metadata") and response.usage_metadata:
            prompt_tokens = response.usage_metadata.prompt_token_count
            completion_tokens = response.usage_metadata.candidates_token_count
            gemini_metrics.record_tokens(
                "summarizer",
                prompt_tokens,
                completion_tokens
            )
            cost = gemini_metrics.estimate_cost(prompt_tokens, completion_tokens)

        log_extra = {
            "category": "ai",
            "operation_type": "SUMMARY",
            "intent": "SUMMARIZE_RESULTS",
            "success": True,
            "execution_time_ms": duration_ms,
            "prompt_length": len(prompt),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost": cost,
            "model": model_manager.current_model_name(),
            "output_length": len(response.text)
        }
        if os.getenv("LOG_FULL_PROMPTS", "false").lower() == "true":
            log_extra["prompt"] = prompt
            log_extra["response_text"] = response.text

        try:
            logger_ai.info("AI Summarization completed successfully", extra=log_extra)
        except Exception:
            pass

        return response.text.strip()
    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_ai.error(
                f"AI Summarization failed with unexpected error: {str(e)}",
                exc_info=True,
                extra={
                    "category": "ai",
                    "operation_type": "SUMMARY",
                    "intent": "SUMMARIZE_RESULTS",
                    "success": False,
                    "execution_time_ms": duration_ms,
                    "error": str(e),
                    "model": model_manager.current_model_name()
                }
            )
        except Exception:
            pass
        return f"Summarisation failed: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Tool 4 — generate_report
# ─────────────────────────────────────────────────────────────────────────────

def generate_report(execution_result: dict) -> str:
    """
    Pure formatting tool: converts already-executed query rows into a Markdown report.
    Never generates SQL. Never validates. Never touches PostgreSQL.

    Args:
        execution_result: The 'execution' dict from a prior execute_sql call.
                          Expected keys: columns, rows.

    Returns:
        A Markdown-formatted string report.
    """
    columns = execution_result.get("columns", [])
    rows    = execution_result.get("rows", [])

    if not columns:
        return "## Report\n\nNo data available for this report."

    data_payload = json.dumps({"columns": columns, "rows": rows[:100]}, default=str)  # cap rows for token safety
    prompt = f"{REPORT_PROMPT}\n\nDATA:\n{data_payload}"

    start_time = time.perf_counter()
    try:
        # Phase 8.5: record this Gemini call in the metrics tracker
        # Phase 4.5 Finding 5: use the same retry wrapper as router/planner/sql_generator
        gemini_metrics.record_call("report_formatter")
        response, success, rate_limit_msg = call_with_retry(
            fn=lambda: model_manager.FORMATTER_MODEL.generate_content(prompt),
            label="Report Formatter",
        )
        duration_ms = (time.perf_counter() - start_time) * 1000

        if not success:
            print(f"[Phase 4.5] Report Formatter rate limit exhausted after retry.")
            try:
                logger_ai.error(
                    f"AI Report generation failed: rate limit exhausted - {rate_limit_msg}",
                    extra={
                        "category": "ai",
                        "operation_type": "REPORT",
                        "intent": "GENERATE_REPORT",
                        "success": False,
                        "execution_time_ms": duration_ms,
                        "error": rate_limit_msg,
                        "model": model_manager.current_model_name()
                    }
                )
            except Exception:
                pass
            return f"## Report\n\nReport generation unavailable: {rate_limit_msg}"

        prompt_tokens = 0
        completion_tokens = 0
        cost = 0.0
        if response and hasattr(response, "usage_metadata") and response.usage_metadata:
            prompt_tokens = response.usage_metadata.prompt_token_count
            completion_tokens = response.usage_metadata.candidates_token_count
            gemini_metrics.record_tokens(
                "report_formatter",
                prompt_tokens,
                completion_tokens
            )
            cost = gemini_metrics.estimate_cost(prompt_tokens, completion_tokens)

        log_extra = {
            "category": "ai",
            "operation_type": "REPORT",
            "intent": "GENERATE_REPORT",
            "success": True,
            "execution_time_ms": duration_ms,
            "prompt_length": len(prompt),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost": cost,
            "model": model_manager.current_model_name(),
            "output_length": len(response.text)
        }
        if os.getenv("LOG_FULL_PROMPTS", "false").lower() == "true":
            log_extra["prompt"] = prompt
            log_extra["response_text"] = response.text

        try:
            logger_ai.info("AI Report generation completed successfully", extra=log_extra)
        except Exception:
            pass

        return response.text.strip()
    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_ai.error(
                f"AI Report generation failed with unexpected error: {str(e)}",
                exc_info=True,
                extra={
                    "category": "ai",
                    "operation_type": "REPORT",
                    "intent": "GENERATE_REPORT",
                    "success": False,
                    "execution_time_ms": duration_ms,
                    "error": str(e),
                    "model": model_manager.current_model_name()
                }
            )
        except Exception:
            pass
        return f"Report generation failed: {e}"


# Helper to print the API CALL SAVED block
def _print_api_call_saved(operation: str, sql: str):
    print("\n====================================")
    print("API CALL SAVED")
    print("====================================")
    print(f"Operation:\n{operation}")
    print("\nRoute:\nDeterministic")
    print("\nGemini:\nSKIPPED")
    print(f"\nGenerated SQL:\n{sql}")
    print("====================================\n")


# ─────────────────────────────────────────────────────────────────────────────
# Shared DDL dispatch — try a deterministic parse of `instruction` via
# ddl_parser first (bypassing the Gemini Executor entirely when possible),
# falling through to execute_sql's own NL/raw-SQL handling otherwise. The
# create/drop database and drop/alter/rename table tools all follow this same
# shape and differ only in their log label, execution-context tool name,
# deterministic intent label, and whether the operation requires confirmation.
# ─────────────────────────────────────────────────────────────────────────────

def _ddl_dispatch(
    op_label: str,
    context_tool_name: str,
    deterministic_intent: str,
    instruction: str,
    target_db: Optional[str],
    session: dict,
    session_id: Optional[str],
    history: list,
    user_intent: Optional[str] = None,
    requires_confirmation: bool = False,
    parsed=None,
) -> dict:
    if parsed is None:
        parsed = parse_simple_ddl(instruction, session)

    if parsed and parsed.deterministic:
        sql = parsed.sql
        _print_api_call_saved(op_label, sql)

        from agent import gemini_metrics
        gemini_metrics.record_bypass("planner")

        context = build_execution_context(context_tool_name, instruction, user_intent=deterministic_intent)
        if requires_confirmation:
            context.requires_confirmation = True
        return execute_sql(
            instruction=sql,
            target_db=target_db,
            session=session,
            session_id=session_id,
            history=history,
            execution_context=context,
        )

    context = build_execution_context(context_tool_name, instruction, user_intent=user_intent)
    return execute_sql(
        instruction=instruction,
        target_db=target_db,
        session=session,
        session_id=session_id,
        history=history,
        execution_context=context,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 5 — create_database
# ─────────────────────────────────────────────────────────────────────────────

def create_database(
    instruction: str,
    target_db: Optional[str],
    session: dict,
    session_id: Optional[str],
    history: list,
    user_intent: Optional[str] = None,
) -> dict:
    return _ddl_dispatch(
        "CREATE_DATABASE", "create_database", "CREATE_DATABASE",
        instruction, target_db, session, session_id, history, user_intent,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 5.1 — drop_database
# ─────────────────────────────────────────────────────────────────────────────

def drop_database(
    instruction: str,
    target_db: Optional[str],
    session: dict,
    session_id: Optional[str],
    history: list,
    user_intent: Optional[str] = None,
) -> dict:
    return _ddl_dispatch(
        "DROP_DATABASE", "execute_sql", "DROP_DATABASE",
        instruction, target_db, session, session_id, history, user_intent,
        requires_confirmation=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 6 — create_table
# ─────────────────────────────────────────────────────────────────────────────

def create_table(
    instruction: str,
    target_db: Optional[str],
    session: dict,
    session_id: Optional[str],
    history: list,
    user_intent: Optional[str] = None,
) -> dict:
    parsed = parse_simple_ddl(instruction, session)
    if parsed:
        if parsed.clarification_needed:
            tbl_name = parsed.table_name
            meta = get_metadata()
            final_db = target_db or meta.get("selected_db") or session.get("selected_database")
            if not final_db:
                dbs = sorted(meta.get("databases", []))
                db_list = "\n".join(f"* {db}" for db in dbs)
                return {
                    "intent": "NEEDS_CLARIFICATION",
                    "sql": None,
                    "question": f"Which database should I use?\n\n{db_list}",
                    "clarification_data": {
                        "type": "MISSING_DATABASE",
                        "target_db": None,
                        "options": dbs,
                        "original_request": instruction,
                        "metadata": {},
                        "attempts": 0
                    },
                    "execution_database": None,
                    "target_db": None,
                    "valid": True,
                    "risk_level": None,
                    "requires_confirmation": False,
                    "blocked_reason": None,
                    "failure_reason": None,
                    "execution": None,
                }
            
            clar_type = "CREATE_TABLE_COLUMNS" if tbl_name else "CREATE_TABLE_NAME"
            return {
                "intent": "NEEDS_CLARIFICATION",
                "sql": None,
                "question": parsed.clarification_message,
                "pending_operation": {
                    "type": "CREATE_TABLE",
                    "arguments": {
                        "table_name": tbl_name,
                        "target_db": final_db
                    }
                },
                "clarification_data": {
                    "type": clar_type,
                    "table_name": tbl_name,
                    "target_db": final_db,
                    "original_request": instruction,
                    "attempts": 0
                },
                "execution_database": None,
                "target_db": final_db,
                "valid": True,
                "risk_level": None,
                "requires_confirmation": False,
                "blocked_reason": None,
                "failure_reason": None,
                "execution": None,
            }
        
        elif parsed.deterministic:
            return _ddl_dispatch(
                "CREATE_TABLE", "create_table", "CREATE_TABLE",
                instruction, target_db, session, session_id, history, user_intent,
                parsed=parsed,
            )

    context = build_execution_context("create_table", instruction, user_intent=user_intent)
    return execute_sql(
        instruction=instruction,
        target_db=target_db,
        session=session,
        session_id=session_id,
        history=history,
        execution_context=context,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 6.1 — drop_table
# ─────────────────────────────────────────────────────────────────────────────

def drop_table(
    instruction: str,
    target_db: Optional[str],
    session: dict,
    session_id: Optional[str],
    history: list,
    user_intent: Optional[str] = None,
) -> dict:
    return _ddl_dispatch(
        "DROP_TABLE", "execute_sql", "DROP_TABLE",
        instruction, target_db, session, session_id, history, user_intent,
        requires_confirmation=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 6.2 — alter_table
# ─────────────────────────────────────────────────────────────────────────────

def alter_table(
    instruction: str,
    target_db: Optional[str],
    session: dict,
    session_id: Optional[str],
    history: list,
    user_intent: Optional[str] = None,
) -> dict:
    return _ddl_dispatch(
        "ALTER_TABLE", "execute_sql", "ALTER_TABLE",
        instruction, target_db, session, session_id, history, user_intent,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 6.3 — rename_table
# ─────────────────────────────────────────────────────────────────────────────

def rename_table(
    instruction: str,
    target_db: Optional[str],
    session: dict,
    session_id: Optional[str],
    history: list,
    user_intent: Optional[str] = None,
) -> dict:
    return _ddl_dispatch(
        "RENAME_TABLE", "execute_sql", "RENAME_TABLE",
        instruction, target_db, session, session_id, history, user_intent,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 7 — list_databases
# ─────────────────────────────────────────────────────────────────────────────

def list_databases() -> dict:
    """
    Returns cached database list from metadata_store.
    Never queries PostgreSQL directly.
    """
    meta = get_metadata()
    return {
        "databases": meta.get("databases", []),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tool 8 — list_tables
# ─────────────────────────────────────────────────────────────────────────────

def list_tables(target_db: Optional[str], session: dict) -> dict:
    """
    Returns cached table list from metadata_store / routing summaries.
    Never queries PostgreSQL directly.
    """
    meta = get_metadata()
    active_db = (
        target_db
        or meta.get("selected_db")
        or session.get("selected_database")
    )
    if not active_db:
        dbs = sorted(meta.get("databases", []))
        db_list = "\n".join(f"* {db}" for db in dbs)
        return {
            "intent": "NEEDS_CLARIFICATION",
            "question": (
                "Which database should I use?\n\n"
                f"{db_list}"
            ),
            "clarification_data": {
                "type": "MISSING_DATABASE",
                "target_db": None,
                "options": dbs,
                "original_request": "List tables",
                "metadata": {},
                "attempts": 0
            }
        }

    # Prefer the full tables cache if it corresponds to the active db
    if active_db:
        routing = meta.get("routing_summaries", {})
        tables  = routing.get(active_db, meta.get("tables", []))
    else:
        tables = meta.get("tables", [])

    return {
        "active_db": active_db,
        "tables": tables,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tool 9 — find_table_database
# ─────────────────────────────────────────────────────────────────────────────

def find_table_database(message: str) -> dict:
    """
    Finds which database(s) contain the specified table name from the routing summaries.
    Never queries PostgreSQL directly.
    """
    from agent.agent_coordinator import _FIND_TABLE_DB_RULE, _SHOW_TABLE_RULE
    match = _SHOW_TABLE_RULE.match(message) or _FIND_TABLE_DB_RULE.match(message)
    if not match:
        return {
            "intent": "FIND_TABLE_DATABASE",
            "table_name": None,
            "databases": [],
        }
    
    table_name = match.group("table_name").strip('"`\'')
    meta = get_metadata()
    routing_summaries = meta.get("routing_summaries", {})
    
    matching_dbs = []
    for db_name, tables in routing_summaries.items():
        if any(t.lower() == table_name.lower() for t in tables):
            matching_dbs.append(db_name)
            
    return {
        "intent": "FIND_TABLE_DATABASE",
        "table_name": table_name,
        "databases": sorted(matching_dbs),
    }
