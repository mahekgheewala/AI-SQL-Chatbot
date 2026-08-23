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


def is_deterministically_executable(query_intent: Optional[QueryIntent], target_table: Optional[str] = None) -> Tuple[bool, str]:
    """
    Evaluates whether the given QueryIntent is 100% deterministically executable by the builder.
    Returns (True, reason) or (False, reason).
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

    # 7. Grouping check
    if query_intent.grouping:
        for g in query_intent.grouping:
            if not g or not isinstance(g, str) or not g.strip():
                return False, f"Invalid grouping column: {g}"

    # 8. Comparisons check (must be empty)
    if query_intent.comparisons:
        return False, "Complex comparisons require LocalPlanner SQL reasoning"

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
