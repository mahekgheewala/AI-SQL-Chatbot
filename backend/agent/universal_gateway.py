"""
Universal Semantic Gateway — Mandatory Architectural Choke Point
==================================================================
Every user message MUST pass through this module before any specialized detector,
intent-specific parser, routing decision, clarification handler, or execution tool
is selected.

Pipeline:
USER MESSAGE -> PREPROCESSING -> UNIVERSAL SEMANTIC GATEWAY (agent.semantic_frame) -> ROUTING -> GatewayDecision

This module is the SINGLE source of truth for both understanding AND routing.
The `decide()` function produces a GatewayDecision that carries understanding + route +
clarification state. No downstream component re-classifies or re-routes.
"""

import logging
from typing import Optional, Dict, Any
from models.schemas import UnderstandingResult, GatewayDecision, RoutingDecision
from utils.logging_config import logger_ai

_logger = logging.getLogger("app.ai.gateway")

def process_universal_semantic_gateway(
    user_message: str,
    raw_message: str,
    database_context: Optional[str] = None,
    session: Optional[Dict[str, Any]] = None
) -> UnderstandingResult:
    """
    Mandatory Architectural Entry Point for ALL user messages.

    Runs agent.semantic_frame.interpret_message() — the single NL-understanding
    engine — exactly once and adapts its result into the legacy
    UnderstandingResult/QueryIntent shape the rest of the pipeline (routing
    policy, handlers, the deterministic SQL builder) already consumes.
    """
    logger_ai.info(f"[GATEWAY] SEMANTIC_GATEWAY_ENTERED: '{user_message}'")
    print(f"\n====================================")
    print(f"UNIVERSAL SEMANTIC GATEWAY ENTERED")
    print(f"Message: {repr(user_message)}")
    print(f"====================================\n")

    cleaned_msg = user_message.strip()
    session = session if session is not None else {}

    from agent import semantic_frame
    from agent.pending_resolution import is_new_command_detected
    from state.metadata_store import get_metadata

    # A fresh command supersedes any pending semantic clarification.
    if is_new_command_detected(cleaned_msg):
        session["semantic_pending_frame"] = None

    meta = get_metadata()
    active_db = database_context or meta.get("selected_db") or session.get("selected_database")
    grounding_meta = semantic_frame.build_grounding_metadata(meta, active_db)
    context = semantic_frame.build_context(session, active_db)

    frame_result = semantic_frame.interpret_message(cleaned_msg, grounding_meta, context)
    frame = frame_result.frame

    # Persist prior frames for cross-turn reference resolution.
    if frame.capability_id not in ("understanding_failed", "general_conversation"):
        prior = list(session.get("semantic_prior_frames") or [])
        session["semantic_prior_frames"] = (prior + [frame])[-10:]

    if frame_result.status == semantic_frame.STATUS_CLARIFY:
        session["semantic_pending_frame"] = frame
    elif frame_result.status == semantic_frame.STATUS_SUCCESS:
        session["semantic_pending_frame"] = None

    result = semantic_frame.frame_result_to_understanding(frame_result, raw_message, cleaned_msg)

    logger_ai.info(f"[GATEWAY] SEMANTIC_RESULT_CREATED: intent={result.intent}, confidence={result.confidence}")
    print(f"[GATEWAY] SEMANTIC_RESULT_CREATED: intent={result.intent}, confidence={result.confidence}")
    return result


# ─── Stage 2: Route Resolution (absorbed from query_router.py) ───────────────
# The gateway is the SINGLE source of truth for both understanding AND routing.
# query_router.route_query() now delegates to _resolve_route().

# Intents that do NOT require a live database connection to execute.
_NO_DB_INTENTS = frozenset({
    "GENERAL_CONVERSATION", "KNOWLEDGE", "LIST_DATABASES",
    "DATABASE_SWITCH", "CREATE_DATABASE", "DROP_DATABASE",
})
_NO_DB_TOOLS = frozenset({
    "list_databases", "switch_database", "create_database", "drop_database",
})


def _requires_db_connection(route: str, intent: str, tool_name: Optional[str]) -> bool:
    """Determine whether a route requires an active database connection."""
    if route in ("CONVERSATION", "CLARIFICATION"):
        return False
    if intent in _NO_DB_INTENTS:
        return False
    if tool_name and tool_name in _NO_DB_TOOLS:
        return False
    return True


def _resolve_route(
    understanding: UnderstandingResult,
    session: dict,
    request_db: Optional[str] = None,
) -> RoutingDecision:
    """Route policy: given an UnderstandingResult, decide where it goes.

    This is the EXACT logic from query_router.route_query(), now authoritative
    inside the gateway. query_router.route_query() delegates here.
    """
    msg_normalized = understanding.normalized_input or understanding.raw_input

    # ── 4-Level Database Priority Resolution ──────────────────────────────────
    meta_summaries: dict = {}
    current_db: Optional[str] = None
    try:
        from state.metadata_store import get_metadata
        meta = get_metadata()
        current_db = meta.get("selected_db")
        meta_summaries = meta.get("routing_summaries", {})
    except Exception:
        pass

    session_db = session.get("selected_database") if session else None

    resolved_db = request_db
    resolve_reason = "EXPLICIT_REQUEST_DB"

    if not resolved_db and understanding.database:
        resolved_db = understanding.database
        resolve_reason = "PHASE1_EXTRACTED_DB"

    if not resolved_db and session_db:
        resolved_db = session_db
        resolve_reason = "SESSION_DB"

    if not resolved_db and current_db:
        resolved_db = current_db
        resolve_reason = "GLOBAL_SELECTED_DB"

    # ── Terminal Failure Check ────────────────────────────────────────────────
    if understanding.status == "UNDERSTANDING_FAILED":
        return RoutingDecision(
            route="CLARIFICATION",
            intent="UNKNOWN",
            target_db=resolved_db,
            needs_clarification=True,
            clarification_type="UNRECOGNIZED_QUERY",
            reason="Phase 1 semantic role parser could not safely structure query."
        )

    # Multi-database table ambiguity check
    needs_clarification = understanding.needs_clarification
    clarification_type = understanding.clarification_type
    target_table = understanding.table

    if target_table and not resolved_db and meta_summaries:
        tbl_lower = target_table.lower()
        matching_dbs = [
            db_name for db_name, tbl_list in meta_summaries.items()
            if tbl_lower in [t.lower() for t in tbl_list]
        ]
        if len(matching_dbs) > 1:
            needs_clarification = True
            clarification_type = "AMBIGUOUS_TABLE"
        elif len(matching_dbs) == 1:
            resolved_db = matching_dbs[0]
            resolve_reason = "SINGLE_MATCHING_DB"

    # ── Infer clarification_type when DDL parser flagged need but didn't name it
    if needs_clarification and not clarification_type:
        intent_upper = (understanding.intent or "").upper()
        if intent_upper == "CREATE_TABLE" and target_table:
            clarification_type = "CREATE_TABLE_COLUMNS"

    # ── Route Policy Decision ─────────────────────────────────────────────────
    if needs_clarification:
        return RoutingDecision(
            route="CLARIFICATION",
            intent=understanding.intent,
            target_db=resolved_db,
            target_table=target_table,
            target_columns=understanding.columns,
            needs_clarification=True,
            clarification_type=clarification_type or "AMBIGUOUS_TABLE",
            reason=f"Ambiguity detected for table '{target_table}' across multiple databases."
        )

    if understanding.intent in {"GENERAL_CONVERSATION", "KNOWLEDGE"}:
        return RoutingDecision(
            route="CONVERSATION",
            intent=understanding.intent,
            target_db=None,
            confidence=understanding.confidence,
            reason="Matched casual conversation or conceptual knowledge prompt."
        )

    if understanding.intent == "LIST_DATABASES":
        return RoutingDecision(
            route="DIRECT", intent="LIST_DATABASES",
            tool_name="list_databases", target_db=resolved_db,
            confidence=1.0, reason="Deterministic list_databases utility command."
        )

    if understanding.intent == "LIST_TABLES":
        return RoutingDecision(
            route="DIRECT", intent="LIST_TABLES",
            tool_name="list_tables", target_db=resolved_db,
            confidence=1.0, reason="Deterministic list_tables utility command."
        )

    if understanding.intent == "DATABASE_SWITCH":
        return RoutingDecision(
            route="DIRECT", intent="DATABASE_SWITCH",
            tool_name="switch_database", target_db=resolved_db,
            confidence=1.0, reason="Deterministic active database switch command."
        )

    if understanding.intent == "DESCRIBE_TABLE":
        return RoutingDecision(
            route="DIRECT", intent="DESCRIBE_TABLE",
            tool_name="get_schema_info", target_db=resolved_db,
            target_table=target_table, confidence=1.0,
            reason="Deterministic schema inspection command."
        )

    # understanding.intent is already authoritative for the live pipeline —
    # semantic_frame's raw_sql capability is forced to intent="RAW_SQL" by
    # frame_result_to_understanding, so this is not a second NLU classifier
    # re-deciding the message. The is_raw_sql() grammar check is kept as a
    # cheap, pure fallback so _resolve_route() (a shared, independently
    # testable function) still behaves correctly for callers that construct
    # an UnderstandingResult some other way and haven't applied that
    # correction (e.g. intent_classifier.classify_intent(), which labels
    # explicit "SELECT ..." as SQL_RETRIEVAL rather than RAW_SQL).
    from agent.sql_detector import is_raw_sql
    raw_sql_matched, _ = is_raw_sql(msg_normalized)
    if understanding.intent == "RAW_SQL" or raw_sql_matched:
        return RoutingDecision(
            route="RAW_SQL", intent="RAW_SQL",
            target_db=resolved_db, target_table=target_table,
            confidence=1.0, reason="Explicit raw SQL statement detected."
        )

    # SIMPLE_DDL is not produced by the live semantic_frame pipeline (each DDL
    # capability carries its own specific legacy_intent) — this branch is kept
    # only so a manually-constructed UnderstandingResult (e.g. in a test) with
    # intent="SIMPLE_DDL" still routes sensibly.
    if understanding.intent == "SIMPLE_DDL":
        if raw_sql_matched:
            return RoutingDecision(
                route="RAW_SQL", intent="SIMPLE_DDL",
                target_db=resolved_db, target_table=target_table,
                confidence=1.0, reason="Explicit DDL SQL statement detected."
            )
        else:
            return RoutingDecision(
                route="AI_PLANNER", intent="SIMPLE_DDL",
                target_db=resolved_db, target_table=target_table,
                confidence=understanding.confidence,
                reason="Natural-language DDL creation request requiring AI reasoning."
            )

    if understanding.intent in {"SQL_RETRIEVAL", "QUERY", "DATABASE_ADMIN", "SCHEMA_EXPLORATION"}:
        from agent.deterministic_sql_builder import is_deterministically_executable
        executable, exec_reason = is_deterministically_executable(
            query_intent=understanding.query_intent,
            target_table=target_table
        )
        if executable:
            return RoutingDecision(
                route="DIRECT", intent=understanding.intent,
                target_db=resolved_db, target_table=target_table,
                target_columns=understanding.columns,
                confidence=understanding.confidence,
                reason=f"Deterministically executable structured query ({exec_reason})."
            )
        else:
            return RoutingDecision(
                route="AI_PLANNER", intent=understanding.intent,
                target_db=resolved_db, target_table=target_table,
                target_columns=understanding.columns,
                confidence=understanding.confidence,
                reason=f"Natural-language query requiring LocalPlanner SQL inference ({exec_reason})."
            )

    return RoutingDecision(
        route="AI_PLANNER", intent=understanding.intent,
        target_db=resolved_db, target_table=target_table,
        confidence=understanding.confidence,
        reason="Natural-language prompt delegated to AI_PLANNER."
    )


def _finalize_decision(understanding: UnderstandingResult, session: Optional[Dict[str, Any]],
                       request_db: Optional[str], message: str) -> GatewayDecision:
    """Shared tail of decide(): resolve the route for an UnderstandingResult and
    assemble the GatewayDecision, including the rich clarification_data payload
    for frame-based clarifications (the shape pending_resolution.py's
    resolve_pending_clarification() expects). Used both for the normal
    pipeline and for the reconstructed-request re-classification after a
    pending clarification is resolved.
    """
    session = session or {}
    routing = _resolve_route(
        understanding=understanding,
        session=session,
        request_db=request_db,
    )
    requires_db = _requires_db_connection(
        route=routing.route,
        intent=routing.intent,
        tool_name=routing.tool_name,
    )

    clarification_msg = understanding.clarification_message
    if routing.route == "CLARIFICATION" and not clarification_msg:
        if routing.target_table:
            clarification_msg = f"Which database contains table **{routing.target_table}**? Please select a database."
        elif routing.clarification_type == "UNRECOGNIZED_QUERY":
            clarification_msg = "I couldn't safely understand this request. Please rephrase your query."
        else:
            clarification_msg = "Could you please clarify your request?"

    clarification_data = None
    clarification_type = routing.clarification_type
    if routing.route == "CLARIFICATION" and understanding.semantic_frame is not None:
        from agent import semantic_frame as _sf
        from state.metadata_store import get_metadata
        clarification_data = _sf.clarification_data_for_frame(
            frame=understanding.semantic_frame,
            clarification_message=clarification_msg,
            meta=get_metadata(),
            target_db=routing.target_db,
            session=session,
            message=message,
        )
        clarification_type = clarification_data["type"]

    return GatewayDecision(
        understanding=understanding,
        route=routing.route,
        target_db=routing.target_db,
        target_table=routing.target_table,
        target_columns=routing.target_columns,
        tool_name=routing.tool_name,
        needs_clarification=routing.needs_clarification,
        clarification_type=clarification_type,
        clarification_message=clarification_msg,
        clarification_data=clarification_data,
        requires_db_connection=requires_db,
        semantic_frame=understanding.semantic_frame,
        reason=routing.reason,
    )


# ─── Stage 2: decide() — The Single Authoritative Entry Point ─────────────────

def decide(
    user_message: str,
    raw_message: str,
    database_context: Optional[str] = None,
    session: Optional[Dict[str, Any]] = None,
    request_db: Optional[str] = None,
) -> GatewayDecision:
    """Single authoritative entry point. Produces a GatewayDecision that carries
    understanding + route + clarification state + DB-connection requirement.

    This is the ONE function that chat.py and the coordinator should call.
    No downstream component re-classifies or re-routes.
    """
    # ── Pending Clarification / Operation Resolution ───────────────────────────
    # If a pending clarification exists in the session, attempt resolution BEFORE
    # normal understanding + routing. The user's message is treated as a response
    # to the pending clarification, not as a new query.
    pending = session.get("pending_clarification") if session else None
    if pending:
        from agent.pending_resolution import (
            is_new_command_detected,
            resolve_pending_clarification,
            _print_clarification_trace,
        )
        import time as _time

        created_at = pending.get("created_at") or _time.time()
        if "created_at" not in pending:
            pending["created_at"] = created_at

        # Timeout: 15 minutes
        if _time.time() - created_at > 900:
            _logger.info("Clarification expired after timeout.")
            session["pending_clarification"] = None
            pending = None
        elif is_new_command_detected(raw_message):
            _logger.info("New command detected. Discarding pending clarification.")
            session["pending_clarification"] = None
            pending = None
        else:
            resolve = resolve_pending_clarification(raw_message, pending)
            if resolve["resolved"]:
                _logger.info(
                    "Clarification resolved: type=%s selected=%s",
                    resolve["clarification_type"],
                    resolve["selected_option"],
                )

                # ── Cancel ────────────────────────────────────────────────────
                if resolve["selected_option"] == "cancel":
                    session["pending_clarification"] = None
                    # Build a minimal understanding so downstream gets a coherent decision
                    _understanding = UnderstandingResult(
                        raw_input=raw_message,
                        normalized_input=raw_message,
                        intent="GENERAL_CONVERSATION",
                        source="PENDING_CANCEL",
                        confidence=1.0,
                        status="COMPLETED",
                    )
                    return GatewayDecision(
                        understanding=_understanding,
                        route="CLARIFICATION",
                        needs_clarification=True,
                        clarification_type="CANCEL",
                        clarification_message="Clarification cancelled.",
                        clarification_data=resolve,
                        requires_db_connection=False,
                        reason="Pending clarification cancelled by user.",
                    )

                # ── CONFIRMATION: return the SQL to execute ───────────────────
                if resolve["clarification_type"] == "CONFIRMATION":
                    _understanding = UnderstandingResult(
                        raw_input=raw_message,
                        normalized_input=raw_message,
                        intent="CONFIRMATION",
                        source="PENDING_CONFIRMATION",
                        confidence=1.0,
                        status="COMPLETED",
                    )
                    return GatewayDecision(
                        understanding=_understanding,
                        route="CONFIRMATION",
                        target_db=resolve.get("override_target_db"),
                        needs_clarification=False,
                        clarification_type="CONFIRMATION",
                        clarification_data=resolve,
                        requires_db_connection=True,
                        reason="Pending CONFIRMATION resolved — SQL ready to execute.",
                    )

                # ── CREATE_TABLE_NAME_SET: stay in clarification ──────────────
                if resolve["clarification_type"] == "CREATE_TABLE_NAME_SET":
                    _understanding = UnderstandingResult(
                        raw_input=raw_message,
                        normalized_input=raw_message,
                        intent="SIMPLE_DDL",
                        source="PENDING_TABLE_NAME_SET",
                        confidence=1.0,
                        status="COMPLETED",
                    )
                    return GatewayDecision(
                        understanding=_understanding,
                        route="CLARIFICATION",
                        target_db=resolve.get("override_target_db"),
                        needs_clarification=True,
                        clarification_type="CREATE_TABLE_COLUMNS",
                        clarification_message=f"Table name set to **{resolve['metadata'].get('table_name')}**. Please provide the list of columns with their data types (e.g., id int, name text).",
                        clarification_data=resolve,
                        requires_db_connection=False,
                        reason="Pending CREATE_TABLE_NAME_SET resolved — waiting for column definitions.",
                    )

                # ── CREATE_TABLE_COLUMNS resolved → CONFIRMATION ─────────────
                # When the user provides column definitions for a pending
                # CREATE_TABLE clarification, the resolution produces a complete
                # CREATE TABLE SQL statement. Return it as CONFIRMATION so the
                # execution layer can run it directly (no re-routing needed).
                if resolve["clarification_type"] == "CREATE_TABLE_COLUMNS":
                    _sql = resolve.get("reconstructed_request")
                    _db = resolve.get("override_target_db")
                    _understanding = UnderstandingResult(
                        raw_input=raw_message,
                        normalized_input=raw_message,
                        intent="SIMPLE_DDL",
                        source="PENDING_COLUMNS_RESOLVED",
                        confidence=1.0,
                        status="COMPLETED",
                    )
                    # Clear pending state
                    session["pending_clarification"] = None
                    return GatewayDecision(
                        understanding=_understanding,
                        route="CONFIRMATION",
                        target_db=_db,
                        needs_clarification=False,
                        clarification_type="CREATE_TABLE_COLUMNS",
                        clarification_data={
                            "sql": _sql,
                            "target_db": _db,
                        },
                        requires_db_connection=True,
                        reason="Pending CREATE_TABLE_COLUMNS resolved — SQL ready to execute.",
                    )

                # ── Other resolved types: reconstruct and re-route ────────────
                # The pending clarification has been resolved. Update session state,
                # then fall through to normal understanding + routing with the
                # reconstructed request.
                reconstructed = resolve.get("reconstructed_request") or raw_message
                override_db = resolve.get("override_target_db")
                if override_db:
                    request_db = override_db

                if resolve.get("metadata", {}).get("selected_table"):
                    if session:
                        session["selected_table"] = resolve["metadata"]["selected_table"]

                # Clear pending state
                session["pending_clarification"] = None

                # Re-classify the reconstructed request through the gateway pipeline
                _reclass = process_universal_semantic_gateway(
                    user_message=reconstructed,
                    raw_message=raw_message,
                    database_context=database_context,
                    session=session,
                )
                return _finalize_decision(_reclass, session, request_db, reconstructed)

    # ── Normal Understanding + Routing Pipeline ────────────────────────────────
    understanding = process_universal_semantic_gateway(
        user_message=user_message,
        raw_message=raw_message,
        database_context=database_context,
        session=session,
    )

    return _finalize_decision(understanding, session, request_db, raw_message)
