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
from typing import Optional

import google.generativeai as genai
from dotenv import load_dotenv

from ai.context_builder import build_schema_context
from ai.gemini_service import generate_sql_response
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

load_dotenv()

# ─── Shared Gemini formatting model (lightweight, no system prompt needed) ────
# Separate instance so it never bleeds SQL-generation instructions.
_fmt_model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config=genai.types.GenerationConfig(
        temperature=0.2,
        response_mime_type="text/plain",
    ),
)


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


def _build_context(session: dict, target_db: Optional[str]) -> str:
    """Build the dynamic schema context exactly as chat.py does."""
    meta = get_metadata()
    return build_schema_context(
        current_db=target_db,
        current_table=None,
        available_dbs=meta.get("databases", []),
        raw_schema=meta.get("schema", {}),
        session=session,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1 — execute_sql
# ─────────────────────────────────────────────────────────────────────────────

def execute_sql(
    instruction: str,
    target_db: Optional[str],
    session: dict,
    session_id: Optional[str],
    history: list,
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

    if not target_db:
        if not requires_superdb(instruction):
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

    _ensure_cached_schema(target_db)
    dynamic_context = _build_context(session, target_db)
    meta = get_metadata()

    # ── Phase 4.5 Finding 1: Raw SQL fast-path ────────────────────────────────
    # If the instruction is already a raw SQL statement, bypass Gemini entirely.
    # Determine the intent deterministically from the leading keyword, then run
    # the normal validation → execution pipeline unchanged.
    raw_intent = _raw_sql_intent(instruction.strip())
    if raw_intent is not None:
        print(f"[Phase 4.5] Raw SQL detected — bypassing Gemini SQL Generator.")
        print(f"[Phase 4.5] Inferred intent: {raw_intent}")
        intent             = raw_intent
        sql                = instruction.strip()
        question           = None
        execution_database = None
    else:
        # ── Phase 3: Generate SQL ─────────────────────────────────────────────
        gen_result = generate_sql_response(
            user_input=instruction,
            dynamic_context=dynamic_context,
            selected_db=target_db or "None",
            selected_table=None,
            history=history,
        )

        intent             = gen_result.get("intent", "UNKNOWN")
        sql                = gen_result.get("sql")
        question           = gen_result.get("question")
        execution_database = gen_result.get("execution_database")

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
                "sql": sql
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
    if requires_superdb(sql):
        exec_db = os.getenv("DB_SUPERDB", "postgres")
    else:
        exec_db = target_db or execution_database

    if not exec_db:
        result["valid"] = False
        result["failure_reason"] = "No target database resolved."
        return result

    if not target_db and execution_database and not requires_superdb(sql):
        print(f"\n[Phase 6.1 Bridge]")
        print(f"  Target DB      : None")
        print(f"  execution_database: {execution_database}")
        print(f"  Using execution_database fallback: {execution_database}\n")

    execution_result = _execute_sql(sql, exec_db)
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
        refresh_metadata_after_ddl(sql, target_db, op)

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

    try:
        # Phase 8.5: record this Gemini call in the metrics tracker
        # Phase 4.5 Finding 4: use the same retry wrapper as router/planner/sql_generator
        gemini_metrics.record_call("summarizer")
        response, success, rate_limit_msg = call_with_retry(
            fn=lambda: _fmt_model.generate_content(prompt),
            label="Summarizer",
        )
        if not success:
            print(f"[Phase 4.5] Summarizer rate limit exhausted after retry.")
            return f"Summarisation unavailable: {rate_limit_msg}"

        if response and hasattr(response, "usage_metadata") and response.usage_metadata:
            gemini_metrics.record_tokens(
                "summarizer",
                response.usage_metadata.prompt_token_count,
                response.usage_metadata.candidates_token_count
            )

        return response.text.strip()
    except Exception as e:
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

    try:
        # Phase 8.5: record this Gemini call in the metrics tracker
        # Phase 4.5 Finding 5: use the same retry wrapper as router/planner/sql_generator
        gemini_metrics.record_call("report_formatter")
        response, success, rate_limit_msg = call_with_retry(
            fn=lambda: _fmt_model.generate_content(prompt),
            label="Report Formatter",
        )
        if not success:
            print(f"[Phase 4.5] Report Formatter rate limit exhausted after retry.")
            return f"## Report\n\nReport generation unavailable: {rate_limit_msg}"

        if response and hasattr(response, "usage_metadata") and response.usage_metadata:
            gemini_metrics.record_tokens(
                "report_formatter",
                response.usage_metadata.prompt_token_count,
                response.usage_metadata.candidates_token_count
            )

        return response.text.strip()
    except Exception as e:
        return f"Report generation failed: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Tool 5 — create_database
# ─────────────────────────────────────────────────────────────────────────────

def create_database(
    instruction: str,
    target_db: Optional[str],
    session: dict,
    session_id: Optional[str],
    history: list,
) -> dict:
    """
    Thin wrapper: delegates entirely to execute_sql().
    The existing generate_sql_response() will generate the CREATE DATABASE statement.
    Validation, execution, session updates, and metadata refresh all happen inside execute_sql().
    """
    return execute_sql(
        instruction=instruction,
        target_db=target_db,
        session=session,
        session_id=session_id,
        history=history,
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
) -> dict:
    """
    Thin wrapper: delegates entirely to execute_sql().
    The existing generate_sql_response() will generate the CREATE TABLE statement.
    Validation, execution, session updates, and schema/routing refresh all happen inside execute_sql().
    """
    return execute_sql(
        instruction=instruction,
        target_db=target_db,
        session=session,
        session_id=session_id,
        history=history,
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
