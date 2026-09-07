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
    Ignores SQL function FROM clauses like EXTRACT(YEAR FROM col).
    """
    if intent == "UPDATE":
        match = _UPDATE_PATTERN.search(sql)
        return match.group(1) if match else None
    elif intent == "INSERT":
        match = _INTO_PATTERN.search(sql)
        return match.group(1) if match else None
    else:  # QUERY, DELETE — use FROM
        # Find all FROM matches and filter out function calls like EXTRACT(YEAR FROM ...)
        matches = re.finditer(r'\bFROM\s+["\'\`]?(\w+)["\'\`]?', sql, re.IGNORECASE)
        for m in matches:
            prefix = sql[:m.start()].strip().upper()
            if prefix.endswith("EXTRACT(") or prefix.endswith("SUBSTRING(") or "EXTRACT(" in prefix[-20:] or "SUBSTRING(" in prefix[-20:]:
                continue
            return m.group(1)
        return None


# SQL reserved words/functions a bare-identifier check must never mistake
# for a column reference.
_SQL_RESERVED_FOR_BARE_COLUMNS = frozenset({
    "select", "from", "where", "and", "or", "not", "null", "is", "in",
    "like", "between", "group", "by", "order", "asc", "desc", "limit",
    "offset", "as", "distinct", "having", "on", "join", "inner", "left",
    "right", "outer", "full", "cross", "true", "false",
    "count", "sum", "avg", "min", "max",
})


def _split_top_level_commas(text: str) -> list[str]:
    """Split on commas that aren't inside parentheses (so a function call
    like COUNT(a, b) isn't split apart)."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def _extract_bare_select_columns(sql: str) -> list[str]:
    """Bare (unqualified, no function call, no '*', no expression) column
    names in the SELECT list — e.g. "name", "salary" from
    "SELECT name, salary FROM t". A column with "AS alias" keeps the
    column, not the alias. Anything containing "(" (function calls like
    COUNT(x)) or "." (already-qualified, checked above) or more than one
    token (an expression like "salary * 2") is left unchecked rather than
    risk a false positive on something this simple extraction can't
    confidently parse."""
    m = re.search(r"\bSELECT\s+(?:DISTINCT\s+)?(.+?)\s+FROM\b", sql, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    cols = []
    for part in _split_top_level_commas(m.group(1)):
        part = part.strip()
        if not part or part == "*" or "(" in part or "." in part:
            continue
        base = re.split(r"\s+AS\s+", part, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        base_tokens = base.split()
        if (
            len(base_tokens) == 1
            and re.match(r"^[a-zA-Z_]\w*$", base_tokens[0])
            and base_tokens[0].lower() not in _SQL_RESERVED_FOR_BARE_COLUMNS
        ):
            cols.append(base_tokens[0])
    return cols


def _extract_bare_where_columns(sql: str) -> list[str]:
    """Bare (unqualified) column names in the WHERE clause, identified by
    appearing directly before a comparison operator or LIKE/IN/IS/BETWEEN
    — the shape a real filter condition always takes, so this doesn't
    need to parse the full expression grammar to find them safely."""
    m = re.search(
        r"\bWHERE\s+(.+?)(?:\s+GROUP\s+BY\b|\s+ORDER\s+BY\b|\s+LIMIT\b|\s+OFFSET\b|$)",
        sql, re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return []
    where_text = m.group(1)
    cols = []
    for pm in re.finditer(
        r"\b([a-zA-Z_]\w*)\s*(?:=|!=|<>|<=|>=|<|>|\bLIKE\b|\bIN\b|\bIS\b|\bBETWEEN\b)",
        where_text, re.IGNORECASE,
    ):
        col = pm.group(1)
        start = pm.start(1)
        if start > 0 and where_text[start - 1] == ".":
            continue  # already-qualified reference, checked above
        if col.lower() in _SQL_RESERVED_FOR_BARE_COLUMNS:
            continue
        cols.append(col)
    return cols


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

    # Case-insensitive table lookup and alias mapping
    schema_tables_lower = {t.lower(): t for t in schema.keys()}
    alias_map = {}
    for tbl_casing in schema.keys():
        t_low = tbl_casing.lower()
        alias_map[t_low] = tbl_casing

    # Find table/alias definitions in FROM and JOIN
    # e.g., FROM salaries s or JOIN departments d
    pattern_table_alias = re.compile(
        r'\b(?:FROM|JOIN)\s+["\'\`]?(\w+)["\'\`]?(?:\s+(?:AS\s+)?["\'\`]?(\w+)["\'\`]?)?',
        re.IGNORECASE
    )
    for m in pattern_table_alias.finditer(sql):
        t_ref = m.group(1)
        a_ref = m.group(2)
        if t_ref.lower() in alias_map:
            actual_t = alias_map[t_ref.lower()]
            alias_map[t_ref.lower()] = actual_t
            if a_ref and a_ref.upper() not in {"ON", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS", "GROUP", "ORDER", "LIMIT", "SET"}:
                alias_map[a_ref.lower()] = actual_t

    if table_name.lower() not in schema_tables_lower and table_name.lower() not in alias_map:
        available = ", ".join(schema.keys()) if schema else "none"
        return {
            "valid": False,
            "reason": (
                f"Table '{table_name}' does not exist in the selected database. "
                f"Available tables: {available}."
            ),
        }

    # Find qualified columns like alias.column or table.column
    pattern_qual_col = re.compile(r'\b(["\'\`]?\w+["\'\`]?)\.(["\'\`]?\w+["\'\`]?)\b')
    for m in pattern_qual_col.finditer(sql):
        prefix = m.group(1).strip('`"\'').lower()
        col_name = m.group(2).strip('`"\'').lower()

        # If prefix is a recognized table or alias in this query
        if prefix in alias_map:
            actual_t = alias_map[prefix]
            actual_cols_lower = {c.lower() for c in schema.get(actual_t, [])}
            if col_name not in actual_cols_lower:
                return {
                    "valid": False,
                    "reason": (
                        f"Column '{m.group(2)}' does not exist in table '{actual_t}'. "
                        f"Available columns in '{actual_t}': {', '.join(schema.get(actual_t, []))}."
                    ),
                }

    # ── Unqualified column check (single-table queries only) ──────────────────
    # Only the qualified form (table.column / alias.column) was checked
    # above — but that's not how most SQL is actually written. A plain
    # "SELECT name, salary FROM employees WHERE age > 30" has no
    # table-qualified columns at all, so name/salary/age previously went
    # completely unchecked here and only failed later, at the database
    # itself, with a raw error instead of this gate's friendly message.
    # Scoped to single-table queries (no JOIN) — with more than one table
    # in play, which table an unqualified column belongs to is genuinely
    # ambiguous from this lightweight extraction, so it's left unchecked
    # rather than risk guessing wrong.
    table_ref_count = len(pattern_table_alias.findall(sql))
    if table_ref_count <= 1 and table_name.lower() in schema_tables_lower:
        actual_t = schema_tables_lower[table_name.lower()]
        actual_cols_lower = {c.lower() for c in schema.get(actual_t, [])}
        for col in _extract_bare_select_columns(sql) + _extract_bare_where_columns(sql):
            if col.lower() not in actual_cols_lower:
                return {
                    "valid": False,
                    "reason": (
                        f"Column '{col}' does not exist in table '{actual_t}'. "
                        f"Available columns in '{actual_t}': {', '.join(schema.get(actual_t, []))}."
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
