"""
Clarification Resolver — LLM-backed slot extraction for stuck clarifications.
================================================================================
Used only when the fast, regex-based clarification-continuation paths in
agent/semantic_frame.py and agent/pending_resolution.py can't cleanly resolve
a user's reply (e.g. a multi-word reply to a "what should I name this table?"
question, or ADD_SAMPLE_DATA needing actual row values generated).

Design:
  - Small, narrow, single-purpose prompts — NOT agent/local_planner.py's
    heavyweight multi-step Planning Document schema. This module only ever
    asks "extract these specific missing fields from this reply," never
    "plan this whole request."
  - Reuses the existing Groq-first/Gemini-fallback calling convention
    (agent/local_planner.py::_call_groq_planner, then ai.model_manager's
    PLANNER_MODEL) rather than introducing a new LLM provider or pattern.
  - Never trusts the LLM's output directly. Every proposed table/column name
    is validated against the live schema via agent/grounding.py before being
    used, and every sample-data value is serialized through
    deterministic_sql_builder._serialize_sql_value — the LLM only ever
    produces structured JSON, never raw SQL text. Any validation failure
    fails closed (ok=False) so the caller falls back to asking again, never
    partially applies an unvalidated guess.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from models.schemas import ColumnSpec
from agent import grounding as G

logger = logging.getLogger("app.ai.clarification_resolver")
_PREFIX = "[CLARIFICATION_RESOLVER]"


# ─── LLM call (Groq-first, Gemini-fallback) ──────────────────────────────────

def _call_llm(prompt: str) -> Optional[str]:
    """Groq-first, Gemini-fallback text completion. Never raises."""
    try:
        from agent.local_planner import _call_groq_planner
        text, _elapsed, _usage = _call_groq_planner(prompt)
        if text:
            return text
    except Exception as e:
        logger.warning(f"{_PREFIX} Groq call failed ({e}); trying Gemini fallback...")

    try:
        from ai.model_manager import PLANNER_MODEL, initialize_models
        from agent.gemini_retry import call_with_retry
        initialize_models()
        if not PLANNER_MODEL:
            return None
        response = call_with_retry(
            lambda: PLANNER_MODEL.generate_content(prompt),
            label="ClarificationResolver Fallback",
        )[0]
        return response.text if response else None
    except Exception as e:
        logger.warning(f"{_PREFIX} Gemini fallback also failed ({e})")
        return None


def _extract_json(text: str) -> Optional[dict]:
    """Pull a JSON object out of raw LLM output — handles <think> blocks and
    markdown fences. Mirrors local_planner._parse_planner_response's
    brace-matching technique, without that function's heavier wrapper-schema
    validation (this module's schemas are much smaller and don't need it)."""
    if not text:
        return None
    stripped = text.strip()
    if "<think>" in stripped and "</think>" in stripped:
        stripped = stripped[stripped.find("</think>") + len("</think>"):].strip()
    if "```" in stripped:
        parts = stripped.split("```")
        if len(parts) >= 3:
            candidate = parts[1].strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            stripped = candidate
    start = stripped.find("{")
    end = stripped.rfind("}") + 1
    if start == -1 or end == 0:
        return None
    try:
        return json.loads(stripped[start:end])
    except json.JSONDecodeError:
        return None


# ─── CREATE TABLE slot extraction ────────────────────────────────────────────

@dataclass
class TableColumnsResolution:
    ok: bool
    table_name: Optional[str] = None
    columns: Optional[List[ColumnSpec]] = None
    reason: Optional[str] = None
    # Names of columns whose type wasn't given by the user and was picked
    # by _guess_column_type() instead — carried through so the caller can
    # tell the user honestly what was assumed, rather than silently
    # defaulting with no visibility (see _build_success_reply() in
    # agent_coordinator.py, which reads this).
    defaulted_columns: List[str] = field(default_factory=list)


# Column-name patterns used to pick a more sensible default type than a
# blanket TEXT when the user didn't specify one — checked in order, first
# match wins. Never silent: resolve_create_table_slots() always reports
# which columns used a guessed type via defaulted_columns above.
_TYPE_GUESS_PATTERNS: List[tuple] = [
    (("_id", "_uuid"), "INTEGER"),
    (("price", "amount", "salary", "cost", "total", "balance", "revenue", "fee", "quantity"), "NUMERIC"),
    (("date", "_at", "time", "created", "updated"), "DATE"),
]
_TYPE_GUESS_PREFIXES = (("is_", "BOOLEAN"), ("has_", "BOOLEAN"))


def _guess_column_type(name: str) -> str:
    """Heuristic default type for a column with no type given, based on
    common naming conventions — used only as a fallback when neither the
    user nor the LLM specified one. Always disclosed to the user
    afterward (see TableColumnsResolution.defaulted_columns), never a
    silent guess."""
    n = name.lower()
    if n == "id":
        return "INTEGER"
    for prefix, sql_type in _TYPE_GUESS_PREFIXES:
        if n.startswith(prefix):
            return sql_type
    for hints, sql_type in _TYPE_GUESS_PATTERNS:
        if any(h in n for h in hints):
            return sql_type
    return "TEXT"


def _build_create_table_prompt(original_request: str, missing: List[str],
                               user_reply: str, metadata: dict) -> str:
    tables = list(metadata.get("tables") or [])
    return f"""/no_think
You are extracting structured information from a user's reply to a database
assistant's clarification question. You are NOT planning, NOT generating
SQL, and NOT executing anything — you only extract what the user actually
said.

CONTEXT:
Original request: {original_request!r}
The assistant still needs: {", ".join(missing) or "(nothing — reconfirm what was given)"}
User's reply: {user_reply!r}

Existing tables in the active database (for awareness only — this is a
CREATE TABLE request, so the table name may be new): {tables}

RULES:
- Extract ONLY what the user actually specified in their reply (and, if
  relevant, the original request). Do not invent a table name, column name,
  column type, or constraint that wasn't stated or clearly implied.
- If something is still not determinable from the text, use null (or an
  empty list, for constraints) for it — do not guess.
- A column the user named without a type should still be included, with
  "type": null.
- If the user described a constraint for a column (primary key, unique,
  required/not null, a default value, or a reference to another table —
  e.g. "id as primary key", "email should be unique", "name is required",
  "customer_id should reference customers(id)"), extract it into that
  column's "constraints" list, written as valid PostgreSQL constraint
  syntax: "PRIMARY KEY", "UNIQUE", "NOT NULL", "DEFAULT <value>", or
  "REFERENCES <table>(<column>)". Do not invent a constraint that wasn't
  stated.

Respond with ONLY a single JSON object, no commentary, no markdown fences:
{{
  "table_name": "<name>" | null,
  "columns": [{{"name": "<name>", "type": "<sql type>" | null, "constraints": ["<constraint>", ...]}}, ...] | null,
  "confidence": <float 0.0-1.0>
}}
"""


def resolve_create_table_slots(
    original_request: str,
    missing: List[str],
    user_reply: str,
    metadata: dict,
    known_table: Optional[str] = None,
) -> TableColumnsResolution:
    from agent.capability_check import _type_allowed, _constraint_allowed
    from agent.pending_resolution import _FORBIDDEN_COLUMN_NAMES

    prompt = _build_create_table_prompt(original_request, missing, user_reply, metadata)
    raw = _call_llm(prompt)
    if not raw:
        return TableColumnsResolution(ok=False, reason="LLM call failed (Groq and Gemini both unavailable)")

    data = _extract_json(raw)
    if not data:
        return TableColumnsResolution(ok=False, reason="Could not parse JSON from LLM response")

    table_name = data.get("table_name")
    if table_name:
        table_name = str(table_name).strip()
        if not table_name or not G.is_identifier(table_name) or G.is_pronoun(table_name):
            return TableColumnsResolution(ok=False, reason=f"LLM-proposed table name {table_name!r} is not a valid identifier")
        grounded_table = G.ground_table(table_name, metadata, allow_fresh=True)
        if grounded_table is None:
            return TableColumnsResolution(ok=False, reason=f"LLM-proposed table name {table_name!r} could not be grounded")
        table_name = grounded_table
    else:
        table_name = None

    columns: Optional[List[ColumnSpec]] = None
    defaulted_columns: List[str] = []
    raw_columns = data.get("columns")
    if raw_columns:
        if not isinstance(raw_columns, list):
            return TableColumnsResolution(ok=False, reason="LLM 'columns' field was not a list")
        seen_names = set()
        parsed_columns: List[ColumnSpec] = []
        for col in raw_columns:
            if not isinstance(col, dict):
                return TableColumnsResolution(ok=False, reason="LLM proposed a malformed column entry")
            name = str(col.get("name") or "").strip()
            if not name or not G.is_identifier(name):
                return TableColumnsResolution(ok=False, reason=f"LLM-proposed column name {name!r} is not a valid identifier")
            if name.lower() in _FORBIDDEN_COLUMN_NAMES:
                return TableColumnsResolution(ok=False, reason=f"LLM-proposed column name {name!r} is a reserved word")
            if name.lower() in seen_names:
                return TableColumnsResolution(ok=False, reason=f"Duplicate column name {name!r}")
            seen_names.add(name.lower())
            col_type = col.get("type")
            col_type = str(col_type).strip() if col_type else None
            if col_type and not _type_allowed(col_type):
                return TableColumnsResolution(ok=False, reason=f"LLM-proposed column type {col_type!r} is not a recognized SQL type")
            if col_type:
                final_type = col_type
            else:
                final_type = _guess_column_type(name)
                defaulted_columns.append(name)
            raw_constraints = col.get("constraints") or []
            if not isinstance(raw_constraints, list):
                return TableColumnsResolution(ok=False, reason=f"LLM-proposed constraints for column {name!r} was not a list")
            constraints: List[str] = []
            for c in raw_constraints:
                c = str(c).strip()
                if not c:
                    continue
                if not _constraint_allowed(c):
                    return TableColumnsResolution(ok=False, reason=f"LLM-proposed constraint {c!r} for column {name!r} is not a recognized/safe constraint")
                constraints.append(c.upper() if c.upper() in {"PRIMARY KEY", "UNIQUE", "NOT NULL", "NULL"} else c)
            parsed_columns.append(ColumnSpec(name=name, type=final_type, constraints=constraints))
        if not parsed_columns:
            return TableColumnsResolution(ok=False, reason="LLM returned an empty columns list")
        columns = parsed_columns

    if not table_name and not columns:
        return TableColumnsResolution(ok=False, reason="LLM could not extract a table name or columns from this reply")

    return TableColumnsResolution(ok=True, table_name=table_name, columns=columns, defaulted_columns=defaulted_columns)


# ─── ADD SAMPLE DATA generation ──────────────────────────────────────────────

@dataclass
class SampleDataResolution:
    ok: bool
    table: Optional[str] = None
    insert_sql: Optional[str] = None
    reason: Optional[str] = None


# Column-name heuristic for "this should be unique" — literal "id", or a
# common unique-identifier naming pattern. Deliberately conservative (no
# bare `endswith("id")`) to avoid false positives like "paid"/"void"/"valid".
def _is_identifier_like_column(name: str) -> bool:
    n = name.lower()
    return n == "id" or n.endswith("_id") or n.endswith("_uuid") or n in {"uuid", "sku", "code"}


def _looks_numeric(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _fetch_existing_column_values(table: str, column: str, target_db: Optional[str], limit: int = 500) -> set:
    """Best-effort read of values already in `column` on `table`, so a new
    sample-data batch doesn't collide with a prior one (e.g. two separate
    "add sample data" requests both generating id "B001"). Returns an empty
    set on any failure — this only sharpens the LLM's context and the
    uniqueness enforcement below; it's never required for correctness."""
    if not target_db:
        return set()
    try:
        from connections.connection_manager import ConnectionManager
        from db.app_database import SessionLocal
        from utils.logging_config import user_id_var
        db_session = SessionLocal()
        try:
            conn = ConnectionManager.get_connection(user_id_var.get(), db_session, target_database=target_db)
            cursor = conn.cursor()
            try:
                cursor.execute(f'SELECT DISTINCT "{column}" FROM "{table}" LIMIT {int(limit)};')
                return {str(row[0]) for row in cursor.fetchall() if row[0] is not None}
            finally:
                cursor.close()
        finally:
            db_session.close()
    except Exception as e:
        logger.warning(f"{_PREFIX} Could not fetch existing values for {table}.{column}: {e}")
        return set()


def _ensure_unique_values(rows: List[Dict[str, Any]], column: str, existing: set, is_numeric: bool) -> None:
    """Mutates `rows` in place so `column`'s value is unique across every
    row and never collides with `existing` — deterministic, not dependent
    on the LLM having gotten this right (it's asked to, but this is the
    actual guarantee)."""
    seen = set(existing)
    for row in rows:
        if column not in row:
            continue
        key = str(row[column]) if row[column] is not None else ""
        if not key or key in seen:
            base = key or "1"
            suffix = 2
            candidate = base
            while candidate in seen:
                if is_numeric and _looks_numeric(base):
                    candidate = str(int(float(base)) + suffix - 1)
                else:
                    candidate = f"{base}-{suffix}"
                suffix += 1
            row[column] = candidate
            seen.add(candidate)
        else:
            seen.add(key)


def _build_sample_data_prompt(original_request: str, user_reply: str, table: str,
                              columns_with_types: Dict[str, str], row_count: int,
                              unique_columns_context: str) -> str:
    cols_desc = ", ".join(f"{c} ({t})" for c, t in columns_with_types.items())
    return f"""/no_think
You are generating plausible sample/dummy data for a database table, based
on a user's request. You are NOT planning and NOT executing anything — you
only produce structured JSON row values.

CONTEXT:
Original request: {original_request!r}
User's reply: {user_reply!r}
Target table: {table!r}
Real columns and their SQL types: {cols_desc}
Number of rows to generate: {row_count}
{unique_columns_context}

RULES:
- Use ONLY the column names listed above — never invent extra columns. You
  may omit an auto-increment-looking id column if the data doesn't need to
  specify it.
- Generate realistic, varied, plausible values appropriate to each column's
  name and type.
- Any column that looks like a unique identifier or primary key (named "id",
  ending in "_id"/"_uuid", or similar) MUST have a DIFFERENT value in every
  row of this batch, and must not reuse any value listed above as already in
  use. Never repeat the same id-like value twice.
- Values are raw data, not SQL — no quotes-as-punctuation, no SQL syntax, no
  explanations inside a value.

Respond with ONLY a single JSON object, no commentary, no markdown fences:
{{
  "table": "{table}",
  "rows": [{{"<column>": <value>, ...}}, ...],
  "confidence": <float 0.0-1.0>
}}
"""


def resolve_sample_data_request(
    original_request: str,
    user_reply: str,
    metadata: dict,
    table_hint: Optional[str] = None,
    row_count_hint: Optional[int] = None,
) -> SampleDataResolution:
    from agent.deterministic_sql_builder import _serialize_sql_value

    table = table_hint
    if table:
        grounded = G.ground_table(table, metadata, allow_fresh=False)
        if grounded is None:
            return SampleDataResolution(ok=False, reason=f"Table {table!r} could not be grounded against the live schema")
        table = grounded

    if not table:
        return SampleDataResolution(ok=False, reason="No table resolved yet — cannot generate sample data")

    columns_by_table = metadata.get("columns_by_table") or {}
    types_by_table = (metadata.get("column_types_by_table") or {}).get(table, {})
    real_columns = list(columns_by_table.get(table) or [])
    if not real_columns:
        return SampleDataResolution(ok=False, reason=f"No known columns for table {table!r}")
    columns_with_types = {c: types_by_table.get(c, "TEXT") for c in real_columns}

    # Identify unique-identifier-looking columns and fetch what's already in
    # the live table for each, so (a) the LLM can be told what to avoid, and
    # (b) uniqueness is enforced deterministically afterward regardless of
    # whether the LLM actually complied.
    target_db = metadata.get("active_database")
    id_like_columns = [c for c in real_columns if _is_identifier_like_column(c)]
    existing_values: Dict[str, set] = {
        c: _fetch_existing_column_values(table, c, target_db) for c in id_like_columns
    }
    if id_like_columns:
        lines = []
        for c in id_like_columns:
            sample_existing = sorted(existing_values[c])[:20]
            lines.append(
                f'  "{c}" — must be unique per row; already in use, do not repeat: '
                + (str(sample_existing) if sample_existing else "(none yet)")
            )
        unique_columns_context = "Unique/identifier columns:\n" + "\n".join(lines)
    else:
        unique_columns_context = ""

    count = row_count_hint if (row_count_hint and 1 <= row_count_hint <= 50) else 5
    prompt = _build_sample_data_prompt(original_request, user_reply, table, columns_with_types, count, unique_columns_context)
    raw = _call_llm(prompt)
    if not raw:
        return SampleDataResolution(ok=False, reason="LLM call failed (Groq and Gemini both unavailable)")

    data = _extract_json(raw)
    if not data:
        return SampleDataResolution(ok=False, reason="Could not parse JSON from LLM response")

    rows = data.get("rows")
    if not rows or not isinstance(rows, list):
        return SampleDataResolution(ok=False, reason="LLM did not return a usable rows list")
    rows = rows[:50]

    resolved_columns: Optional[List[str]] = None
    grounded_rows: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row:
            return SampleDataResolution(ok=False, reason="LLM proposed a malformed row")
        grounded_row: Dict[str, Any] = {}
        for key, value in row.items():
            grounded_col = G.ground_column(str(key), metadata, table=table, allow_fresh=False)
            if grounded_col is None:
                return SampleDataResolution(ok=False, reason=f"LLM-proposed column {key!r} does not exist on table {table!r}")
            grounded_row[grounded_col] = value
        if resolved_columns is None:
            resolved_columns = list(grounded_row.keys())
        elif set(grounded_row.keys()) != set(resolved_columns):
            return SampleDataResolution(ok=False, reason="LLM rows used inconsistent column sets")
        grounded_rows.append(grounded_row)

    if not resolved_columns or not grounded_rows:
        return SampleDataResolution(ok=False, reason="No valid rows extracted")

    # Deterministic uniqueness guarantee — never trust the LLM alone for
    # this, regardless of how clearly the prompt asked for it.
    for c in id_like_columns:
        if c in resolved_columns:
            col_type = columns_with_types.get(c, "")
            is_numeric = any(h in col_type.upper() for h in ("INT", "SERIAL", "NUMERIC", "DECIMAL"))
            _ensure_unique_values(grounded_rows, c, existing_values.get(c, set()), is_numeric)

    value_rows = [[row[c] for c in resolved_columns] for row in grounded_rows]

    cols_sql = ", ".join(resolved_columns)
    values_sql = ", ".join(
        "(" + ", ".join(_serialize_sql_value(v) for v in row) + ")"
        for row in value_rows
    )
    insert_sql = f"INSERT INTO {table} ({cols_sql}) VALUES {values_sql};"

    return SampleDataResolution(ok=True, table=table, insert_sql=insert_sql)


# ─── Chart clarification resolution ──────────────────────────────────────────
# Used when a chart request ("show a pie chart for this table") is grounded
# enough to satisfy the visualize capability's required "chart" role (a
# ChartSpec exists) but not enough to actually build SQL from (no measure
# column) — that combination sends it to the Local Planner (Groq) instead of
# a deterministic clarification, and Groq's own clarification_required has no
# structured way to say "which column" beyond a generic question. This
# resolver interprets the reply (e.g. "id, name, marks", "by department")
# against the real schema and produces a fully-grounded chart spec that
# agent/universal_gateway.py turns into a normal SemanticFrame — reusing the
# exact same deterministic execute_sql + VisualizationEngine path a
# one-shot, fully-specified chart request already uses.

_VALID_CHART_TYPES = {
    "pie", "bar", "line", "scatter", "area", "donut", "histogram", "box",
    "funnel", "gauge", "radar",
}
_VALID_AGGREGATIONS = {"SUM", "AVG", "COUNT", "MIN", "MAX"}


@dataclass
class ChartSlotsResolution:
    ok: bool
    table: Optional[str] = None
    chart_type: Optional[str] = None
    measure: Optional[str] = None
    dimension: Optional[str] = None
    aggregation: Optional[str] = None
    reason: Optional[str] = None


def _build_chart_prompt(original_request: str, user_reply: str, metadata: dict,
                        table: Optional[str]) -> str:
    if table:
        cols = list((metadata.get("columns_by_table") or {}).get(table) or [])
        types = (metadata.get("column_types_by_table") or {}).get(table) or {}
        schema_desc = f"Target table: {table!r}\nColumns: " + ", ".join(
            f"{c} ({types.get(c, 'unknown type')})" for c in cols
        )
    else:
        tables = list(metadata.get("tables") or [])
        schema_desc = f"No table determined yet. Available tables: {tables}"

    return f"""/no_think
You are extracting structured information from a user's reply to a database
assistant's chart clarification question. You are NOT generating SQL — you
only extract which chart the user wants.

CONTEXT:
Original request: {original_request!r}
User's reply: {user_reply!r}
{schema_desc}

RULES:
- Determine: chart type, which column is the measure (the numeric value being
  charted), which column is the dimension (what it's broken down/grouped by,
  if any), and what aggregation (if any) combines multiple rows per dimension
  value.
- Use ONLY column names that are actually listed above — never invent one.
- If the reply just lists column names (e.g. "id, name, marks") without
  saying which is which, use your judgement from the column types: a
  numeric/aggregatable column is usually the measure, a text/categorical
  column is usually the dimension.
- If no table was determined above and the reply doesn't make one clear
  either, leave "table" null.

Respond with ONLY a single JSON object, no commentary, no markdown fences:
{{
  "table": "<name>" | null,
  "chart_type": "bar" | "pie" | "line" | "scatter" | "area" | "donut" | "histogram" | null,
  "measure": "<column>" | null,
  "dimension": "<column>" | null,
  "aggregation": "SUM" | "AVG" | "COUNT" | "MIN" | "MAX" | null,
  "confidence": <float 0.0-1.0>
}}
"""


def resolve_chart_slots(
    original_request: str,
    user_reply: str,
    metadata: dict,
    table_hint: Optional[str] = None,
) -> ChartSlotsResolution:
    table = None
    if table_hint:
        table = G.ground_table(table_hint, metadata, allow_fresh=False)
        if table is None:
            return ChartSlotsResolution(ok=False, reason=f"Table hint {table_hint!r} could not be grounded")

    prompt = _build_chart_prompt(original_request, user_reply, metadata, table)
    raw = _call_llm(prompt)
    if not raw:
        return ChartSlotsResolution(ok=False, reason="LLM call failed (Groq and Gemini both unavailable)")

    data = _extract_json(raw)
    if not data:
        return ChartSlotsResolution(ok=False, reason="Could not parse JSON from LLM response")

    if table is None:
        proposed_table = data.get("table")
        if proposed_table:
            table = G.ground_table(str(proposed_table).strip(), metadata, allow_fresh=False)
    if table is None:
        return ChartSlotsResolution(ok=False, reason="Could not determine a target table for the chart")

    chart_type = data.get("chart_type")
    chart_type = str(chart_type).strip().lower() if chart_type else None
    if chart_type not in _VALID_CHART_TYPES:
        chart_type = None  # left null — the visualization engine picks a sensible default

    measure_word = data.get("measure")
    measure = G.ground_column(str(measure_word).strip(), metadata, table=table, allow_fresh=False) if measure_word else None
    if not measure:
        return ChartSlotsResolution(ok=False, reason="Could not determine a valid measure column for the chart")

    dimension_word = data.get("dimension")
    dimension = G.ground_column(str(dimension_word).strip(), metadata, table=table, allow_fresh=False) if dimension_word else None

    aggregation = data.get("aggregation")
    aggregation = str(aggregation).strip().upper() if aggregation else None
    if aggregation not in _VALID_AGGREGATIONS:
        aggregation = None

    # A dimension being present means the query WILL be aggregated —
    # frame_to_chart_query_intent (agent/semantic_frame.py) defaults a null
    # aggregation to SUM, not "no aggregation" — so a non-numeric measure
    # (e.g. a grade/category stored as text) must not be left to fall
    # through to SUM/AVG/MIN/MAX, which Postgres rejects outright on a text
    # column. COUNT works on any type and is usually what's actually wanted
    # for a non-numeric measure ("how many rows per category").
    if dimension:
        measure_type = ((metadata.get("column_types_by_table") or {}).get(table) or {}).get(measure, "")
        is_numeric_measure = any(h in str(measure_type).upper() for h in G._NUMERIC_TYPE_HINTS)
        if not is_numeric_measure and aggregation != "COUNT":
            aggregation = "COUNT"

    return ChartSlotsResolution(
        ok=True, table=table, chart_type=chart_type,
        measure=measure, dimension=dimension, aggregation=aggregation,
    )
