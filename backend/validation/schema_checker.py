"""
Phase 4 — Schema Checker
=========================
Gate 2 of the validation pipeline.

Responsibility:
  Validate that the SQL references tables and columns that actually exist
  in the currently selected database, using the in-memory schema cached
  by metadata_store (populated during Phase 2 schema introspection).

Scope:
  - Only DML intents are checked: QUERY, INSERT, UPDATE, DELETE.
  - DDL intents (CREATE_*, DROP_*, ADD_COLUMN, etc.) are skipped:
      - CREATE ops reference tables/columns that don't exist yet.
      - DROP ops existence is validated differently by the DB engine.
  - If no schema is loaded (no database selected), the check passes
    gracefully to avoid false validation failures.

Strategy:
  Lightweight keyword-based extraction is used instead of a full SQL parser.
  Since the SQL comes from Gemini (which was grounded on the real schema),
  simple token extraction is accurate enough. A full parser (sqlglot, pglast)
  can be swapped in during Phase 7+ hardening.
"""

import re
from typing import Optional

# ── Intents that reference existing tables/columns ────────────────────────────
_DML_INTENTS: set[str] = {"QUERY", "INSERT", "UPDATE", "DELETE"}

# ── Table name extraction patterns ────────────────────────────────────────────
# Each pattern captures the primary table name from a specific SQL keyword position.
_FROM_PATTERN   = re.compile(r'\bFROM\s+["\'\`]?(\w+)["\'\`]?', re.IGNORECASE)
_INTO_PATTERN   = re.compile(r'\bINTO\s+["\'\`]?(\w+)["\'\`]?', re.IGNORECASE)
_UPDATE_PATTERN = re.compile(r'^\s*UPDATE\s+["\'\`]?(\w+)["\'\`]?', re.IGNORECASE | re.MULTILINE)

# ── SET clause column extraction ──────────────────────────────────────────────
# Extracts the SET portion of an UPDATE statement, then pulls column names.
# Example: "SET salary = 50000, dept = 'HR' WHERE id = 5"
_SET_CLAUSE_PATTERN = re.compile(
    r'\bSET\s+(.+?)(?:\s+WHERE\b|$)',
    re.IGNORECASE | re.DOTALL
)
_SET_COLUMN_PATTERN = re.compile(r'["\'\`]?(\w+)["\'\`]?\s*=', re.IGNORECASE)


def _extract_table_name(intent: str, sql: str) -> Optional[str]:
    """
    Extract the primary table name from a SQL statement based on intent.

    Args:
        intent: The Gemini-classified intent (determines which keyword to look for).
        sql:    The SQL string.

    Returns:
        The extracted table name string, or None if extraction fails.
    """
    if intent == "UPDATE":
        match = _UPDATE_PATTERN.search(sql)
    elif intent == "INSERT":
        match = _INTO_PATTERN.search(sql)
    else:  # QUERY, DELETE — use FROM
        match = _FROM_PATTERN.search(sql)

    return match.group(1) if match else None


def _extract_set_columns(sql: str) -> list[str]:
    """
    Extract the column names referenced in the SET clause of an UPDATE statement.

    Args:
        sql: The UPDATE SQL string.

    Returns:
        A list of column name strings.
    """
    set_match = _SET_CLAUSE_PATTERN.search(sql)
    if not set_match:
        return []
    set_clause = set_match.group(1)
    return [m.group(1) for m in _SET_COLUMN_PATTERN.finditer(set_clause)]


def check_schema(intent: str, sql: Optional[str], schema: dict) -> dict:
    """
    Gate 2: Validate that the SQL references existing tables and columns.

    Args:
        intent: The Gemini-classified intent string.
        sql:    The generated SQL string. May be None.
        schema: Dict mapping table_name → [column_name, ...] from metadata_store.
                Example: { "employees": ["id", "name", "salary"], "depts": ["id", "name"] }

    Returns:
        {
            "valid": bool,
            "reason": str | None    (populated when valid=False)
        }
    """

    # ── Only validate DML intents ─────────────────────────────────────────────
    if intent not in _DML_INTENTS:
        return {"valid": True, "reason": None}

    # ── Pass through if SQL or schema is absent ───────────────────────────────
    if not sql:
        return {"valid": True, "reason": None}

    if not schema:
        # No database/schema loaded yet — cannot validate. Allow through gracefully.
        return {"valid": True, "reason": None}

    # ── Extract and verify the target table ───────────────────────────────────
    table_name = _extract_table_name(intent, sql)

    if table_name is None:
        # Could not extract a table name (e.g. complex subquery, wildcard).
        # Allow through — we can't validate what we can't see.
        return {"valid": True, "reason": None}

    # Case-insensitive table lookup
    schema_tables_lower = {t.lower(): t for t in schema.keys()}

    if table_name.lower() not in schema_tables_lower:
        available = ", ".join(schema.keys()) if schema else "none"
        return {
            "valid": False,
            "reason": (
                f"Table '{table_name}' does not exist in the selected database. "
                f"Available tables: {available}."
            ),
        }

    # ── For UPDATE: validate SET clause column names ──────────────────────────
    if intent == "UPDATE":
        # Resolve to the actual table name (preserving original casing from schema)
        actual_table = schema_tables_lower[table_name.lower()]
        schema_cols_lower = {c.lower() for c in schema[actual_table]}

        set_columns = _extract_set_columns(sql)

        for col in set_columns:
            # 'id' is always valid as a WHERE target; skip it in SET col list
            if col.lower() == "id":
                continue
            if col.lower() not in schema_cols_lower:
                return {
                    "valid": False,
                    "reason": (
                        f"Column '{col}' does not exist in table '{actual_table}'. "
                        f"Available columns: {', '.join(schema[actual_table])}."
                    ),
                }

    return {"valid": True, "reason": None}
