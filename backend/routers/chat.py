import os
import re
import time
from typing import Optional
from fastapi import APIRouter, HTTPException
from models.schemas import ChatRequest, ChatResponse, ExecuteConfirmedRequest
from state.metadata_store import get_metadata, refresh_routing_summaries  # Phase 7
from state.session_store import get_session, update_session, log_session_state  # Phase 6
from ai.router_service import route_to_database  # Phase 7
from validation.sql_validator import validate as validate_sql
from db.executor import execute_sql  # Phase 5 Executor (kept for /execute-confirmed)
from agent import gemini_metrics  # Phase 8.5: Gemini call instrumentation
from agent.agent_coordinator import _deterministic_dispatch  # Phase 4.5 Finding 3

router = APIRouter()


# ─── Phase 8.4 OPT-1 + Phase 8.9: Explicit database extraction ────────────────

# Patterns that indicate the user has named a database explicitly.
# Matching is case-insensitive; the extracted name is lower-cased before lookup.
_EXPLICIT_DB_PATTERN = re.compile(
    r"\b(?:from|in|use)\s+(?:database\s+|db\s+)?([\w]+)\b|\b(?:database|db)\s+([\w]+)\b",
    re.IGNORECASE,
)


def _extract_explicit_db(
    message: str,
    routing_summaries: dict,
) -> Optional[str]:
    """
    Phase 8.4 OPT-1 + Phase 8.9: Scan the user message for an explicit database name.

    Returns a validated database name string if found in routing_summaries,
    otherwise returns None (Router AI runs as normal).
    """
    for match in _EXPLICIT_DB_PATTERN.finditer(message):
        candidate = (match.group(1) or match.group(2) or "").lower()
        if candidate in routing_summaries:
            print(f"[Phase 8.9] Explicit DB extracted: '{candidate}' from message.")
            return candidate
    return None


_NEW_COMMAND_PATTERN = re.compile(
    r"^\s*(show|list|display|find|count|search|get|use|switch|select|insert|update|delete|create|alter|drop|truncate|describe|explain|generate\s+report|format)\b",
    re.IGNORECASE
)


def is_new_command_detected(message: str) -> bool:
    """
    Detect if the user message matches a brand new command prefix.
    If so, we discard any pending clarification context.
    """
    return bool(_NEW_COMMAND_PATTERN.match(message))


_VALID_DATATYPES = {
    "int", "integer", "serial", "text", "varchar", "char", "numeric",
    "decimal", "float", "double", "real", "boolean", "bool", "date",
    "time", "timestamp", "json", "uuid",
    "bigint", "smallint", "jsonb", "bytea", "timestamptz", "character varying", "character",
    "double precision"
}

_CONSTRAINT_KEYWORDS = {"primary", "foreign", "unique", "check", "constraint"}


_FORBIDDEN_COLUMN_NAMES = {
    "show", "list", "display", "find", "count", "search", "get", "use",
    "switch", "select", "insert", "update", "delete", "create", "alter",
    "drop", "truncate", "describe", "explain", "generate", "format"
}


def split_column_definitions(message: str) -> list[str]:
    """
    Split a column definition list by commas, but ignoring commas inside parentheses.
    E.g., "price NUMERIC(10,2), name TEXT" -> ["price NUMERIC(10,2)", "name TEXT"]
    """
    parts = []
    current = []
    paren_depth = 0
    for char in message:
        if char == '(':
            paren_depth += 1
        elif char == ')':
            paren_depth -= 1
        
        if char == ',' and paren_depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
            
    if current:
        parts.append("".join(current).strip())
        
    return [p for p in parts if p]


def is_valid_column_definition(message: str) -> bool:
    """
    Validate if the message looks like a valid Postgres column definition list
    (e.g., "id INTEGER, name VARCHAR(100) NOT NULL" or just "name TEXT").
    """
    parts = split_column_definitions(message)
    if not parts:
        return False

    for part in parts:
        tokens = part.split()
        if not tokens:
            continue

        # If it's a composite constraint, skip column-specific validation
        first_token = tokens[0].lower()
        is_constraint = False
        for kw in _CONSTRAINT_KEYWORDS:
            if first_token == kw or first_token.startswith(kw + "("):
                is_constraint = True
                break
        if is_constraint:
            continue

        if len(tokens) < 2:
            return False

        col_name = tokens[0].lower()
        if col_name in _FORBIDDEN_COLUMN_NAMES:
            return False

        datatype = tokens[1].lower()
        # Handle multi-word type "character varying"
        if datatype == "character" and len(tokens) >= 3 and (tokens[2].lower() == "varying" or tokens[2].lower().startswith("varying(")):
            datatype_clean = "character varying"
        # Handle multi-word type "double precision"
        elif datatype == "double" and len(tokens) >= 3 and tokens[2].lower().startswith("precision"):
            datatype_clean = "double precision"
        else:
            datatype_clean = re.sub(r"\(.*?\)", "", tokens[1]).strip().lower()
        
        if datatype_clean not in _VALID_DATATYPES:
            return False

    return True


# ─── Helpers (unchanged from Phase 5 / Phase 6) ──────────────────────────────

def _apply_refresh_flags(response: ChatResponse, execution_result: dict):
    """Helper to set schema refresh flags based on successful execution."""
    if not execution_result.get("success"):
        return

    op = execution_result.get("operation", "")

    if op == "CREATE DATABASE":
        response.refresh_databases = True
    elif op in ["CREATE", "ALTER", "DROP"]:
        if response.database:
            response.refresh_tables = True
            response.refresh_schema = True


def _update_session_from_execution(
    session_id: str | None,
    intent: str,
    sql: str | None,
    execution_result: dict | None,
) -> None:
    """
    Phase 6: Update session state ONLY when execution succeeded.
    This is the critical guard — memory never reflects failed operations.
    """
    if not execution_result or not execution_result.get("success"):
        return

    if not sql:
        return

    op        = execution_result.get("operation", "")
    sql_strip = sql.strip()
    sql_parts = sql_strip.split()

    kwargs: dict = {"last_successful_intent": intent, "add_operation": op}

    # Extract the newly created database name
    if op == "CREATE DATABASE" and len(sql_parts) >= 3:
        new_db = sql_parts[2].strip('";')
        kwargs["selected_database"] = new_db

    # Extract the newly created / modified table name
    elif op in {"CREATE", "ALTER", "CREATE TABLE", "ALTER TABLE"} and len(sql_parts) >= 3 and sql_parts[1].upper() == "TABLE":
        if "RENAME" not in sql.upper():
            from db.executor import extract_table_name
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


# ─── Phase 8: /chat endpoint ─────────────────────────────────────────────────


def _print_clarification_trace(
    clarification_type: str,
    original_request: str,
    options: list,
    selected_option: str,
    reconstructed_request: str,
    override_target_db: str,
) -> None:
    """Print a structured clarification resolution trace block. No side effects."""
    print("=====================================")
    print("CLARIFICATION RESOLUTION TRACE")
    print("=====================================")
    print(f"Clarification Type  : {clarification_type}")
    print(f"Original Request    : {original_request}")
    print(f"Options             : {options if options else 'N/A'}")
    print(f"Selected Option     : {selected_option}")
    print(f"Reconstructed Req   : {reconstructed_request}")
    print(f"Override Target DB  : {override_target_db}")
    print("=====================================")


def resolve_pending_clarification(message: str, pending: dict) -> dict:
    msg = message.strip().lower()
    
    # 1. Cancellation terms (including confirmation rejections)
    if msg in {"cancel", "nevermind", "stop", "reset", "no", "n", "reject", "deny"}:
        _print_clarification_trace(
            clarification_type=pending.get("type"),
            original_request=pending.get("original_request", "<unknown>"),
            options=pending.get("options", []),
            selected_option="cancel",
            reconstructed_request="<cancelled>",
            override_target_db="<none>",
        )
        return {
            "resolved": True,
            "selected_option": "cancel",
            "clarification_type": pending.get("type"),
            "reconstructed_request": None,
            "override_target_db": None,
            "metadata": {}
        }
        
    clar_type = pending.get("type")
    
    # 2. CREATE_TABLE_COLUMNS
    if clar_type == "CREATE_TABLE_COLUMNS":
        table_name = pending.get("table_name")
        
        # Validation 1: table_name exists in pending clarification
        if not table_name:
            print("[Clarification] Validation failed: table_name missing in pending clarification.")
            return {
                "resolved": False,
                "selected_option": None,
                "clarification_type": clar_type,
                "reconstructed_request": None,
                "override_target_db": None,
                "metadata": {}
            }
            
        # Validation 2: table_name matches valid identifier pattern
        identifier_pat = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*$')
        if not identifier_pat.match(table_name):
            print(f"[Clarification] Validation failed: table_name '{table_name}' is not a valid SQL identifier.")
            return {
                "resolved": False,
                "selected_option": None,
                "clarification_type": clar_type,
                "reconstructed_request": None,
                "override_target_db": None,
                "metadata": {}
            }
            
        # Validation 3: columns string is non-empty
        columns_str = message.strip()
        if not columns_str:
            print("[Clarification] Validation failed: columns definition is empty.")
            return {
                "resolved": False,
                "selected_option": None,
                "clarification_type": clar_type,
                "reconstructed_request": None,
                "override_target_db": None,
                "metadata": {}
            }
            
        if not is_valid_column_definition(message):
            print(f"[Clarification] Invalid column definition structure: {repr(message)}")
            return {
                "resolved": False,
                "selected_option": None,
                "clarification_type": clar_type,
                "reconstructed_request": None,
                "override_target_db": None,
                "metadata": {}
            }
            
        target_db = pending.get("target_db")
        
        # Format columns with 4-space indentation and newline
        parts = [p.strip() for p in split_column_definitions(message)]
        indented_parts = [f"    {p}" for p in parts]
        cols_formatted = ",\n".join(indented_parts)
        reconstructed = f"CREATE TABLE {table_name} (\n{cols_formatted}\n);"
        
        # Validation 4: log/print resolution trace before execution
        orig_req = pending.get("original_request", "<unknown>")
        print("=====================================")
        print("CLARIFICATION RESOLUTION TRACE")
        print("=====================================")
        print("Original Request:")
        print(orig_req)
        print()
        print("Resolved Columns:")
        print(columns_str)
        print()
        print("Generated SQL:")
        print(reconstructed)
        print("=====================================")
        
        return {
            "resolved": True,
            "selected_option": message.strip(),
            "clarification_type": clar_type,
            "reconstructed_request": reconstructed,
            "override_target_db": target_db,
            "metadata": {}
        }
        
    # 3. CONFIRMATION
    if clar_type == "CONFIRMATION":
        if msg in {"confirm", "yes", "y", "ok", "proceed"}:
            sql = pending.get("metadata", {}).get("sql")
            target_db = pending.get("target_db")
            _print_clarification_trace(
                clarification_type=clar_type,
                original_request=pending.get("original_request", "<unknown>"),
                options=[],
                selected_option="confirm",
                reconstructed_request=sql or "<none>",
                override_target_db=target_db or "<none>",
            )
            return {
                "resolved": True,
                "selected_option": "confirm",
                "clarification_type": clar_type,
                "reconstructed_request": sql,
                "override_target_db": target_db,
                "metadata": {}
            }
        else:
            print("[Clarification] CONFIRMATION — user response not recognised as confirmation, resolved=False")
            return {
                "resolved": False,
                "selected_option": None,
                "clarification_type": clar_type,
                "reconstructed_request": None,
                "override_target_db": None,
                "metadata": {}
            }
            
    # 4. AMBIGUOUS_TABLE_LOCATION and MISSING_DATABASE
    if clar_type in {"AMBIGUOUS_TABLE_LOCATION", "MISSING_DATABASE"}:
        options = pending.get("options", [])
        cleaned_options = [opt.strip().lower() for opt in options]
        
        # 1. Exact match check (case-insensitive) first
        for idx, opt in enumerate(cleaned_options):
            if msg == opt:
                selected = options[idx]
                return _build_db_resolution_result(clar_type, selected, pending)
                
        # Sort options by length descending to prevent option shadowing
        sorted_options = sorted(list(enumerate(cleaned_options)), key=lambda x: len(x[1]), reverse=True)
        
        # 2. Word boundary check (longest options first)
        for idx, opt in sorted_options:
            if re.search(rf"\b{re.escape(opt)}\b", msg):
                selected = options[idx]
                return _build_db_resolution_result(clar_type, selected, pending)
                
        # 3. Ordinal words to index map
        ordinals = {
            "first": 0, "1st": 0, "1": 0, "one": 0,
            "second": 1, "2nd": 1, "2": 1, "two": 1,
            "third": 2, "3rd": 2, "3": 2, "three": 2,
        }
        for word, idx in ordinals.items():
            if re.search(rf"\b{word}\b", msg) and idx < len(options):
                selected = options[idx]
                return _build_db_resolution_result(clar_type, selected, pending)
                
        # 4. Numerical patterns (e.g. database 1, selection 2)
        numeric_patterns = [
            r"\b(?:database|db|option|choice|number|selection)\s+(\d+)\b",
            r"\b(?:the\s+)?(\w+)\s+(?:database|db|option)\b"
        ]
        for pat in numeric_patterns:
            match = re.search(pat, msg)
            if match:
                val = match.group(1)
                if val.isdigit():
                    idx = int(val) - 1
                    if 0 <= idx < len(options):
                        selected = options[idx]
                        return _build_db_resolution_result(clar_type, selected, pending)
                else:
                    if val in ordinals:
                        idx = ordinals[val]
                        if idx < len(options):
                            selected = options[idx]
                            return _build_db_resolution_result(clar_type, selected, pending)
                            
        # 5. Substring keyword check (longest options first)
        for idx, opt in sorted_options:
            if opt in msg:
                selected = options[idx]
                return _build_db_resolution_result(clar_type, selected, pending)
                
    # Could not match any resolution strategy
    print(
        f"[Clarification] UNRESOLVED — type={clar_type} message={repr(message)} "
        f"options={pending.get('options', [])}"
    )
    return {
        "resolved": False,
        "selected_option": None,
        "clarification_type": clar_type,
        "reconstructed_request": None,
        "override_target_db": None,
        "metadata": {}
    }


def _build_db_resolution_result(clar_type: str, selected: str, pending: dict) -> dict:
    orig_req = pending.get("original_request")
    _print_clarification_trace(
        clarification_type=clar_type,
        original_request=orig_req or "<unknown>",
        options=pending.get("options", []),
        selected_option=selected,
        reconstructed_request=orig_req or "<none>",
        override_target_db=selected,
    )
    return {
        "resolved": True,
        "selected_option": selected,
        "clarification_type": clar_type,
        "reconstructed_request": orig_req,
        "override_target_db": selected,
        "metadata": {}
    }


# ─── Phase 8: /chat endpoint ─────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    # ── Phase 8.5: Start metrics request tracking ───────────────────────────
    gemini_metrics.start_request(request.message)

    # ── Phase 6: Load session from RAM store ─────────────────────────────────
    session = get_session(request.session_id)
    log_session_state(request.session_id)

    # ── Extract dynamic context from in-memory metadata store ─────────────────
    meta              = get_metadata()
    current_db        = meta.get("selected_db")
    routing_summaries = meta.get("routing_summaries", {})

    # ── Phase 8.7: Defensive lazy refresh ────────────────────────────────────
    # Safety net: if routing summaries are empty (startup init failed, or DB was
    # unreachable at boot), attempt to rebuild them now before any routing logic runs.
    # This ensures all three routing paths work even without a prior /api/databases call.
    if not routing_summaries:
        print("[Phase 8.7] routing_summaries empty at request time — triggering lazy refresh.")
        try:
            from db.schema_fetcher import fetch_all_databases
            from state.metadata_store import set_databases
            dbs = fetch_all_databases()
            set_databases(dbs)
            refresh_routing_summaries()
            routing_summaries = get_metadata().get("routing_summaries", {})
            print(f"[Phase 8.7] Lazy refresh complete: {list(routing_summaries.keys())}")
        except Exception as e:
            print(f"[Phase 8.7] WARNING: Lazy refresh failed: {e}")

    # ── Check for existing pending clarification ─────────────────────────────
    pending = session.get("pending_clarification")
    if pending:
        created_at = pending.get("created_at", 0)
        # Timeout Check: 15 minutes limit (900 seconds)
        if time.time() - created_at > 900:
            print("[Phase 8.11] Clarification expired after timeout.")
            session["pending_clarification"] = None
            pending = None
        elif is_new_command_detected(request.message):
            print(f"[Phase 8.11] New command detected. Discarding pending clarification: {request.message}")
            session["pending_clarification"] = None
            pending = None
        else:
            # Try to resolve
            resolve = resolve_pending_clarification(request.message, pending)
            if resolve["resolved"]:
                print("[Phase 8.11] Clarification resolved.")
                print(f"Type      : {resolve['clarification_type']}")
                print(f"Selection : {resolve['selected_option']}")
                
                if resolve["selected_option"] == "cancel":
                    print("[Phase 8.11] Clarification canceled by user.")
                    session["pending_clarification"] = None
                    return ChatResponse(
                        reply="Clarification cancelled. Let's start again.",
                        intent="CANCEL",
                        database=None,
                        valid=True
                    )
                
                # If confirmation, execute DDL/DML directly
                if resolve["clarification_type"] == "CONFIRMATION":
                    from db.executor import execute_sql as db_execute_sql, requires_superdb
                    sql = resolve["reconstructed_request"]
                    exec_db = resolve["override_target_db"]
                    if requires_superdb(sql):
                        exec_db = os.getenv("DB_SUPERDB", "postgres")
                    
                    execution_result = db_execute_sql(sql, exec_db)
                    
                    if execution_result.get("success"):
                        # Update session memory
                        op = execution_result.get("operation", "")
                        sql_parts = sql.strip().split()
                        kwargs = {"last_successful_intent": "CONFIRMATION", "add_operation": op}
                        if op == "CREATE DATABASE" and len(sql_parts) >= 3:
                            kwargs["selected_database"] = sql_parts[2].strip('";')
                        elif op in {"CREATE", "ALTER", "CREATE TABLE", "ALTER TABLE"} and len(sql_parts) >= 3 and sql_parts[1].upper() == "TABLE":
                            if "RENAME" not in sql.upper():
                                from db.executor import extract_table_name
                                new_tbl = extract_table_name(sql)
                                if new_tbl:
                                    kwargs["selected_table"] = new_tbl
                        update_session(request.session_id, **kwargs)

                        # Handle DROP TABLE cleanup
                        if op == "DROP" and "TABLE" in sql.upper():
                            match = re.search(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?([a-zA-Z0-9_\"'`]+)", sql, re.IGNORECASE)
                            if match:
                                dropped_table = match.group(1).strip('`"\'')
                                from state.session_store import clear_session_table
                                clear_session_table(request.session_id, dropped_table)

                        # Handle ALTER TABLE RENAME TO
                        elif op == "ALTER" and "TABLE" in sql.upper() and "RENAME" in sql.upper():
                            match = re.search(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([a-zA-Z0-9_\"'`]+)\s+RENAME\s+TO\s+([a-zA-Z0-9_\"'`]+)", sql, re.IGNORECASE)
                            if match:
                                old_table = match.group(1).strip('`"\'')
                                new_table = match.group(2).strip('`"\'')
                                from state.session_store import rename_session_table
                                rename_session_table(request.session_id, old_table, new_table)
                        
                        # Handle DROP DATABASE session cleanup
                        from db.executor import _clean_sql
                        cleaned_sql = _clean_sql(sql)
                        is_drop_db = (
                            op == "DROP DATABASE"
                            or (op == "DROP" and "DATABASE" in cleaned_sql.upper())
                        )
                        if is_drop_db:
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
                                clear_session_database(request.session_id, dropped_db, db_tables)

                        # Refresh metadata cache
                        from state.metadata_store import refresh_metadata_after_ddl
                        refresh_metadata_after_ddl(sql, exec_db, op)
                        
                        # Commit state: Clear clarification
                        session["pending_clarification"] = None
                        print("[Phase 8.11] Clarification completed successfully.")
                        print("State committed.")
                        
                        response = ChatResponse(
                            reply="✅ Execution complete.",
                            intent="CONFIRMATION",
                            database=exec_db,
                            sql=sql,
                            valid=True,
                            risk_level="SAFE",
                            requires_confirmation=False,
                            execution=execution_result,
                            refresh_databases=(op == "CREATE DATABASE"),
                            refresh_tables=(op in {"CREATE", "ALTER", "DROP"}),
                            refresh_schema=(op in {"CREATE", "ALTER", "DROP"}),
                        )
                        return response
                    else:
                        # Execution failed: Do NOT clear pending state
                        error = execution_result.get("error", "unknown error")
                        return ChatResponse(
                            reply=f"❌ Execution failed: {error}",
                            intent="CONFIRMATION",
                            database=exec_db,
                            sql=sql,
                            valid=True,
                            risk_level="SAFE",
                            requires_confirmation=False,
                            execution=execution_result
                        )

                # Reconstruct request for other query types
                reconstructed_message = resolve["reconstructed_request"]
                override_db = resolve["override_target_db"]
                
                from agent.agent_coordinator import run as agent_run
                gemini_metrics.start_request(reconstructed_message)
                
                agent_result = agent_run(
                    user_message=reconstructed_message,
                    router_db=override_db,
                    session=session,
                    session_id=request.session_id,
                    history=request.history or [],
                    target_db=override_db,
                )
                
                gemini_metrics.end_request()
                
                execution = agent_result.get("execution")
                success = True
                if execution and not execution.get("success"):
                    success = False
                    
                if success:
                    # Sync DB to session if relevant
                    final_db = agent_result.get("database") or override_db
                    if final_db and final_db in routing_summaries:
                        update_session(request.session_id, selected_database=final_db)
                        
                    # Commit state: Clear clarification
                    session["pending_clarification"] = None
                    print("[Phase 8.11] Clarification completed successfully.")
                    print("State committed.")
                else:
                    print("[Phase 8.11] Clarification execution failed. Retaining pending state.")
                    
                return ChatResponse(
                    reply=agent_result.get("reply", ""),
                    intent=agent_result.get("intent", "UNKNOWN"),
                    database=agent_result.get("database", override_db),
                    sql=agent_result.get("sql"),
                    question=agent_result.get("question"),
                    valid=agent_result.get("valid", True),
                    risk_level=agent_result.get("risk_level", "SAFE"),
                    requires_confirmation=agent_result.get("requires_confirmation", False),
                    blocked_reason=agent_result.get("blocked_reason"),
                    execution=agent_result.get("execution"),
                    refresh_databases=agent_result.get("refresh_databases", False),
                    refresh_tables=agent_result.get("refresh_tables", False),
                    refresh_schema=agent_result.get("refresh_schema", False),
                )
                
            else:
                # Unresolved
                pending["attempts"] += 1
                if pending["attempts"] >= 3:
                    print("[Phase 8.11] Clarification expired after max attempts.")
                    session["pending_clarification"] = None
                    return ChatResponse(
                        reply="I couldn't determine a valid selection. Let's start again.",
                        intent="CANCEL",
                        database=None,
                        valid=True
                    )
                else:
                    print(f"[Phase 8.11] Clarification attempt {pending['attempts']}/3 failed.")
                    # Re-prompt same clarification question
                    return ChatResponse(
                        reply=pending.get("question", "Could you clarify your choice?"),
                        intent="NEEDS_CLARIFICATION",
                        database=pending.get("target_db"),
                        question=pending.get("question"),
                        valid=True
                    )

    # ── Phase 7 / 7.1 / 8.4: AI Database Router with cascading short-circuit ──
    session_db  = session.get("selected_database")
    explicit_db = _extract_explicit_db(request.message, routing_summaries)

    # ── Phase 4.5 Finding 3: Skip Router AI for deterministic requests ────────
    # If the message matches any deterministic dispatch rule (SWITCH_DB, UTILITY,
    # SCHEMA, RAW_SQL, DDL, DML, QUERY), the Router AI call adds zero value.
    # Target DB will be resolved via explicit_db / session / metadata fallbacks.
    _dispatch_tool = _deterministic_dispatch(request.message)
    if _dispatch_tool:
        router_db     = explicit_db or session_db
        router_source = "DETERMINISTIC_BYPASS"
        gemini_metrics.record_bypass("router")
        print(
            f"[Phase 4.5] Router AI skipped — deterministic dispatch to '{_dispatch_tool}'."
        )
    elif explicit_db:
        router_db     = explicit_db
        router_source = "EXPLICIT_PATTERN"
        gemini_metrics.record_bypass("router")
        print(f"[Phase 8.4] Explicit DB pattern match '{explicit_db}' — skipping Router AI.")
    elif session_db and session_db in routing_summaries:
        router_db     = session_db
        router_source = "SESSION_SHORT_CIRCUIT"
        gemini_metrics.record_bypass("router")
        print(f"\n[Phase 7.1] Session short-circuit: '{session_db}' in routing summaries — skipping Router AI.")
    else:
        # Determine Router Trigger Reason
        if not session_db and not explicit_db and not current_db:
            router_trigger_reason = "NO_TARGET_DB"
        elif not session_db:
            router_trigger_reason = "NO_SESSION_DB"
        elif session_db not in routing_summaries:
            router_trigger_reason = "UNKNOWN_DATABASE"
        else:
            router_trigger_reason = "AMBIGUOUS_DATABASE"
            
        print(f"Router Trigger Reason: {router_trigger_reason}")

        router_db = route_to_database(
            question=request.message,
            routing_summaries=routing_summaries,
            session=session,
            history=request.history,
        )
        router_source = "ROUTER_AI"

    target_db = (
        explicit_db
        or router_db
        or session.get("selected_database")
        or current_db
    )

    print("\n====================================")
    print("PHASE 7 / 6.1 EXECUTION CONTEXT")
    print("================================")
    print(f"Router Decision:\n  {router_db or 'None'} (via {router_source})")
    print(f"Frontend Selected Database:\n  {current_db}")
    print(f"Session Memory Database:\n  {session.get('selected_database')}")
    print(f"Initial Target DB:\n  {target_db}")
    print("====================================\n")

    # ── Phase 8: Delegate to Agent Coordinator ────────────────────────────────
    from agent.agent_coordinator import run as agent_run

    agent_result = agent_run(
        user_message=request.message,
        router_db=router_db,
        session=session,
        session_id=request.session_id,
        history=request.history or [],
        target_db=target_db,
    )

    print("\n====================================")
    print("REQUEST GEMINI SUMMARY")
    print("====================================")
    summary = gemini_metrics.get_active_request_summary()
    print(f"Router Calls      : {summary['router']}")
    print(f"Planner Calls     : {summary['planner']}")
    print(f"SQL Calls         : {summary['sql_generator']}")
    print(f"Summary Calls     : {summary['summarizer']}")
    print(f"Report Calls      : {summary['report_formatter']}")
    print(f"Total Calls       : {summary['total']}")
    print(f"Prompt Tokens     : {summary['prompt_tokens']}")
    print(f"Response Tokens   : {summary['response_tokens']}")
    print(f"Total Tokens      : {summary['total_tokens']}")
    print(f"Estimated Cost     : ${summary['estimated_cost']:.5f}")
    print(f"Rate Limit Events : {summary['rate_limit_events']}")
    print(f"Retry Attempts    : {summary['retry_attempts']}")
    print(f"Largest Prompt    : {summary['largest_prompt_label']}")
    print(f"Largest Prompt Tokens: {summary['largest_prompt_tokens']}")
    print("====================================\n")

    print("Prompt Leaderboard")
    for i, (label, tokens) in enumerate(summary["leaderboard"], 1):
        print(f"{i}. {label:<25} {tokens} tokens")
    print("====================================\n")

    gemini_metrics.end_request()

    intent = agent_result.get("intent", "UNKNOWN")

    # ── Handle newly created Needs Clarification ─────────────────────────────
    if intent == "NEEDS_CLARIFICATION" and "clarification_data" in agent_result:
        clar_data = agent_result["clarification_data"]
        clar_data["created_at"] = time.time()
        clar_data["attempts"] = 0
        session["pending_clarification"] = clar_data
        
        print("[Phase 8.11] Clarification created.")
        print(f"Type      : {clar_data.get('type')}")
        print(f"Target DB : {clar_data.get('target_db')}")
        print(f"Attempts  : 0")

    # Update session context if request succeeds
    if agent_result.get("valid", True) and intent != "NEEDS_CLARIFICATION":
        final_db = agent_result.get("database") or target_db
        if final_db and final_db in routing_summaries:
            update_session(request.session_id, selected_database=final_db)

    # ── Map agent result → ChatResponse ──────────────────────────────────────
    response = ChatResponse(
        reply=agent_result.get("reply", ""),
        intent=intent,
        database=agent_result.get("database", target_db),
        sql=agent_result.get("sql"),
        question=agent_result.get("question"),
        valid=agent_result.get("valid", True),
        risk_level=agent_result.get("risk_level", "SAFE"),
        requires_confirmation=agent_result.get("requires_confirmation", False),
        blocked_reason=agent_result.get("blocked_reason"),
        execution=agent_result.get("execution"),
        refresh_databases=agent_result.get("refresh_databases", False),
        refresh_tables=agent_result.get("refresh_tables", False),
        refresh_schema=agent_result.get("refresh_schema", False),
    )

    return response


# ─── Phase 5: /execute-confirmed endpoint (updated) ─────────────────────────

@router.post("/execute-confirmed", response_model=ChatResponse)
async def execute_confirmed_endpoint(request: ExecuteConfirmedRequest):
    """
    Phase 5: Dual-Validation Execution Route.
    Phase 6: Session memory updated ONLY after successful execution.
    Called when a user confirms a HIGH_RISK or CRITICAL_RISK operation.
    """
    meta       = get_metadata()
    raw_schema = meta.get("schema", {})

    # ── Dual Validation (Security Requirement) ────────────────────────────────
    validation = validate_sql(request.intent, request.sql, raw_schema)

    if not validation["valid"]:
        raise HTTPException(
            status_code=400,
            detail=f"Validation failed during execution: {validation.get('failure_reason') or validation.get('blocked_reason')}"
        )

    if validation["risk_level"] == "BLOCKED":
        raise HTTPException(status_code=403, detail="Operation is blocked.")

    # ── Execute ───────────────────────────────────────────────────────────────
    from db.executor import requires_superdb
    target_db = request.database
    if requires_superdb(request.sql):
        target_db = os.getenv("DB_SUPERDB", "postgres")

    if not target_db:
        raise HTTPException(status_code=400, detail="No target database available for execution.")

    execution_result = execute_sql(request.sql, target_db)

    # Refresh metadata cache immediately on successful DDL execution
    if execution_result.get("success"):
        op = execution_result.get("operation", "")
        # Handle DROP DATABASE session cleanup before cache is refreshed/cleared
        from db.executor import _clean_sql
        cleaned_sql = _clean_sql(request.sql)
        is_drop_db = (
            op == "DROP DATABASE"
            or (op == "DROP" and "DATABASE" in cleaned_sql.upper())
        )
        if is_drop_db:
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
                clear_session_database(request.session_id, dropped_db, db_tables)

        from state.metadata_store import refresh_metadata_after_ddl
        refresh_metadata_after_ddl(request.sql, target_db, op)

    # Build a standard ChatResponse containing only the execution result
    response = ChatResponse(
        reply="Execution complete.",
        intent=request.intent,
        database=request.database,
        sql=request.sql,
        valid=validation["valid"],
        risk_level=validation["risk_level"],
        requires_confirmation=False,
        execution=execution_result
    )

    _apply_refresh_flags(response, execution_result)

    # ── Phase 6: Update session ONLY after successful execution ─────────────
    _update_session_from_execution(
        request.session_id, request.intent, request.sql, execution_result
    )

    return response
