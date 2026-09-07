"""
Phase 2 — Deterministic SQL Builder
====================================

Translates fully supported structured QueryIntent representations directly into SQL
without calling an AI planner or LLM.

Capability-Based Executability Policy:
- Evaluates whether ALL non-empty structured semantic features present in QueryIntent
  are supported by this builder.
- Returns (False, reason) if ANY represented feature is unsupported, preventing partial
  SQL generation or semantic dropping.
"""

import re
from typing import Optional, Tuple, Dict, Any, List
import logging
from models.schemas import QueryIntent, QueryFilter

logger = logging.getLogger("app.ai.deterministic_sql_builder")

SUPPORTED_FILTER_OPS = {"=", "!=", "<", "<=", ">", ">="}

def _serialize_sql_value(val: Any) -> str:
    """Safely serializes a value into a SQL literal string with single-quote escaping."""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float)):
        return str(val)
    s_val = str(val).replace("'", "''")
    return f"'{s_val}'"


def is_deterministically_executable(
    query_intent: Optional[QueryIntent],
    target_table: Optional[str] = None,
    raw_message: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Evaluates whether the given QueryIntent is 100% deterministically executable by the builder.
    Returns (True, reason) or (False, reason).

    raw_message (optional): the original user message, used only for the
    "unaccounted-for by X" safety check below (step 9). Passing it enables
    a fail-safe fallback to the AI Planner for aggregation phrasings the
    deterministic parser's regexes don't anticipate; omitting it (e.g. the
    internal call from build_sql() below, which never sees the raw
    message) simply skips that one extra check.
    """
    if not query_intent:
        return False, "QueryIntent is None"

    # 1. Operation check
    op = (query_intent.operation or "").upper()
    if op not in {"SELECT"}:
        return False, f"Operation '{op}' is not supported for deterministic SQL construction"

    # 2. Table resolution check
    resolved_table = target_table or (query_intent.entities.tables[0] if (query_intent.entities and query_intent.entities.tables) else None)
    if not resolved_table:
        return False, "Missing target table in QueryIntent"

    # 3. Columns check (supported if empty -> * or list of string identifiers)
    if query_intent.entities and query_intent.entities.columns:
        for col in query_intent.entities.columns:
            if not isinstance(col, str) or not col.strip():
                return False, "Invalid column specification"

    # 4. Constraints check (limit, offset & filters)
    if query_intent.constraints:
        limit = query_intent.constraints.limit
        if limit is not None:
            if not isinstance(limit, int) or limit < 1:
                return False, f"Invalid limit constraint: {limit}"

        offset = query_intent.constraints.offset
        if offset is not None:
            if not isinstance(offset, int) or offset < 0:
                return False, f"Invalid offset constraint: {offset}"

        # Filters check
        filters = query_intent.constraints.filters or []
        if filters:
            for f in filters:
                col = getattr(f, "column", None) or (f.get("column") if isinstance(f, dict) else None)
                f_op = getattr(f, "operator", None) or getattr(f, "op", None) or (f.get("operator") or f.get("op") if isinstance(f, dict) else None)
                val = getattr(f, "value", None) if hasattr(f, "value") else (f.get("value") or f.get("val") if isinstance(f, dict) else None)

                if not col or not isinstance(col, str) or not col.strip():
                    return False, f"Invalid filter column in filter: {f}"
                if not f_op or str(f_op).upper() not in SUPPORTED_FILTER_OPS:
                    return False, f"Unsupported filter operator '{f_op}' in filter: {f}"
                if val is None:
                    return False, f"Filter value is None in filter: {f}"

    # 5. Ordering check
    if query_intent.ordering:
        for item in query_intent.ordering:
            col = getattr(item, "column", None) or (item.get("column") if isinstance(item, dict) else None)
            direction = str(getattr(item, "direction", None) or (item.get("direction") or item.get("dir") if isinstance(item, dict) else "DESC")).upper()
            if not col or direction not in {"ASC", "DESC"}:
                return False, f"Unsupported ordering specification: {item}"

    # 6. Aggregations check
    if query_intent.aggregations:
        _SUPPORTED_AGG_FNS = {"COUNT", "SUM", "AVG", "MIN", "MAX"}
        for agg in query_intent.aggregations:
            fn = getattr(agg, "function", None) or (agg.get("function") if isinstance(agg, dict) else None)
            col = getattr(agg, "column", None) or (agg.get("column") if isinstance(agg, dict) else None)
            if not fn or str(fn).upper() not in _SUPPORTED_AGG_FNS:
                return False, f"Unsupported aggregation function: {fn}"
            if not col:
                return False, f"Aggregation missing column: {agg}"

        # 6b. Filter-on-aggregated-column check — this builder always
        # renders every filter as WHERE (applied to raw rows, BEFORE
        # aggregation), never HAVING (applied to the aggregated result).
        # When a filter's column is the SAME column an aggregation is
        # computed over ("departments with average salary over 50000"),
        # those two meanings genuinely diverge: WHERE-then-AVG silently
        # computes each group's average from only the rows that already
        # pass the filter, which is a DIFFERENT number from "this group's
        # real average, only keep groups where that average clears the
        # bar" (HAVING) — and both are plausible readings of the same
        # sentence. Building either one with silent confidence risks
        # presenting a wrong number as a real answer, so this refuses
        # deterministic execution and defers to the AI Planner instead,
        # which can weigh the full sentence (e.g. an explicit "having").
        agg_columns = {
            str(getattr(agg, "column", None) or (agg.get("column") if isinstance(agg, dict) else "")).lower()
            for agg in query_intent.aggregations
        }
        for f in (query_intent.constraints.filters if query_intent.constraints else None) or []:
            f_col = str(getattr(f, "column", None) or (f.get("column") if isinstance(f, dict) else "")).lower()
            if f_col and f_col in agg_columns:
                return False, (
                    f"Filter on '{f_col}' matches an aggregated column — WHERE vs HAVING is "
                    f"ambiguous here, deferring to AI Planner rather than risk a silently wrong result"
                )

    # 7. Grouping check
    if query_intent.grouping:
        for g in query_intent.grouping:
            if not g or not isinstance(g, str) or not g.strip():
                return False, f"Invalid grouping column: {g}"

    # 8. Comparisons check (must be empty)
    if query_intent.comparisons:
        return False, "Complex comparisons require LocalPlanner SQL reasoning"

    # 9. Safety net: an aggregation together with a trailing "by <word>" in
    #    the raw message that resolves to a real, plausible column name but
    #    isn't reflected in grouping, ordering, or a filter suggests the
    #    deterministic parser's regexes probably missed something (the
    #    "average salary by department" class of bug) — refuse
    #    deterministic execution rather than silently drop it, and let the
    #    caller fall through to the AI Planner instead, which reads the
    #    full sentence and is far more likely to catch it. This is a
    #    fail-safe for phrasings the grouping-detection widening in
    #    semantic_frame.py doesn't anticipate, not a replacement for it.
    if raw_message and query_intent.aggregations:
        m = re.search(r"(?<!order )(?<!sorted )(?<!sort on )\bby\s+([a-z_]+)\b", raw_message.lower())
        if m:
            candidate_col = m.group(1)
            grouped = {str(g).lower() for g in (query_intent.grouping or [])}
            ordered = {
                str(getattr(o, "column", None) or (o.get("column") if isinstance(o, dict) else "") or "").lower()
                for o in (query_intent.ordering or [])
            }
            filtered = {
                str(getattr(f, "column", None) or (f.get("column") if isinstance(f, dict) else "") or "").lower()
                for f in ((query_intent.constraints.filters if query_intent.constraints else None) or [])
            }
            if candidate_col not in grouped and candidate_col not in ordered and candidate_col not in filtered:
                return False, (
                    f"Message contains 'by {candidate_col}' not reflected in grouping/ordering/"
                    f"filters — deferring to AI Planner rather than risk silently dropping it"
                )

    return True, "QueryIntent is fully supported for deterministic SQL construction"


def build_sql(query_intent: QueryIntent, target_table: Optional[str] = None) -> str:
    """
    Constructs a valid SQL query string from a fully supported QueryIntent object.
    Raises ValueError if the QueryIntent is not deterministically executable.
    """
    executable, reason = is_deterministically_executable(query_intent, target_table)
    if not executable:
        raise ValueError(f"Cannot build SQL deterministically: {reason}")

    resolved_table = target_table or (query_intent.entities.tables[0] if (query_intent.entities and query_intent.entities.tables) else None)

    # Build SELECT clause: aggregations take precedence over raw columns
    has_aggs = bool(query_intent.aggregations)
    if has_aggs:
        select_parts = []
        for agg in query_intent.aggregations:
            fn = str(getattr(agg, "function", "") or (agg.get("function") if isinstance(agg, dict) else "")).upper()
            col = getattr(agg, "column", "") or (agg.get("column") if isinstance(agg, dict) else "")
            select_parts.append(f"{fn}({col})")
        if query_intent.grouping:
            for g in query_intent.grouping:
                g_str = str(g).strip()
                if g_str and g_str not in select_parts:
                    select_parts.append(g_str)
        cols = ", ".join(select_parts)
    else:
        cols = "*"
        if query_intent.entities and query_intent.entities.columns:
            cols = ", ".join(query_intent.entities.columns)

    sql_parts = [f"SELECT {cols} FROM {resolved_table}"]

    # WHERE filters
    if query_intent.constraints and query_intent.constraints.filters:
        where_clauses = []
        for f in query_intent.constraints.filters:
            col = getattr(f, "column", None) or (f.get("column") if isinstance(f, dict) else None)
            f_op = str(getattr(f, "operator", None) or getattr(f, "op", None) or (f.get("operator") or f.get("op") if isinstance(f, dict) else "=")).upper()
            val = getattr(f, "value", None) if hasattr(f, "value") else (f.get("value") or f.get("val") if isinstance(f, dict) else None)

            val_repr = _serialize_sql_value(val)
            where_clauses.append(f"{col} {f_op} {val_repr}")
        if where_clauses:
            sql_parts.append("WHERE " + " AND ".join(where_clauses))

    # GROUP BY
    if query_intent.grouping:
        group_clauses = [str(g).strip() for g in query_intent.grouping if g]
        if group_clauses:
            sql_parts.append("GROUP BY " + ", ".join(group_clauses))

    # ORDER BY
    if query_intent.ordering:
        order_clauses = []
        for item in query_intent.ordering:
            col = getattr(item, "column", None) or (item.get("column") if isinstance(item, dict) else None)
            direction = str(getattr(item, "direction", None) or (item.get("direction") or item.get("dir") if isinstance(item, dict) else "DESC")).upper()
            order_clauses.append(f"{col} {direction}")
        if order_clauses:
            sql_parts.append("ORDER BY " + ", ".join(order_clauses))

    # LIMIT
    if query_intent.constraints and query_intent.constraints.limit is not None:
        sql_parts.append(f"LIMIT {query_intent.constraints.limit}")

    # OFFSET
    if query_intent.constraints and query_intent.constraints.offset is not None:
        sql_parts.append(f"OFFSET {query_intent.constraints.offset}")

    return " ".join(sql_parts) + ";"
