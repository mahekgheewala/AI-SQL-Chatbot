import os
import re
import time
from typing import Optional, Any
from fastapi import APIRouter, HTTPException, Depends
from models.schemas import ChatRequest, ChatResponse, ExecuteConfirmedRequest
from state.metadata_store import get_metadata, refresh_routing_summaries  # Phase 7
from state.session_store import get_session, update_session, log_session_state  # Phase 6
from validation.sql_validator import validate as validate_sql
from db.executor import execute_sql  # Phase 5 Executor (kept for /execute-confirmed)
from utils.config_loader import get_superdb_name
from agent import gemini_metrics  # Phase 8.5: Gemini call instrumentation
from agent.typo_intent_layer import process as _typo_intent_process  # Phase 1: Typo & Intent Layer
from auth.dependencies import get_current_user
from utils.logging_config import (
    session_id_var,
    user_id_var,
    database_name_var,
    selected_table_var,
    metadata_version_var,
    logger_audit
)

router = APIRouter()


def _get_default_database(user_id: int) -> Optional[str]:
    """The database this user connected via the setup wizard, if any."""
    from db.app_database import SessionLocal
    from services.connection_service import ConnectionService

    db_session = SessionLocal()
    try:
        conn_model = ConnectionService(db_session).get_user_connection(user_id)
        return conn_model.default_database if conn_model else None
    finally:
        db_session.close()


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


def _process_sql_execution_result(
    agent_result: dict,
    session: dict,
    session_id: str,
    classification: dict,
    selected_pipeline_name: str,
    execution_path: str,
    target_db: str
) -> Optional[Any]:
    execution = agent_result.get("execution")
    if not (execution and isinstance(execution, dict) and execution.get("success")):
        return None
        
    # Check cache first if agent_result indicates cached run
    if agent_result.get("cached"):
        from analytics import AnalyticsEngine
        cached = AnalyticsEngine.get_cached_result(session_id)
        if cached:
            print("[Result Processor] Reusing cached ProcessedResult object.")
            return cached
        
    from agent.result_processing import ResultProcessor
    
    # Process result
    processed_result = ResultProcessor.process(
        columns=execution.get("columns", []),
        rows=execution.get("rows", []),
        sql=agent_result.get("sql") or execution.get("sql"),
        dbname=agent_result.get("database") or target_db,
        classification=classification.get("category"),
        selected_pipeline=selected_pipeline_name,
        execution_path=execution_path,
        column_types=execution.get("column_types")
    )
    
    # 1. Human-Readable Console Output (Uvicorn Logs)
    print("\n====================================")
    print("RESULT PROCESSOR")
    print("====================================")
    print("Dataset Profile Started")
    for step_name, step_ms in processed_result.processing.step_durations.items():
        print(f"  {step_name:<28} {step_ms:.2f} ms")
    print(f"Result Processor Completed:   {processed_result.processing.processing_time_ms:.2f} ms")
    
    print("\nDataset Summary:")
    print(f"  Rows:                       {processed_result.execution.row_count}")
    print(f"  Columns:                    {processed_result.execution.column_count}")
    
    num_cols = ", ".join(processed_result.semantics.numeric_columns)
    print(f"  Numeric Columns:            {num_cols if num_cols else 'None'}")
    
    cat_cols = ", ".join(processed_result.semantics.categorical_columns)
    print(f"  Categorical Columns:        {cat_cols if cat_cols else 'None'}")
    
    dt_cols = ", ".join(processed_result.semantics.datetime_columns)
    print(f"  Datetime Columns:           {dt_cols if dt_cols else 'None'}")
    
    bool_cols = ", ".join(processed_result.semantics.boolean_columns)
    print(f"  Boolean Columns:            {bool_cols if bool_cols else 'None'}")
    
    # Memory formatting
    mem = processed_result.processing.memory_usage_bytes
    if mem < 1024:
        mem_str = f"{mem} B"
    elif mem < 1024 * 1024:
        mem_str = f"{mem / 1024:.2f} KB"
    else:
        mem_str = f"{mem / (1024 * 1024):.2f} MB"
    print(f"  Memory Usage:               {mem_str}")
    print("====================================\n")
    
    # 2. Structured JSON Log (logs/app.log)
    from utils.logging_config import logger_query
    try:
        logger_query.info(
            "SQL result processing completed",
            extra={
                "category": "query",
                "operation_type": "RESULT_PROCESSING",
                "database_name": processed_result.execution.database_name,
                "dataframe_rows": processed_result.execution.row_count,
                "dataframe_columns": processed_result.execution.column_count,
                "numeric_columns": processed_result.semantics.numeric_columns,
                "categorical_columns": processed_result.semantics.categorical_columns,
                "datetime_columns": processed_result.semantics.datetime_columns,
                "boolean_columns": processed_result.semantics.boolean_columns,
                "statistics_status": processed_result.statistics.status,
                "processing_time_ms": processed_result.processing.processing_time_ms,
                "rows": processed_result.execution.row_count,
                "columns": processed_result.execution.column_count,
                "dataset_empty": processed_result.profile.dataset_empty,
                "numeric_column_count": processed_result.profile.numeric_column_count,
                "categorical_column_count": processed_result.profile.categorical_column_count,
                "statistics_generated": processed_result.statistics.status in {"IMMEDIATE", "COMPUTED"},
                "memory_usage_bytes": processed_result.processing.memory_usage_bytes,
                "warnings": processed_result.processing.warnings
            }
        )
    except Exception:
        pass
        
    # 3. Save lightweight metadata in session memory
    session["last_dataset_metadata"] = {
        "columns": processed_result.dataset.columns,
        "row_count": processed_result.execution.row_count,
        "numeric_columns": processed_result.semantics.numeric_columns,
        "categorical_columns": processed_result.semantics.categorical_columns,
        "datetime_columns": processed_result.semantics.datetime_columns,
        "boolean_columns": processed_result.semantics.boolean_columns,
        "timestamp": time.time()
    }
    
    # Cache the source result if it's an unaggregated SELECT query
    from analytics.engine import AnalyticsEngine
    from analytics.helpers import is_unaggregated_select, extract_tables_from_select, get_sql_fingerprint
    
    sql_str = processed_result.execution.sql or ""
    if sql_str and is_unaggregated_select(sql_str):
        source_tables = extract_tables_from_select(sql_str)
        exec_mode = agent_result.get("execution_mode", "STANDARD_SQL")
        db_name = processed_result.execution.database_name or target_db
        fingerprint = get_sql_fingerprint(sql_str)
        
        AnalyticsEngine.cache_source(
            session_id=session_id,
            result=processed_result,
            source_tables=source_tables,
            db_name=db_name,
            sql_fingerprint=fingerprint,
            execution_mode=exec_mode
        )
    
    return processed_result



_QUESTION_INTERRUPTION_PATTERN = re.compile(
    r"^\s*(what|where|how|why|which|in\s+which|is|are|can|could|would|will|does|do)\b",
    re.IGNORECASE
)

# ─── Shared pending-clarification helpers (Stage 3: shared module) ────────────
from agent.pending_resolution import (  # noqa: E402
    is_new_command_detected,
    split_column_definitions,
    is_valid_column_definition,
)


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
            clear_session_database(session_id, dropped_db, db_tables)


# ─── Phase 8: /chat endpoint ─────────────────────────────────────────────────


# ─── Clarification resolution imported from shared module (Stage 3) ────────────
from agent.pending_resolution import (  # noqa: E402
    _print_clarification_trace,
    resolve_pending_clarification,
    _build_db_resolution_result,
    _build_table_resolution_result,
)


# ──────────────────────────────────────────────────────────────────────────────
# resolve_pending_clarification, _print_clarification_trace,
# _build_db_resolution_result, and _build_table_resolution_result have been
# moved to agent/pending_resolution.py (Stage 3).
# They are imported above.
# ──────────────────────────────────────────────────────────────────────────────
"""
[REMOVED: resolve_pending_clarification_OLD body — see agent/pending_resolution.py]
"""
# ─── Phase 8: /chat endpoint ─────────────────────────────────────────────────

def _resolve_chat_session(user_id: int, session_id: Optional[str]) -> str:
    """
    Phase 9.5 — Server-controlled chat sessions.

    Resolves and validates the chat session id for the authenticated user:
      * If the client omits a session id, the server generates one.
      * A new session is persisted (ChatSession row) so it is server-owned,
        survives restarts, and can be revoked on logout / password change.
      * A session id owned by another user, deactivated, or expired is rejected.

    Returns the validated session id, raising HTTPException otherwise.
    """
    from datetime import datetime
    from db.app_database import SessionLocal
    from repositories.chat_session_repository import ChatSessionRepository

    if not session_id:
        import uuid
        session_id = uuid.uuid4().hex

    db = SessionLocal()
    try:
        repo = ChatSessionRepository(db)
        chat_session = repo.get_by_session_id(session_id)
        if chat_session is None:
            repo.create(session_id, user_id)
            return session_id

        if chat_session.user_id != user_id:
            raise HTTPException(status_code=403, detail="Invalid session")
        if not chat_session.is_active:
            raise HTTPException(
                status_code=403,
                detail="This conversation has ended. Please start a new one.",
            )
        if chat_session.expires_at is not None and chat_session.expires_at < datetime.utcnow():
            raise HTTPException(
                status_code=403,
                detail="This conversation has expired. Please start a new one.",
            )
        repo.touch(chat_session)
        return session_id
    finally:
        db.close()


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, current_user: Any = Depends(get_current_user)):
    from state.metadata_store import get_cache_generation

    # Phase 9.5: Enforce server-side session ownership before any work happens.
    request.session_id = _resolve_chat_session(current_user.id, request.session_id)

    # Bind request-level context variables
    session_token = session_id_var.set(request.session_id)
    user_id_token = user_id_var.set(current_user.id)
    
    # Load session to get selected database/table if any
    session = get_session(request.session_id)
    log_session_state(request.session_id)

    initial_db = session.get("selected_database")
    # If nothing has been explicitly selected/switched to yet this session,
    # default to the database the user actually connected (their
    # PostgresConnection.default_database) rather than leaving every
    # ambiguous request ("show me all tables") to fall through to a
    # clarification prompt or an empty result.
    if not initial_db:
        initial_db = _get_default_database(current_user.id)
        if initial_db:
            update_session(request.session_id, selected_database=initial_db)
            session["selected_database"] = initial_db
    initial_table = session.get("selected_table")

    db_token = database_name_var.set(initial_db)
    table_token = selected_table_var.set(initial_table)
    mv_token = metadata_version_var.set(get_cache_generation())

    try:
        _cleaned_message, _intent_meta = _typo_intent_process(request.message)
        session["last_intent_meta"] = _intent_meta

        # ── Phase 8.5: Start metrics request tracking ───────────────────────────
        gemini_metrics.start_request(_cleaned_message)

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

        # ── Pending operation state removed (Stage 4) ───────────────────────────
        # The pending_operation block was a legacy duplicate of pending_clarification
        # with hardcoded type-inference heuristics. All pending clarification handling
        # is now owned by the Universal Gateway via decide().

        # ── Pending clarification block removed (Stage 4) ───────────────────────
        # All pending clarification resolution, timeout, new-command detection,
        # CONFIRMATION execution, and re-routing is now owned by the Universal
        # Gateway via decide().

        # ── Single Authoritative Gateway Call ───────────────────────────────────
        # The Universal Semantic Gateway is the ONE entry point for understanding,
        # context resolution (pending clarification), and routing. No downstream
        # component re-classifies or re-routes.
        from agent.universal_gateway import decide
        session_db = session.get("selected_database")
        explicit_db = _extract_explicit_db(_cleaned_message, routing_summaries)

        decision = decide(
            user_message=_cleaned_message,
            raw_message=request.message,
            database_context=explicit_db or session_db,
            session=session,
            request_db=getattr(request, "database", None),
        )

        intent = decision.understanding.intent
        confidence = decision.understanding.confidence
        target_db = decision.target_db
        database_name_var.set(target_db)

        if decision.understanding.source == "DETERMINISTIC":
            gemini_metrics.record_bypass("intent_classifier")
            gemini_metrics.record_bypass("router")

        # Print gateway decision trace
        print("\n====================================")
        print("GATEWAY DECISION")
        print("====================================")
        print(f"Route      : {decision.route}")
        print(f"Intent     : {intent}")
        print(f"Target DB  : {target_db}")
        print(f"Table      : {decision.target_table}")
        print(f"Tool       : {decision.tool_name}")
        print(f"Clarif.    : {decision.clarification_type}")
        print(f"Reason     : {decision.reason}")
        print("====================================\n")

        # ── Route: CLARIFICATION ───────────────────────────────────────────────
        # The gateway determined the message needs clarification.
        # Store pending state and return the clarification question.
        if decision.route == "CLARIFICATION":
            if decision.clarification_type == "UNRECOGNIZED_QUERY":
                return ChatResponse(
                    reply="I couldn't safely understand this request. Please rephrase your query.",
                    intent="UNDERSTANDING_FAILED",
                    database=session_db,
                    valid=False
                )

            # session["pending_clarification"] becomes the authoritative
            # pending state below either way — a stale session["semantic_pending_frame"]
            # left over from an earlier turn must not linger once it does (see
            # the identical note at the other place this pending state gets
            # set, further down in this function), or the next unrelated
            # message can get silently reinterpreted as more input for an
            # already-finished clarification.
            session["semantic_pending_frame"] = None
            if decision.clarification_data:
                # Frame-based clarification (agent.semantic_frame) — carries the
                # richer options/metadata shape pending_resolution.py's resolver
                # needs (e.g. real database/table candidate lists).
                pending_state = dict(decision.clarification_data)
                pending_state["created_at"] = time.time()
                pending_state.setdefault("attempts", 0)
                session["pending_clarification"] = pending_state
            else:
                session["pending_clarification"] = {
                    "type": decision.clarification_type,
                    "original_request": request.message,
                    "target_db": target_db,
                    "created_at": time.time(),
                    "attempts": 0,
                    "table_name": decision.target_table
                }
            reply_text = decision.clarification_message
            if not reply_text:
                if decision.target_table:
                    reply_text = f"Which database contains table **{decision.target_table}**? Please select a database."
                else:
                    reply_text = "Could you please clarify your request?"

            return ChatResponse(
                reply=reply_text,
                intent="NEEDS_CLARIFICATION",
                database=session_db,
                question=reply_text,
                valid=True
            )

        # ── Route: CONFIRMATION ────────────────────────────────────────────────
        # The gateway resolved a pending clarification and returned SQL to execute.
        # This covers:
        #   - CONFIRMATION: user confirmed a DDL/DML operation
        #   - CREATE_TABLE_COLUMNS: user provided column definitions for a pending
        #     CREATE TABLE — the gateway produced the full CREATE TABLE SQL.
        if decision.route == "CONFIRMATION":
            clar_data = decision.clarification_data or {}
            sql = clar_data.get("sql")
            exec_db = clar_data.get("target_db") or target_db

            if not sql:
                return ChatResponse(
                    reply="No SQL to execute from the confirmation.",
                    intent="CONFIRMATION",
                    database=exec_db,
                    valid=False
                )

            # Re-validate before executing — this is the resolved output of a
            # clarification, not a re-classified message, so it never passed
            # through the 3-gate validation pipeline any other way (mirrors
            # /execute-confirmed's dual-validation, which this route lacked).
            confirm_intent = clar_data.get("intent")
            confirm_meta = get_metadata()
            confirm_validation = validate_sql(confirm_intent, sql, confirm_meta.get("schema", {}))
            if not confirm_validation["valid"]:
                try:
                    logger_audit.warning(
                        f"Validation failed during CONFIRMATION execution: "
                        f"{confirm_validation.get('failure_reason') or confirm_validation.get('blocked_reason')}",
                        extra={
                            "category": "audit",
                            "operation_type": "CONFIRM_EXECUTE_VALIDATION_FAILURE",
                            "intent": confirm_intent,
                            "sql": sql,
                            "success": False,
                        }
                    )
                except Exception:
                    pass
                return ChatResponse(
                    reply=f"❌ Validation failed: {confirm_validation.get('failure_reason') or confirm_validation.get('blocked_reason')}",
                    intent="CONFIRMATION",
                    database=exec_db,
                    valid=False,
                    risk_level=confirm_validation.get("risk_level"),
                )

            from db.executor import execute_sql as db_execute_sql, requires_superdb
            if requires_superdb(sql):
                exec_db = get_superdb_name()

            database_name_var.set(exec_db)
            execution_result = db_execute_sql(sql, exec_db)

            if execution_result.get("success"):
                op = execution_result.get("operation", "")
                _update_session_from_execution(request.session_id, "CONFIRMATION", sql, execution_result)

                # Refresh metadata cache
                from state.metadata_store import refresh_metadata_after_ddl
                refresh_metadata_after_ddl(sql, exec_db, op)

                # Commit state: Clear clarification
                session["pending_clarification"] = None
                print("[Stage 4] CONFIRMATION executed successfully.")
                try:
                    logger_audit.info(
                        "CONFIRMATION executed successfully",
                        extra={
                            "category": "audit",
                            "operation_type": "CONFIRMATION_EXECUTED",
                            "clarification_type": decision.clarification_type,
                            "session_id": request.session_id,
                            "sql": sql,
                            "database_name": exec_db
                        }
                    )
                except Exception:
                    pass

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
                error = execution_result.get("error", "unknown error")
                try:
                    logger_audit.error(
                        f"CONFIRMATION execution failed: {error}",
                        extra={
                            "category": "audit",
                            "operation_type": "CONFIRMATION_FAILED",
                            "clarification_type": decision.clarification_type,
                            "session_id": request.session_id,
                            "sql": sql,
                            "database_name": exec_db,
                            "error": error
                        }
                    )
                except Exception:
                    pass
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

        # ── Route: CONVERSATION ────────────────────────────────────────────────
        if decision.route == "CONVERSATION":
            from agent.handlers import get_handler
            handler = get_handler(intent)
            start_time = time.perf_counter()
            agent_result = handler.handle(
                message=_cleaned_message,
                metadata=decision.understanding.model_dump(),
                target_db=None,
                router_db=None,
                session=session,
                session_id=request.session_id,
                history=request.history or [],
            )
            gemini_metrics.end_request()
            return ChatResponse(
                reply=agent_result.get("reply", ""),
                intent=intent,
                database=None,
                sql=None,
                question=None,
                valid=agent_result.get("valid", True),
                execution=None,
            )

        # ── Route: DIRECT / AI_PLANNER / RAW_SQL ───────────────────────────────
        # Build intent metadata for handler dispatch. The handler reads route
        # from metadata to decide DIRECT vs AI_PLANNER execution path.
        intent_meta = decision.understanding.model_dump()
        intent_meta["target_db"] = target_db
        intent_meta["target_table"] = decision.target_table
        intent_meta["route"] = decision.route

        # ── CONNECTION GUARD ───────────────────────────────────────────────────
        try:
            from connections.connection_manager import ConnectionManager
            from db.app_database import SessionLocal as _GuardSessionLocal
            _guard_db = _GuardSessionLocal()
            try:
                ConnectionManager.get_connection(current_user.id, _guard_db)
                print(f"[Connection Guard] Active connection confirmed for user {current_user.id}.")
            except ValueError as _conn_err:
                print(f"[Connection Guard] FAILED for user {current_user.id}: {_conn_err}")
                gemini_metrics.end_request()
                return ChatResponse(
                    reply="No active database session. Please reconnect before running queries.",
                    intent="NO_CONNECTION",
                    database=None,
                    valid=False,
                )
            finally:
                _guard_db.close()
        except Exception as _guard_exc:
            print(f"[Connection Guard] Unexpected error during guard check: {_guard_exc}. Continuing.")

        # Execute route via handler dispatch
        from agent.handlers import get_handler
        handler = get_handler(intent)
        pipeline_start_time = time.perf_counter()
        agent_result = handler.handle(
            message=_cleaned_message,
            metadata=intent_meta,
            target_db=target_db,
            router_db=target_db,
            session=session,
            session_id=request.session_id,
            history=request.history or [],
            decision=decision,
        )

        gemini_metrics.end_request()
        pipeline_duration_ms = (time.perf_counter() - pipeline_start_time) * 1000
        intent = agent_result.get("intent", "UNKNOWN")

        # Print REQUEST SUMMARY
        print("\n====================================")
        print("REQUEST SUMMARY")
        print("====================================")
        print(f"User Intent:\n  {decision.understanding.intent}")
        print(f"Execution Intent:\n  {intent}")
        print(f"Route:\n  {decision.route}")
        print(f"Execution Time:\n  {pipeline_duration_ms:.2f} ms")
        print("====================================\n")

        # ── Handle newly created Needs Clarification / Confirmation ──────────────
        # A HIGH_RISK/CRITICAL_RISK operation ("Type CONFIRM to proceed") keeps
        # `intent` as the actual operation (e.g. "DROP_TABLE") — not
        # "NEEDS_CLARIFICATION" — even though it carries clarification_data and
        # requires_confirmation=True. Without also checking that flag here, the
        # pending state this needs for later resolution is never persisted, and
        # typing "confirm" as a follow-up chat message can never work (the
        # dedicated Confirm button bypasses this entirely via /execute-confirmed,
        # which is why that path has always worked while this one silently
        # hasn't).
        if intent == "NEEDS_CLARIFICATION" or agent_result.get("requires_confirmation"):
            if "clarification_data" in agent_result:
                clar_data = agent_result["clarification_data"]
                clar_data["created_at"] = time.time()
                clar_data["attempts"] = 0
                session["pending_clarification"] = clar_data
                # session["pending_clarification"] is now the single
                # authoritative pending state going forward (universal_gateway
                # checks it first, before ever re-interpreting a raw message).
                # A stale semantic_pending_frame left over from this same turn
                # (or an earlier one) must not linger — it only gets consulted
                # when a message is interpreted fresh, which won't happen again
                # while pending_clarification exists, but WOULD happen the next
                # time it doesn't (e.g. once this clarification resolves) —
                # and a leftover frame there gets treated as still-pending,
                # hijacking the next unrelated message.
                session["semantic_pending_frame"] = None

                print("[Stage 4] Clarification created.")
                print(f"Type      : {clar_data.get('type')}")
                print(f"Target DB : {clar_data.get('target_db')}")

                try:
                    logger_audit.info(
                        f"Needs clarification triggered: {clar_data.get('type')}",
                        extra={
                            "category": "audit",
                            "operation_type": "CLARIFICATION_CREATED",
                            "clarification_type": clar_data.get("type"),
                            "session_id": request.session_id,
                            "target_db": clar_data.get("target_db"),
                            "question": clar_data.get("question")
                        }
                    )
                except Exception:
                    pass

        # Update session context if request succeeds
        execution = agent_result.get("execution")
        execution_success = execution.get("success", True) if execution else True

        processed_res = None
        visualization_result = None
        if agent_result.get("valid", True) and intent != "NEEDS_CLARIFICATION":
            if execution_success:
                final_db = agent_result.get("database") or target_db
                kwargs = {}
                if final_db and final_db in routing_summaries:
                    kwargs["selected_database"] = final_db

                if intent not in {"NEEDS_CLARIFICATION", "UNKNOWN", "ERROR", "RATE_LIMITED", "CANCEL"}:
                    kwargs["last_successful_intent"] = intent
                    kwargs["last_user_intent"] = decision.understanding.intent
                    kwargs["last_execution_intent"] = intent

                if kwargs:
                    update_session(request.session_id, **kwargs)
                    selected_table_var.set(session.get("selected_table"))

                # Process raw SQL execution result through centralized Result Processor
                processed_res = _process_sql_execution_result(
                    agent_result=agent_result,
                    session=session,
                    session_id=request.session_id,
                    classification={"category": intent, "pipeline": "STANDARD", "source": decision.understanding.source, "user_intent": decision.understanding.intent},
                    selected_pipeline_name="STANDARD",
                    execution_path=f"STANDARD -> HANDLER -> {intent}",
                    target_db=target_db
                )

                # The gateway's own grounded frame — not agent_result["intent"],
                # which reflects the underlying SQL's shape (e.g. "QUERY" for
                # the SELECT a chart's data fetch produces) — is the source of
                # truth for "this was a chart request".
                _frame = getattr(decision, "semantic_frame", None)
                if processed_res is not None and _frame is not None and _frame.capability_id == "visualize":
                    try:
                        from visualization.engine import VisualizationEngine
                        viz = VisualizationEngine.generate(
                            processed_result=processed_res,
                            user_message=request.message,
                            session_id=request.session_id,
                        )
                        visualization_result = viz.model_dump()
                    except Exception as _viz_exc:
                        print(f"[Visualization] Chart generation failed: {_viz_exc}")

            # Save classification context in session memory
            msg_count = session.get("message_count", 0) + 1
            session["message_count"] = msg_count
            session["last_classification"] = {
                "category": intent,
                "pipeline": "STANDARD",
                "presentation": "TABLE",
                "timestamp": time.time(),
                "sequence_id": msg_count
            }

        execution_payload = agent_result.get("execution")
        if processed_res:
            from models.schemas import ExecutionResult
            columns_to_return = processed_res.dataset.columns
            rows_to_return = processed_res.dataset.dataframe.values.tolist() if processed_res.dataset.dataframe is not None else []

            execution_payload = ExecutionResult(
                success=True,
                operation=agent_result.get("execution", {}).get("operation") or "SELECT",
                columns=columns_to_return,
                rows=rows_to_return,
                row_count=len(rows_to_return),
                message=agent_result.get("execution", {}).get("message") or "Query executed successfully.",
                error=agent_result.get("execution", {}).get("error")
            )

        response = ChatResponse(
            reply=agent_result.get("reply", ""),
            intent=intent,
            database=session.get("selected_database") if session.get("selected_database") is not None else (agent_result.get("database") or target_db),
            sql=agent_result.get("sql"),
            question=agent_result.get("question"),
            valid=agent_result.get("valid", True),
            risk_level=agent_result.get("risk_level", "SAFE"),
            requires_confirmation=agent_result.get("requires_confirmation", False),
            blocked_reason=agent_result.get("blocked_reason"),
            execution=execution_payload,
            refresh_databases=agent_result.get("refresh_databases", False),
            refresh_tables=agent_result.get("refresh_tables", False),
            refresh_schema=agent_result.get("refresh_schema", False),
            visualization=visualization_result,
        )

        return response

    finally:
        session_id_var.reset(session_token)
        database_name_var.reset(db_token)
        selected_table_var.reset(table_token)
        metadata_version_var.reset(mv_token)
        gemini_metrics.reset_active_request()


# ─── Phase 5: /execute-confirmed endpoint (updated) ─────────────────────────

@router.post("/execute-confirmed", response_model=ChatResponse)
async def execute_confirmed(request: ExecuteConfirmedRequest, current_user: Any = Depends(get_current_user)):
    """
    Phase 5: Dual-Validation Execution Route.
    Phase 6: Session memory updated ONLY after successful execution.
    Called when a user confirms a HIGH_RISK or CRITICAL_RISK operation.
    """
    from state.metadata_store import get_cache_generation

    # Phase 9.5: Enforce server-side session ownership before any work happens.
    request.session_id = _resolve_chat_session(current_user.id, request.session_id)

    session_token = session_id_var.set(request.session_id)
    db_token = database_name_var.set(request.database)
    mv_token = metadata_version_var.set(get_cache_generation())
    
    try:
        meta       = get_metadata()
        raw_schema = meta.get("schema", {})

        # ── Dual Validation (Security Requirement) ────────────────────────────────
        validation = validate_sql(request.intent, request.sql, raw_schema)

        if not validation["valid"]:
            try:
                logger_audit.warning(
                    f"Validation failed during execution confirmation: {validation.get('failure_reason') or validation.get('blocked_reason')}",
                    extra={
                        "category": "audit",
                        "operation_type": "CONFIRM_EXECUTE_VALIDATION_FAILURE",
                        "intent": request.intent,
                        "sql": request.sql,
                        "success": False
                    }
                )
            except Exception:
                pass
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
            target_db = get_superdb_name()

        if not target_db:
            raise HTTPException(status_code=400, detail="No target database available for execution.")

        database_name_var.set(target_db)

        # Audit log confirmation start
        try:
            logger_audit.info(
                f"Confirmation execution started on database '{target_db}'",
                extra={
                    "category": "audit",
                    "operation_type": "CONFIRM_EXECUTION_START",
                    "action": "execute_confirmed",
                    "intent": request.intent,
                    "sql": request.sql,
                    "database_name": target_db
                }
            )
        except Exception:
            pass

        execution_result = execute_sql(request.sql, target_db, intent=request.intent)

        # Refresh metadata cache immediately on successful DDL execution
        if execution_result.get("success"):
            try:
                logger_audit.info(
                    f"Confirmation execution succeeded on database '{target_db}'",
                    extra={
                        "category": "audit",
                        "operation_type": "CONFIRM_EXECUTION_SUCCESS",
                        "action": "execute_confirmed",
                        "intent": request.intent,
                        "sql": request.sql,
                        "database_name": target_db
                    }
                )
            except Exception:
                pass

            op = execution_result.get("operation", "")
            # Session cleanup (selected table/database tracking, DROP TABLE /
            # RENAME TABLE / DROP DATABASE cleanup) before cache is refreshed/cleared
            _update_session_from_execution(request.session_id, request.intent, request.sql, execution_result)

            from state.metadata_store import refresh_metadata_after_ddl
            refresh_metadata_after_ddl(request.sql, target_db, op)
        else:
            try:
                logger_audit.error(
                    f"Confirmation execution failed on database '{target_db}': {execution_result.get('error')}",
                    extra={
                        "category": "audit",
                        "operation_type": "CONFIRM_EXECUTION_FAILED",
                        "action": "execute_confirmed",
                        "intent": request.intent,
                        "sql": request.sql,
                        "database_name": target_db,
                        "error": execution_result.get("error")
                    }
                )
            except Exception:
                pass

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
    finally:
        session_id_var.reset(session_token)
        database_name_var.reset(db_token)
        metadata_version_var.reset(mv_token)
