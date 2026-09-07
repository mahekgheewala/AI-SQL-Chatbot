"""Regression tests for agent.deterministic_sql_builder's safety net and the
multi-clause query fixes it depends on (multiple aggregations, multiple
comparative filters, "top N by X" ordering, and the grouped-superlative
"top X by Y" phrasing that must defer to the AI Planner instead of running
SQL grouped by a fabricated column).

Run with:
    pytest backend/tests/test_deterministic_sql_builder.py -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.semantic_frame import interpret_message, frame_to_query_intent
from agent.deterministic_sql_builder import build_sql, is_deterministically_executable

PEOPLE_METADATA = {
    "databases": ["people_db"],
    "active_database": "people_db",
    "tables": ["people"],
    "all_tables_by_db": {"people_db": ["people"]},
    "columns_by_table": {
        "people": ["id", "name", "department", "salary", "age", "status", "sales"],
    },
    "column_types_by_table": {
        "people": {"id": "integer", "name": "text", "department": "text",
                   "salary": "numeric", "age": "integer", "status": "text",
                   "sales": "numeric"},
    },
}


def _build(message: str):
    result = interpret_message(message, PEOPLE_METADATA, {})
    qi = frame_to_query_intent(result.frame)
    ok, reason = is_deterministically_executable(qi, raw_message=message)
    return result, qi, ok, reason


def test_multiple_aggregations_build_correct_sql():
    # "total X and average Y by Z" — both aggregations must survive into
    # the generated SQL, not just the first one mentioned.
    result, qi, ok, reason = _build("show total salary and average sales by department")
    assert ok, reason
    sql = build_sql(qi)
    assert "SUM(salary)" in sql
    assert "AVG(sales)" in sql
    assert "GROUP BY department" in sql


def test_two_comparative_filters_build_correct_sql():
    result, qi, ok, reason = _build("find people with age over 30 and salary under 100000")
    assert ok, reason
    sql = build_sql(qi)
    assert "age > '30'" in sql
    assert "salary < '100000'" in sql
    assert " AND " in sql


def test_between_filter_builds_correct_sql():
    result, qi, ok, reason = _build("people with salary between 40000 and 80000")
    assert ok, reason
    sql = build_sql(qi)
    assert "salary >= '40000'" in sql
    assert "salary <= '80000'" in sql


def test_implicit_equality_filter_builds_correct_sql():
    result, qi, ok, reason = _build("people with status active")
    assert ok, reason
    sql = build_sql(qi)
    assert "status = 'active'" in sql


def test_top_n_by_column_builds_order_by_desc():
    # "top 5 X by Y" must produce ORDER BY Y DESC LIMIT 5, not just LIMIT 5
    # with rows returned in arbitrary order.
    result, qi, ok, reason = _build("top 5 people by salary")
    assert ok, reason
    sql = build_sql(qi)
    assert "ORDER BY salary DESC" in sql
    assert "LIMIT 5" in sql


def test_grouped_superlative_defers_to_ai_planner_instead_of_wrong_sql():
    # "top department by total sales" is a phrasing this codebase doesn't
    # fully understand yet (it means "rank departments by SUM(sales)", a
    # GROUP BY + ORDER BY + LIMIT combination the deterministic parser
    # doesn't build). The critical behavior is that it must NOT silently
    # run SQL grouped by a fabricated "total" column — it must refuse
    # deterministic execution and defer to the AI Planner, which can read
    # the full sentence.
    result, qi, ok, reason = _build("top department by total sales")
    assert "total" not in (qi.grouping or [])
    assert not ok
    assert "by total" in reason


def test_filter_on_aggregated_column_defers_instead_of_wrong_where():
    # "departments with average salary over 50000" is genuinely ambiguous:
    # it could mean "only average the rows where salary > 50000" (WHERE,
    # applied before aggregating) or "only keep departments whose average
    # salary clears 50000" (HAVING, applied after aggregating) — this
    # builder only ever renders WHERE, so building this deterministically
    # would silently produce the WHERE reading even when HAVING was meant.
    # Verified against real data (in the investigation, not repeated here):
    # a department whose TRUE average is exactly 50000 (not over it) got
    # wrongly included with a fabricated average, because the WHERE clause
    # dropped its lower salaries before averaging. Must defer instead.
    result, qi, ok, reason = _build("departments with average salary over 50000")
    assert not ok
    assert "salary" in reason


def test_filter_on_different_column_than_aggregation_still_builds():
    # A filter on a DIFFERENT column than the one being aggregated has no
    # such ambiguity — WHERE is unambiguously correct ("average salary by
    # department, only for engineering") and must still build normally.
    result, qi, ok, reason = _build("average salary by department where age > 30")
    assert ok, reason
    sql = build_sql(qi)
    assert "AVG(salary)" in sql
    assert "WHERE age > '30'" in sql
    assert "GROUP BY department" in sql
