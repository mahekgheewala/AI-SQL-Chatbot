"""
Pending Clarification Resolution — Shared Module
=================================================
Extracted from chat.py (Stage 3). This module owns all pending-clarification
resolution logic. Both the Universal Gateway and chat.py import from here.

The gateway calls these functions to resolve pending state.
chat.py calls them too during the transition period (Stages 3-4).
"""
from __future__ import annotations

import re
import time
from typing import Dict, Any, Optional


# ─── New Command Detection ────────────────────────────────────────────────────

_NEW_COMMAND_PATTERN = re.compile(
    r"^\s*(show|list|display|find|count|search|get|use|switch|select|insert|update|delete|create|alter|drop|truncate|describe|explain|generate\s+report|format)\b",
    re.IGNORECASE
)


def is_new_command_detected(message: str) -> bool:
    """Detect if the user message matches a brand new command prefix.
    If so, we discard any pending clarification context."""
    return bool(_NEW_COMMAND_PATTERN.match(message))


# ─── Cancellation ──────────────────────────────────────────────────────────

CANCEL_WORDS = frozenset({"cancel", "nevermind", "stop", "reset", "no", "n", "reject", "deny"})


def is_cancel_word(message: str) -> bool:
    """Check if the message is a cancellation word."""
    return message.strip().lower() in CANCEL_WORDS


# ─── Column Definition Validation ─────────────────────────────────────────────

_VALID_DATATYPES = {
    "int", "integer", "serial", "text", "varchar", "char", "numeric",
    "decimal", "float", "double", "real", "boolean", "bool", "date",
    "time", "timestamp", "json", "uuid",
    "bigint", "smallint", "jsonb", "bytea", "timestamptz",
    "character varying", "character", "double precision"
}

_CONSTRAINT_KEYWORDS = {"primary", "foreign", "unique", "check", "constraint"}

_FORBIDDEN_COLUMN_NAMES = {
    "show", "list", "display", "find", "count", "search", "get", "use",
    "switch", "select", "insert", "update", "delete", "create", "alter",
    "drop", "truncate", "describe", "explain", "generate", "format"
}


def split_column_definitions(message: str) -> list[str]:
    """Split a column definition list by commas, ignoring commas inside parentheses."""
    parts: list[str] = []
    current: list[str] = []
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
    """Validate if the message looks like a valid Postgres column definition list."""
    parts = split_column_definitions(message)
    if not parts:
        return False
    for part in parts:
        tokens = part.split()
        if not tokens:
            continue
        first_token = tokens[0].lower()
        is_constraint = any(
            first_token == kw or first_token.startswith(kw + "(")
            for kw in _CONSTRAINT_KEYWORDS
        )
        if is_constraint:
            continue
        if len(tokens) < 2:
            return False
        col_name = tokens[0].lower()
        if col_name in _FORBIDDEN_COLUMN_NAMES:
            return False
        datatype = tokens[1].lower()
        if datatype == "character" and len(tokens) >= 3 and (tokens[2].lower() in ("varying",) or tokens[2].lower().startswith("varying(")):
            datatype_clean = "character varying"
        elif datatype == "double" and len(tokens) >= 3 and tokens[2].lower().startswith("precision"):
            datatype_clean = "double precision"
        else:
            datatype_clean = re.sub(r"\(.*?\)", "", tokens[1]).strip().lower()
        if datatype_clean not in _VALID_DATATYPES:
            return False
    return True


# ─── Resolution Trace ─────────────────────────────────────────────────────────

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


# ─── Resolution Builders ──────────────────────────────────────────────────────

def _grounding_metadata_for_pending(pending: dict) -> dict:
    """Live grounding metadata scoped to a pending clarification's target
    database — used only by the LLM-fallback resolvers below, which must
    validate whatever the LLM proposes against the real schema. Explicitly
    syncs the schema cache first (matching agent_coordinator.py's
    _format_schema_context_for_planner convention) — a table created earlier
    in the same conversation may not have been picked up by a passive cache
    read yet, which would otherwise make every column on it look ungrounded."""
    from state.metadata_store import get_metadata, sync_database_context
    from agent.semantic_frame import build_grounding_metadata
    target_db = pending.get("target_db")
    if target_db:
        try:
            sync_database_context(target_db)
        except Exception:
            pass
    return build_grounding_metadata(get_metadata(), target_db)


def _resolve_create_table_columns_via_llm(message: str, table_name: str, pending: dict) -> Optional[dict]:
    """LLM fallback for CREATE_TABLE_COLUMNS when is_valid_column_definition()
    can't parse the reply as a structural column-definition list — e.g. a
    reply naming columns in plain English rather than "name TYPE, name TYPE"
    syntax. Returns a resolved result dict, or None if the LLM couldn't
    extract valid columns either (caller keeps its existing "ask again"
    behavior in that case)."""
    from agent.clarification_resolver import resolve_create_table_slots

    result = resolve_create_table_slots(
        original_request=pending.get("original_request", ""),
        missing=["columns"],
        user_reply=message,
        metadata=_grounding_metadata_for_pending(pending),
        known_table=table_name,
    )
    if not result.ok or not result.columns:
        return None

    target_db = pending.get("target_db")
    parts = [f"{c.name} {c.type or 'TEXT'}" for c in result.columns]
    indented_parts = [f"    {p}" for p in parts]
    cols_formatted = ",\n".join(indented_parts)
    reconstructed = f"CREATE TABLE {table_name} (\n{cols_formatted}\n);"

    orig_req = pending.get("original_request", "<unknown>")
    _print_clarification_trace(
        clarification_type="CREATE_TABLE_COLUMNS",
        original_request=orig_req,
        options=[],
        selected_option=message.strip(),
        reconstructed_request=reconstructed,
        override_target_db=target_db,
    )
    return {
        "resolved": True,
        "selected_option": message.strip(),
        "clarification_type": "CREATE_TABLE_COLUMNS",
        "reconstructed_request": reconstructed,
        "override_target_db": target_db,
        "metadata": {}
    }


def _resolve_visualize_via_llm(message: str, pending: dict) -> Optional[dict]:
    """LLM resolution for a chart clarification reply. Unlike every other
    branch here, this doesn't produce a `reconstructed_request` SQL/NL
    string — it hands back a fully-grounded chart spec that
    agent/universal_gateway.py turns directly into a SemanticFrame, reusing
    the same deterministic execute_sql + VisualizationEngine path a
    one-shot, fully-specified chart request already uses. Returns None if
    the LLM couldn't resolve it either (caller keeps its existing "ask
    again" behavior)."""
    from agent.clarification_resolver import resolve_chart_slots

    result = resolve_chart_slots(
        original_request=pending.get("original_request", ""),
        user_reply=message,
        metadata=_grounding_metadata_for_pending(pending),
        table_hint=pending.get("table_name"),
    )
    if not result.ok:
        return None

    _print_clarification_trace(
        clarification_type="MISSING_ROLE",
        original_request=pending.get("original_request", "<unknown>"),
        options=[],
        selected_option=message.strip(),
        reconstructed_request=(
            f"VISUALIZE table={result.table} chart_type={result.chart_type} "
            f"measure={result.measure} dimension={result.dimension} aggregation={result.aggregation}"
        ),
        override_target_db=pending.get("target_db"),
    )
    return {
        "resolved": True,
        "selected_option": message.strip(),
        "clarification_type": "VISUALIZE_RESOLVED",
        "reconstructed_request": None,
        "override_target_db": pending.get("target_db"),
        "metadata": {
            "chart": {
                "table": result.table,
                "chart_type": result.chart_type,
                "measure": result.measure,
                "dimension": result.dimension,
                "aggregation": result.aggregation,
            }
        }
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


def _build_table_resolution_result(clar_type: str, selected: str, pending: dict) -> dict:
    orig_req = pending.get("original_request") or ""

    # add_sample_data can never be reconstructed by gluing the resolved table
    # name onto the end of the original sentence — string concatenation
    # produces grammatically broken text ("add sample data in it books") that
    # the downstream LLM misreads as a read request, and there is no
    # deterministic SQL builder for INSERT to fall back on. Once a table is
    # known, generate the actual sample data (validated against the live
    # schema) instead.
    if pending.get("capability_id") == "add_sample_data":
        from agent.clarification_resolver import resolve_sample_data_request
        result = resolve_sample_data_request(
            original_request=orig_req,
            user_reply=orig_req,
            metadata=_grounding_metadata_for_pending(pending),
            table_hint=selected,
            row_count_hint=pending.get("sample_count"),
        )
        if result.ok and result.insert_sql:
            _print_clarification_trace(
                clarification_type=clar_type,
                original_request=orig_req or "<unknown>",
                options=pending.get("options", []),
                selected_option=selected,
                reconstructed_request=result.insert_sql,
                override_target_db=pending.get("target_db"),
            )
            return {
                "resolved": True,
                "selected_option": selected,
                "clarification_type": clar_type,
                "reconstructed_request": result.insert_sql,
                "override_target_db": pending.get("target_db"),
                "metadata": {"selected_table": selected}
            }
        print(f"[Clarification] LLM could not generate sample data: {result.reason}")
        return {
            "resolved": False,
            "selected_option": None,
            "clarification_type": clar_type,
            "reconstructed_request": None,
            "override_target_db": None,
            "metadata": {}
        }

    reconstructed = orig_req
    if selected and selected.lower() not in orig_req.lower():
        reconstructed = f"{orig_req} {selected}"
    _print_clarification_trace(
        clarification_type=clar_type,
        original_request=orig_req or "<unknown>",
        options=pending.get("options", []),
        selected_option=selected,
        reconstructed_request=reconstructed,
        override_target_db=pending.get("target_db"),
    )
    return {
        "resolved": True,
        "selected_option": selected,
        "clarification_type": clar_type,
        "reconstructed_request": reconstructed,
        "override_target_db": pending.get("target_db"),
        "metadata": {"selected_table": selected}
    }


# ─── Main Resolution Function ─────────────────────────────────────────────────

def resolve_pending_clarification(message: str, pending: dict) -> dict:
    """Resolve a pending clarification against the user's new message.

    Returns a dict with keys: resolved, selected_option, clarification_type,
    reconstructed_request, override_target_db, metadata.
    """
    msg = message.strip().lower()

    # 1. Cancellation terms
    if is_cancel_word(msg):
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

        if not table_name:
            candidate_tbl = message.strip()
            identifier_pat = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
            reserved_kw = {"select", "create", "table", "database", "from", "where", "insert", "update", "delete", "drop", "alter"}
            if identifier_pat.match(candidate_tbl) and candidate_tbl.lower() not in reserved_kw:
                print(f"[Clarification] Setting missing table_name to '{candidate_tbl}'.")
                pending["table_name"] = candidate_tbl
                return {
                    "resolved": True,
                    "selected_option": candidate_tbl,
                    "clarification_type": "CREATE_TABLE_NAME_SET",
                    "reconstructed_request": f"CREATE TABLE {candidate_tbl}",
                    "override_target_db": pending.get("target_db"),
                    "metadata": {"table_name": candidate_tbl, "requires_columns": True}
                }
            else:
                print("[Clarification] Validation failed: table_name missing in pending clarification.")
                return {
                    "resolved": False,
                    "selected_option": None,
                    "clarification_type": clar_type,
                    "reconstructed_request": None,
                    "override_target_db": None,
                    "metadata": {}
                }

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
            print(f"[Clarification] Invalid column definition structure: {repr(message)}. Trying LLM fallback...")
            fallback_result = _resolve_create_table_columns_via_llm(message, table_name, pending)
            if fallback_result is not None:
                return fallback_result
            print("[Clarification] LLM fallback also failed to extract valid columns.")
            return {
                "resolved": False,
                "selected_option": None,
                "clarification_type": clar_type,
                "reconstructed_request": None,
                "override_target_db": None,
                "metadata": {}
            }

        target_db = pending.get("target_db")
        parts = [p.strip() for p in split_column_definitions(message)]
        indented_parts = [f"    {p}" for p in parts]
        cols_formatted = ",\n".join(indented_parts)
        reconstructed = f"CREATE TABLE {table_name} (\n{cols_formatted}\n);"

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
            intent = pending.get("metadata", {}).get("intent")
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
                "metadata": {"intent": intent}
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

    # 4. AMBIGUOUS_TABLE, MISSING_TABLE, AMBIGUOUS_TABLE_LOCATION, MISSING_DATABASE, AMBIGUOUS_ANALYTICS_TABLE
    if clar_type in {"AMBIGUOUS_TABLE", "MISSING_TABLE", "AMBIGUOUS_TABLE_LOCATION", "MISSING_DATABASE", "AMBIGUOUS_ANALYTICS_TABLE"}:
        options = pending.get("options", [])
        cleaned_options = [opt.strip().lower() for opt in options]

        def build_res(selected_val):
            if clar_type in {"AMBIGUOUS_TABLE", "MISSING_TABLE", "AMBIGUOUS_ANALYTICS_TABLE"}:
                return _build_table_resolution_result(clar_type, selected_val, pending)
            return _build_db_resolution_result(clar_type, selected_val, pending)

        for idx, opt in enumerate(cleaned_options):
            if msg == opt:
                return build_res(options[idx])

        sorted_options = sorted(list(enumerate(cleaned_options)), key=lambda x: len(x[1]), reverse=True)

        for idx, opt in sorted_options:
            if re.search(rf"\b{re.escape(opt)}\b", msg):
                return build_res(options[idx])

        ordinals = {
            "first": 0, "1st": 0, "1": 0, "one": 0,
            "second": 1, "2nd": 1, "2": 1, "two": 1,
            "third": 2, "3rd": 2, "3": 2, "three": 2,
        }
        for word, idx in ordinals.items():
            if re.search(rf"\b{word}\b", msg) and idx < len(options):
                return build_res(options[idx])

        numeric_patterns = [
            r"\b(?:database|db|table|option|choice|number|selection)\s+(\d+)\b",
            r"\b(?:the\s+)?(\w+)\s+(?:database|db|table|option)\b"
        ]
        for pat in numeric_patterns:
            match = re.search(pat, msg)
            if match:
                val = match.group(1)
                if val.isdigit():
                    idx = int(val) - 1
                    if 0 <= idx < len(options):
                        return build_res(options[idx])
                else:
                    if val in ordinals:
                        idx = ordinals[val]
                        if idx < len(options):
                            return build_res(options[idx])

        for idx, opt in sorted_options:
            if opt in msg:
                return build_res(options[idx])

        if clar_type in {"AMBIGUOUS_TABLE", "MISSING_TABLE"}:
            tbl_candidate = message.strip()
            identifier_pat = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
            if identifier_pat.match(tbl_candidate) and tbl_candidate.lower() not in {"cancel", "stop", "no"}:
                return _build_table_resolution_result(clar_type, tbl_candidate, pending)

    # 4.5. CREATE_TABLE_NAME
    if clar_type == "CREATE_TABLE_NAME":
        tbl_name = message.strip()
        identifier_pat = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
        reserved_kw = {"select", "create", "table", "database", "from", "where", "insert", "update", "delete", "drop", "alter"}
        if identifier_pat.match(tbl_name) and tbl_name.lower() not in reserved_kw:
            _print_clarification_trace(
                clarification_type=clar_type,
                original_request=pending.get("original_request", "<unknown>"),
                options=[],
                selected_option=tbl_name,
                reconstructed_request=f"CREATE TABLE {tbl_name}",
                override_target_db=pending.get("target_db"),
            )
            return {
                "resolved": True,
                "selected_option": tbl_name,
                "clarification_type": clar_type,
                "reconstructed_request": f"CREATE TABLE {tbl_name}",
                "override_target_db": pending.get("target_db"),
                "metadata": {"table_name": tbl_name, "next_step": "CREATE_TABLE_COLUMNS"}
            }

    # 4.6. MISSING_ROLE for a "visualize" capability — chart clarification
    # ("Which column would you like to use for the pie chart?"). This is a
    # generic label (see clarification_data_for_frame — a fully-grounded
    # frame that the Local Planner still decided needs clarification has no
    # more specific missing_required role to name), so it's scoped narrowly
    # to capability_id == "visualize" here; other MISSING_ROLE cases fall
    # through to the caller's generic "could not resolve" handling.
    if clar_type == "MISSING_ROLE" and pending.get("capability_id") == "visualize":
        chart_result = _resolve_visualize_via_llm(message, pending)
        if chart_result is not None:
            return chart_result
        print("[Clarification] LLM chart resolution failed.")

    # 5. PENDING_VISUALIZATION
    if clar_type == "PENDING_VISUALIZATION":
        orig_req = pending.get("original_request", "")
        reconstructed = f"{orig_req} {msg}"
        target_db = pending.get("target_db")
        _print_clarification_trace(
            clarification_type=clar_type,
            original_request=orig_req,
            options=pending.get("options", []),
            selected_option=msg,
            reconstructed_request=reconstructed,
            override_target_db=target_db,
        )
        return {
            "resolved": True,
            "selected_option": msg,
            "clarification_type": clar_type,
            "reconstructed_request": reconstructed,
            "override_target_db": target_db,
            "metadata": {}
        }

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
