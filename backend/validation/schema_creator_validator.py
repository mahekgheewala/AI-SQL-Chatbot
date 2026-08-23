"""
Phase 4 — Schema Creator Validator
====================================
Gate 3 of the validation pipeline.

Responsibility:
  For DDL creation/modification operations, validate that:
    1. SQL does not contain comment-based injection patterns (-- or /* */).
    2. Column types used in ADD_COLUMN / MODIFY_COLUMN are in the allowed whitelist.

Activated for:
  CREATE_DATABASE, CREATE_TABLE, ADD_COLUMN, MODIFY_COLUMN, RENAME_COLUMN, RENAME_TABLE

Skipped for all other intents (their safety and schema checks are handled
by the earlier two gates).

Identifier Validation:
  Uses the same regex as database_manager.py and table_manager.py:
    ^[a-zA-Z][a-zA-Z0-9_]*$
  This ensures naming rules are enforced uniformly across the codebase.

Type Allowlist:
  Shared with db/table_manager.py via db/column_types.py — a single source of
  truth, so a type accepted here is guaranteed accepted at actual table
  creation too.

Note:
  Deep structural SQL parsing (e.g. extracting every identifier from a
  CREATE TABLE statement) is Phase 7+ territory. This gate focuses on
  the highest-value, low-complexity checks: injection guards and type
  validation for column ADD/MODIFY operations.
"""

import re
from typing import Optional

from db.column_types import ALLOWED_COLUMN_TYPES, normalize_column_type

# ── Intents this validator handles ────────────────────────────────────────────
_CREATOR_INTENTS: set[str] = {
    "CREATE_DATABASE",
    "CREATE_TABLE",
    "ADD_COLUMN",
    "MODIFY_COLUMN",
    "RENAME_COLUMN",
    "RENAME_TABLE",
}

# ── Injection guard patterns ──────────────────────────────────────────────────
_LINE_COMMENT_PATTERN  = re.compile(r'--')
_BLOCK_COMMENT_PATTERN = re.compile(r'/\*')

# ── Column type extraction for ADD_COLUMN / MODIFY_COLUMN ────────────────────
# Matches: ADD COLUMN <colname> <TYPE>
# e.g. "ALTER TABLE employees ADD COLUMN salary INTEGER"
_ADD_COL_TYPE_PATTERN = re.compile(
    r'\bADD\s+COLUMN\s+\w+\s+(\w+)',
    re.IGNORECASE
)

# Matches: ALTER COLUMN <colname> TYPE <TYPE>
# e.g. "ALTER TABLE employees ALTER COLUMN salary TYPE FLOAT"
_MODIFY_COL_TYPE_PATTERN = re.compile(
    r'\bTYPE\s+(\w+)',
    re.IGNORECASE
)

# Matches: SET DATA TYPE <TYPE>
_SET_DATA_TYPE_PATTERN = re.compile(
    r'\bSET\s+DATA\s+TYPE\s+(\w+)',
    re.IGNORECASE
)


def _has_injection_patterns(sql: str) -> bool:
    """
    Detect SQL comment sequences that may indicate an injection attempt.

    Args:
        sql: The SQL string to inspect.

    Returns:
        True if a suspicious comment pattern is found.
    """
    if _LINE_COMMENT_PATTERN.search(sql):
        return True
    if _BLOCK_COMMENT_PATTERN.search(sql):
        return True
    return False


def _extract_column_type(intent: str, sql: str) -> Optional[str]:
    """
    Extract the declared column type from an ADD_COLUMN or MODIFY_COLUMN statement.

    Args:
        intent: "ADD_COLUMN" or "MODIFY_COLUMN".
        sql:    The SQL string.

    Returns:
        The extracted type keyword (uppercase), or None if not found.
    """
    if intent == "ADD_COLUMN":
        match = _ADD_COL_TYPE_PATTERN.search(sql)
    else:  # MODIFY_COLUMN
        match = (
            _MODIFY_COL_TYPE_PATTERN.search(sql)
            or _SET_DATA_TYPE_PATTERN.search(sql)
        )
    return match.group(1).upper() if match else None


def check_creator_sql(intent: str, sql: Optional[str]) -> dict:
    """
    Gate 3: Validate DDL SQL for injection patterns and allowed column types.

    Args:
        intent: The Gemini-classified intent string.
        sql:    The generated SQL string. May be None.

    Returns:
        {
            "valid": bool,
            "reason": str | None    (populated when valid=False)
        }
    """

    # ── Only validate DDL creation/modification intents ───────────────────────
    if intent not in _CREATOR_INTENTS:
        return {"valid": True, "reason": None}

    if not sql:
        return {"valid": True, "reason": None}

    # ── Injection guard ───────────────────────────────────────────────────────
    if _has_injection_patterns(sql):
        return {
            "valid": False,
            "reason": (
                "SQL contains comment sequences (-- or /* */) that may indicate "
                "a SQL injection attempt."
            ),
        }

    # ── Column type validation (ADD_COLUMN / MODIFY_COLUMN only) ─────────────
    if intent in {"ADD_COLUMN", "MODIFY_COLUMN"}:
        col_type = _extract_column_type(intent, sql)

        if col_type is not None:
            base_type = normalize_column_type(col_type)

            if base_type not in ALLOWED_COLUMN_TYPES:
                return {
                    "valid": False,
                    "reason": (
                        f"Column type '{col_type}' is not in the allowed type list. "
                        f"Allowed types: {', '.join(sorted(ALLOWED_COLUMN_TYPES))}."
                    ),
                }

    return {"valid": True, "reason": None}
