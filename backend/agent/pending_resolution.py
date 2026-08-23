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
