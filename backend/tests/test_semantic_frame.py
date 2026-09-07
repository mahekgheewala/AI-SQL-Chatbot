"""Stage 2 unit tests: SemanticFrame interpretation pipeline.

Covers action detection, typed role parsing, entity grounding, capability
completeness checks, and multi-turn continuation — all against synthetic
metadata (no database or network access).

Run with:
    pytest backend/tests/test_semantic_frame.py -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from agent.semantic_frame import (
    interpret_message,
    STATUS_SUCCESS,
    STATUS_CLARIFY,
    STATUS_FAILED,
)
from models.schemas import SemanticFrame, ChartSpec

# ─── Synthetic metadata matching the baseline hr_database fixture ────────────

HR_METADATA = {
    "databases": ["hr_database", "another"],
    "active_database": "hr_database",
    "tables": ["employees", "departments"],
    "all_tables_by_db": {
        "hr_database": ["employees", "departments"],
        "another": ["students"],
    },
    "columns_by_table": {
        "employees": ["id", "name", "department", "salary", "hire_date"],
        "departments": ["id", "name"],
        "students": ["id", "name", "major", "gpa"],
    },
    "column_types_by_table": {
        "employees": {"id": "integer", "name": "text", "department": "text",
                      "salary": "numeric", "hire_date": "date"},
        "departments": {"id": "integer", "name": "text"},
        "students": {"id": "integer", "name": "text", "major": "text", "gpa": "numeric"},
    },
}

# A deliberately non-HR schema (no "employee"/"salary"/"department" anywhere)
# used to prove attribute resolution is schema-driven, not tuned to any
# particular business vocabulary.
WIDGETS_METADATA = {
    "databases": ["widgets_db"],
    "active_database": "widgets_db",
    "tables": ["widgets"],
    "all_tables_by_db": {"widgets_db": ["widgets"]},
    "columns_by_table": {
        "widgets": ["id", "sku", "price", "weight_kg", "restocked_at"],
    },
    "column_types_by_table": {
        "widgets": {"id": "integer", "sku": "text", "price": "numeric",
                    "weight_kg": "numeric", "restocked_at": "date"},
    },
}


def run(message: str, context: dict = None):
    return interpret_message(message, HR_METADATA, context or {})


# Dedicated fixture for the multi-clause / comparative-filter regression
# tests below — has an "age" and "status" column that HR_METADATA
# deliberately does NOT have (other tests above rely on "no age-like
# column exists on employees" as their premise; adding one there would
# break those tests' meaning).
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


def run_people(message: str, context: dict = None):
    return interpret_message(message, PEOPLE_METADATA, context or {})


# ─── Greetings ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", ["hello", "hi", "hey", "thanks", "thank you",
                                 "goodbye", "bye"])
def test_greetings(msg):
    r = run(msg)
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "general_conversation"
    assert r.frame.action == "converse"


# ─── Understanding failures ──────────────────────────────────────────────────

def test_empty_message_is_failed():
    r = run("")
    assert r.status == STATUS_FAILED
    assert r.frame.capability_id == "understanding_failed"


def test_gibberish_is_failed():
    r = run("purple monkey dishwasher")
    assert r.status == STATUS_FAILED
    assert r.frame.capability_id == "understanding_failed"


# ─── Create database ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg,db", [
    ("create a database called sales", "sales"),
    ("create database sales", "sales"),
    ("please create a database named analytics", "analytics"),
])
def test_create_database_fresh_name(msg, db):
    r = run(msg)
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "create_database"
    assert r.frame.database == db


def test_create_database_existing_name():
    r = run("create a database named hr_database")
    assert r.status == STATUS_SUCCESS
    assert r.frame.database == "hr_database"


def test_create_database_missing_name():
    r = run("create a database")
    assert r.status == STATUS_CLARIFY
    assert r.frame.capability_id == "create_database"
    assert "database" in r.frame.missing_required
    assert "name" in (r.clarification_message or "")


# ─── Create table ────────────────────────────────────────────────────────────

def test_create_table_with_columns():
    r = run("create table employees with columns name text, salary integer")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "create_table"
    assert r.frame.table == "employees"
    assert [c.name for c in r.frame.columns] == ["name", "salary"]
    assert r.frame.columns[0].type == "TEXT"
    assert r.frame.columns[1].type == "INTEGER"


def test_create_table_parenthesized():
    r = run("CREATE TABLE sales_orders (id integer, total numeric)")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "create_table"
    assert r.frame.table == "sales_orders"
    assert [c.name for c in r.frame.columns] == ["id", "total"]
    assert r.frame.columns[1].type == "NUMERIC"


def test_create_table_missing_name():
    r = run("create a table with columns id integer, name text")
    assert r.status == STATUS_CLARIFY
    assert r.frame.capability_id == "create_table"
    assert "table" in r.frame.missing_required
    assert "name" in (r.clarification_message or "")


def test_create_table_missing_columns():
    r = run("create a table called customers")
    assert r.status == STATUS_CLARIFY
    assert "columns" in r.frame.missing_required
    assert "column" in (r.clarification_message or "").lower()


# ─── Alter column ────────────────────────────────────────────────────────────

def test_add_column():
    r = run("add column email to employees")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "add_column"
    assert r.frame.alter.operation == "ADD_COLUMN"
    assert r.frame.alter.column == "email"
    assert r.frame.table == "employees"


def test_add_column_with_type():
    r = run("add column email text to employees")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "add_column"
    assert r.frame.alter.column == "email"
    assert r.frame.alter.new_type == "TEXT"


def test_drop_column():
    r = run("drop column salary from employees")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "drop_column"
    assert r.frame.alter.operation == "DROP_COLUMN"
    assert r.frame.alter.column == "salary"


def test_drop_column_not_found_is_clarification():
    r = run("drop column email from employees")
    assert r.status == STATUS_CLARIFY
    assert r.frame.capability_id == "drop_column"
    assert "email" in (r.clarification_message or "")


def test_add_column_missing_table():
    r = run("add a column called status")
    assert r.status == STATUS_CLARIFY
    assert r.frame.capability_id == "add_column"
    assert "table" in r.frame.missing_required


def test_drop_column_name_before_keyword():
    # "drop the X column" — the column name comes BEFORE the word "column",
    # not after. Only "drop column X" (name after) was handled; this at
    # least as natural phrasing found no column name at all despite one
    # being named right there in the sentence.
    r = run("drop the salary column from employees")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "drop_column"
    assert r.frame.alter.column == "salary"
    assert r.frame.table == "employees"


def test_drop_column_does_not_grab_table_name_across_from():
    # The forward scan used to skip straight past "from" looking for the
    # next identifier, which could walk past the column/table boundary and
    # grab the TABLE name as if it were the column ("...from employees" ->
    # column="employees").
    r = run("delete the salary column from employees")
    assert r.status == STATUS_SUCCESS
    assert r.frame.alter.column == "salary"
    assert r.frame.alter.column != "employees"


def test_alter_column_followup_carries_table_from_prior_turn():
    # "now drop the status column" right after "describe employees" names
    # no table at all — same missing-context-threading gap
    # _parse_add_sample_data had before it was fixed.
    prior = SemanticFrame(action="describe", object_type="TABLE",
                          capability_id="describe_table", table="employees")
    r = run("drop the salary column", {"prior_frames": [prior]})
    assert r.status == STATUS_SUCCESS
    assert r.frame.table == "employees"
    assert r.frame.alter.column == "salary"


# ─── Add sample data ─────────────────────────────────────────────────────────

def test_add_sample_data_with_count():
    r = run("add 20 sample rows to employees")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "add_sample_data"
    assert r.frame.sample_data.count == 20
    assert r.frame.table == "employees"


def test_add_sample_data_without_count():
    r = run("add sample data to employees")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "add_sample_data"
    assert r.frame.sample_data.count is None
    assert r.frame.table == "employees"


def test_add_sample_data_missing_table():
    r = run("add sample data")
    assert r.status == STATUS_CLARIFY
    assert r.frame.capability_id == "add_sample_data"
    assert "table" in r.frame.missing_required


def test_add_sample_data_followup_carries_table_from_prior_turn():
    # Found via a multi-turn follow-up audit: "add sample data to
    # employees" then "add 5 more" — the second message names no table at
    # all. _parse_retrieve/_parse_visualize already fall back to the prior
    # turn's table in this situation; _parse_add_sample_data never did,
    # losing the table entirely on the very next turn.
    prior = SemanticFrame(action="add", object_type="DATA",
                          capability_id="add_sample_data", table="employees")
    r = run("add 5 more", {"prior_frames": [prior]})
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "add_sample_data"
    assert r.frame.table == "employees"
    assert r.frame.sample_data.count == 5


def test_add_sample_data_bare_more_recognized_as_action():
    # "add more" (no number, no "rows"/"records"/"sample data") previously
    # matched none of the add_sample_data action triggers at all and fell
    # through to capability_id="understanding_failed" — there is no other
    # capability "add more" could sensibly mean in this app.
    prior = SemanticFrame(action="add", object_type="DATA",
                          capability_id="add_sample_data", table="employees")
    r = run("add more", {"prior_frames": [prior]})
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "add_sample_data"
    assert r.frame.table == "employees"


def test_add_sample_data_count_from_bare_more():
    # "add 10 more" — no "rows"/"records"/"data"/etc. at all, just "more".
    # The count regex only recognized a fixed unit-word list that didn't
    # include "more", so "10" was silently discarded and the caller
    # defaulted to a hardcoded 5 regardless of what was actually asked for.
    prior = SemanticFrame(action="add", object_type="DATA",
                          capability_id="add_sample_data", table="employees")
    r = run("add 10 more", {"prior_frames": [prior]})
    assert r.frame.sample_data.count == 10


# ─── Visualize ───────────────────────────────────────────────────────────────

def test_visualize_bar_chart():
    r = run("show a bar chart of average salary by department")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "visualize"
    assert r.frame.chart.chart_type == "bar"
    assert r.frame.chart.measure == "salary"
    assert r.frame.chart.dimension == "department"


def test_visualize_pie_chart_with_table():
    r = run("show a pie chart of employees by department")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "visualize"
    assert r.frame.chart.chart_type == "pie"
    assert r.frame.chart.dimension == "department"
    assert r.frame.table == "employees"


def test_visualize_unresolvable_measure_asks_instead_of_building_empty_chart():
    # "bogus" is not a real column on any table — the completeness check
    # used to only verify a ChartSpec object existed at all, not that its
    # measure actually resolved, so this silently built a chart with
    # measure=None (nothing to plot) instead of asking what to plot.
    r = run("plot bogus by department")
    assert r.status == STATUS_CLARIFY
    assert "measure" in r.frame.missing_required


def test_visualize_chart_type_followup_keeps_prior_measure_and_dimension():
    # "now show it as a pie chart" only changes the chart TYPE — a person
    # wouldn't re-state "salary by department" just to switch chart types.
    # Without inheriting the prior turn's chart spec, the new chart
    # silently had no measure or dimension at all.
    prior = SemanticFrame(action="visualize", object_type="CHART", capability_id="visualize",
                          table="employees",
                          chart=ChartSpec(chart_type="bar", measure="salary", dimension="department"))
    r = run("now show it as a pie chart", {"prior_frames": [prior]})
    assert r.status == STATUS_SUCCESS
    assert r.frame.chart.chart_type == "pie"
    assert r.frame.chart.measure == "salary"
    assert r.frame.chart.dimension == "department"


# ─── Switch database ─────────────────────────────────────────────────────────

def test_switch_database():
    r = run("switch to another")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "switch_database"
    assert r.frame.database == "another"


def test_switch_database_not_found_is_clarification():
    r = run("switch to nonexistent")
    assert r.status == STATUS_CLARIFY
    assert r.frame.capability_id == "switch_database"
    assert "nonexistent" in (r.clarification_message or "")


def test_switch_database_no_target():
    r = run("switch database")
    assert r.status == STATUS_CLARIFY
    assert "Which database" in (r.clarification_message or "")


def test_bare_use_of_nonexistent_word_is_not_database_switch():
    # "now use headcount instead" (a chart follow-up asking for a
    # different measure) used to always be read as "switch database to
    # headcount" purely because it contains the word "use" — even though
    # "headcount" isn't a database at all. "use" alone is genuinely
    # ambiguous; only commit to database-switch when the word after it is
    # a real database, or "database"/"db" is explicitly said.
    r = run("now use headcount instead")
    assert r.frame.capability_id != "switch_database"


def test_use_with_real_database_still_switches():
    # "use" on its own DOES still mean switch-database when it's actually
    # followed by a real database name — this fix must not break that.
    r = run("use another")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "switch_database"
    assert r.frame.database == "another"


# ─── List / describe ─────────────────────────────────────────────────────────

def test_list_tables():
    r = run("show tables")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "list_tables"


def test_list_databases():
    r = run("show databases")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "list_databases"


def test_describe_table():
    r = run("describe employees")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "describe_table"
    assert r.frame.table == "employees"


def test_describe_table_not_found_is_clarification():
    r = run("describe nonexistent_table")
    assert r.status == STATUS_CLARIFY
    assert "nonexistent_table" in (r.clarification_message or "")


# ─── Retrieve ────────────────────────────────────────────────────────────────

def test_retrieve_all_rows():
    r = run("show all employees")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "retrieve"
    assert r.frame.table == "employees"


def test_retrieve_filter_greater_than():
    r = run("show employees with salary greater than 50000")
    assert r.status == STATUS_SUCCESS
    assert len(r.frame.filters) == 1
    f = r.frame.filters[0]
    assert f.column == "salary"
    assert f.operator == ">"
    assert f.value == "50000"


def test_retrieve_filter_where():
    r = run("show employees where salary > 60000")
    assert r.status == STATUS_SUCCESS
    assert len(r.frame.filters) == 1
    assert r.frame.filters[0].column == "salary"
    assert r.frame.filters[0].operator == ">"


def test_retrieve_filter_in():
    r = run("show employees in the sales department")
    assert r.status == STATUS_SUCCESS
    assert r.frame.filters[0].column == "department"
    assert r.frame.filters[0].value == "sales"


def test_retrieve_filter_date():
    r = run("show employees hired after 2020")
    assert r.status == STATUS_SUCCESS
    assert r.frame.filters[0].column == "hire_date"
    assert r.frame.filters[0].operator == ">"
    assert r.frame.filters[0].value == "2020"


def test_retrieve_limit():
    r = run("give me the top 5 employees")
    assert r.status == STATUS_SUCCESS
    assert r.frame.limit == 5


def test_retrieve_columns():
    r = run("show me name, salary from employees")
    assert r.status == STATUS_SUCCESS
    assert [c.name for c in r.frame.columns] == ["name", "salary"]


def test_retrieve_bad_filter_column_is_clarification():
    r = run("show employees with nonexistentcol greater than 5")
    assert r.status == STATUS_CLARIFY
    assert "nonexistentcol" in (r.clarification_message or "")


def test_retrieve_typo_filter_does_not_duplicate():
    # "salry" (typo for "salary") gets caught by TWO independent filter
    # parsers in the same pass — one keeps the raw typo'd spelling, the
    # other resolves it correctly via fuzzy matching — so they look like
    # different filters until BOTH get grounded to the real column name.
    # The dedup pass in _build_frame runs before grounding, so it missed
    # this; only a second dedup after grounding catches it.
    r = run("show employees with salry over 50000")
    assert r.status == STATUS_SUCCESS
    assert len(r.frame.filters) == 1
    assert r.frame.filters[0].column == "salary"


# ─── Superlatives & comparatives — schema-driven, never a hardcoded word map ─

def test_superlative_scalar_aggregation_resolves_real_column():
    r = run("what is the highest salary in employees")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "retrieve"
    assert len(r.frame.aggregations) == 1
    assert r.frame.aggregations[0].function == "MAX"
    assert r.frame.aggregations[0].column == "salary"


def test_superlative_ordering_resolves_real_column():
    r = run("show the lowest salary in employees")
    assert r.status == STATUS_SUCCESS
    assert r.frame.ordering
    assert r.frame.ordering[0].column == "salary"
    assert r.frame.ordering[0].direction == "ASC"
    assert r.frame.limit == 1


def test_superlative_unresolvable_word_asks_instead_of_guessing():
    # "paid" has no lexical relationship to "salary" — the assistant must ask
    # rather than silently apply the old hardcoded _ATTRIBUTE_VERBS guess.
    r = run("show the highest paid employees")
    assert r.status == STATUS_CLARIFY
    assert "ordering_column" in r.frame.missing_required
    assert "salary" in (r.clarification_message or "")


def test_superlative_generalizes_to_a_non_hr_schema():
    # Same code path, an unrelated domain (widgets, not HR) — proves
    # resolution is schema-driven, not tuned to any business vocabulary.
    r = interpret_message("what is the highest price in widgets", WIDGETS_METADATA, {})
    assert r.status == STATUS_SUCCESS
    assert r.frame.aggregations[0].function == "MAX"
    assert r.frame.aggregations[0].column == "price"


def test_comparative_filter_resolves_real_column():
    r = run("show employees with salary at least 50000")
    assert r.status == STATUS_SUCCESS
    filters = [f for f in r.frame.filters if f.column == "salary"]
    assert filters and filters[0].operator == ">="
    assert filters[0].value == "50000"


def test_comparative_filter_implicit_attribute_no_match_asks():
    # No age/date-of-birth-like column exists on employees — must ask rather
    # than guessing salary or hire_date for "older".
    r = run("show employees older than 30")
    assert r.status == STATUS_CLARIFY
    assert "ordering_column" in r.frame.missing_required


# ─── Multi-clause / "universal query" regressions ────────────────────────────
# One message filtering on more than one column, or phrasing a filter in a
# way that has no explicit operator word, or a comparative sentence whose
# real column word sits behind a grammar word ("is", "are") — all silently
# dropped data or asked an unanswerable question before being fixed. Each
# test below pins down the exact broken sentence so a future change to
# semantic_frame.py can't quietly reintroduce the same drop.

def test_retrieve_two_comparative_filters_on_different_columns():
    # Previously only the FIRST "<col> over/under N" clause survived —
    # re.search() found one match and never looked for a second.
    r = run_people("find people with age over 30 and salary under 100000")
    assert r.status == STATUS_SUCCESS
    filters = {(f.column, f.operator, f.value) for f in r.frame.filters}
    assert ("age", ">", "30") in filters
    assert ("salary", "<", "100000") in filters


def test_retrieve_older_than_resolves_to_age_column():
    # "older than" used to be resolved as if the WORD "older" itself were a
    # candidate column name — it never matches any real column, so this
    # incorrectly asked for clarification even when a real "age" column
    # exists right there in the schema.
    r = run_people("find people older than 30")
    assert r.status == STATUS_SUCCESS
    filters = [f for f in r.frame.filters if f.column == "age"]
    assert filters and filters[0].operator == ">" and filters[0].value == "30"


def test_retrieve_comparative_filter_skips_grammar_word():
    # The word directly before "over" here is "is" (a grammar word), not
    # the real attribute "age" — used to be taken literally, fail to
    # resolve, and wrongly ask for clarification even though "age over 30"
    # further left in the sentence already parsed correctly on its own.
    r = run_people("find people whose age is over 30")
    assert r.status == STATUS_SUCCESS
    filters = [f for f in r.frame.filters if f.column == "age"]
    assert filters and filters[0].operator == ">" and filters[0].value == "30"


def test_retrieve_top_n_by_column_infers_ordering():
    # "top 5 X by Y" means the 5 HIGHEST Y, not 5 arbitrary rows — the "by
    # salary" half used to be silently dropped, leaving a LIMIT with no
    # ORDER BY at all.
    r = run_people("top 2 people by salary")
    assert r.status == STATUS_SUCCESS
    assert r.frame.limit == 2
    assert r.frame.ordering
    assert r.frame.ordering[0].column == "salary"
    assert r.frame.ordering[0].direction == "DESC"


def test_retrieve_grouped_superlative_does_not_fabricate_group_column():
    # "top department by total sales" used to have the grouping safety net
    # grab "total" (an aggregation-function WORD, not a real column) back
    # out of "total sales" and treat it as the grouping dimension — this
    # silently ran SQL grouped by a column that doesn't exist. It must not
    # invent a grouping column; deterministic_sql_builder's own safety net
    # (tested separately in test_deterministic_sql_builder.py) is what
    # sends this to the AI Planner instead once group_by is correctly empty.
    r = run_people("top department by total sales")
    assert r.status == STATUS_SUCCESS
    assert "total" not in r.frame.group_by
    assert len(r.frame.aggregations) == 1
    assert r.frame.aggregations[0].function == "SUM"
    assert r.frame.aggregations[0].column == "sales"


def test_retrieve_implicit_equality_with_no_operator_word():
    # "with status active" has no "="/"is"/"are" between the column and the
    # value — this used to match nothing at all and silently ran an
    # unfiltered SELECT *.
    r = run_people("people with status active")
    assert r.status == STATUS_SUCCESS
    filters = [f for f in r.frame.filters if f.column == "status"]
    assert filters and filters[0].operator == "=" and filters[0].value == "active"


def test_retrieve_implicit_equality_does_not_fabricate_from_descriptive_phrase():
    # "high" is not a real column — must NOT invent a filter col="high"
    # val="salary" just because it has the same "with <word> <word>" shape
    # as a real implicit-equality filter.
    r = run_people("people with high salary")
    assert r.status == STATUS_SUCCESS
    assert r.frame.filters == []


def test_retrieve_implicit_equality_does_not_collide_with_comparative_filter():
    # "with salary greater than 50000" must produce ONLY the real
    # comparative filter — the implicit-equality fallback used to also
    # match "salary greater" as if "greater" were a literal value,
    # producing a bogus second filter alongside the correct one.
    r = run_people("people with salary greater than 50000")
    assert r.status == STATUS_SUCCESS
    assert len(r.frame.filters) == 1
    assert r.frame.filters[0].column == "salary"
    assert r.frame.filters[0].operator == ">"
    assert r.frame.filters[0].value == "50000"


def test_retrieve_between_filter():
    # "between X and Y" was not recognized at all — silently ran an
    # unfiltered SELECT * instead of the two-sided range the user asked for.
    r = run_people("people with salary between 40000 and 80000")
    assert r.status == STATUS_SUCCESS
    filters = {(f.column, f.operator, f.value) for f in r.frame.filters}
    assert ("salary", ">=", "40000") in filters
    assert ("salary", "<=", "80000") in filters


def test_retrieve_where_clause_chained_with_and():
    # "where A = B and C = D" — only the FIRST "where"-anchored clause was
    # ever extracted; every clause chained after it with "and" (which has
    # no "where" of its own) was silently dropped.
    r = run_people("show people where salary > 50000 and age > 30")
    filters = {(f.column, f.operator, f.value) for f in r.frame.filters}
    assert ("salary", ">", "50000") in filters
    assert ("age", ">", "30") in filters


def test_retrieve_with_equals_chained_with_and():
    # Same gap as above, for "with A = B and C = D" phrasing.
    r = run_people("people with department = sales and status = active")
    filters = {(f.column, f.operator, f.value) for f in r.frame.filters}
    assert ("department", "=", "sales") in filters
    assert ("status", "=", "active") in filters


def test_retrieve_two_different_comparative_constructs():
    # "at least" and "at most" are two DIFFERENT _COMPARATIVE_OPS pattern
    # types — the old code returned as soon as the FIRST pattern type
    # matched anywhere in the message, so the second construct (a
    # completely different column) was silently dropped even though
    # nothing about it was actually ambiguous.
    r = run_people("people with salary at least 50000 and age at most 60")
    filters = {(f.column, f.operator, f.value) for f in r.frame.filters}
    assert ("salary", ">=", "50000") in filters
    assert ("age", "<=", "60") in filters


# ─── Knowledge (conceptual SQL/DB questions) ─────────────────────────────────

def test_knowledge_question_routes_to_knowledge_capability():
    r = run("what is a foreign key")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "knowledge"


def test_greeting_still_routes_to_general_conversation():
    r = run("hi there")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "general_conversation"


# ─── Raw SQL ─────────────────────────────────────────────────────────────────

def test_raw_sql_select():
    r = run("SELECT * FROM employees")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "raw_sql"
    assert r.frame.raw_sql == "SELECT * FROM employees"


def test_raw_sql_insert():
    r = run("INSERT INTO employees (name) VALUES ('alice')")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "raw_sql"


def test_ddl_opener_is_not_raw_sql():
    r = run("CREATE TABLE foo (id INT)")
    assert r.frame.capability_id == "create_table"
    assert r.status == STATUS_SUCCESS
    assert r.frame.table == "foo"


# ─── Drop / rename ───────────────────────────────────────────────────────────

def test_drop_table():
    r = run("drop table employees")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "drop_table"
    assert r.frame.table == "employees"


def test_drop_table_not_found_is_clarification():
    r = run("drop table nonexistent")
    assert r.status == STATUS_CLARIFY
    assert r.frame.capability_id == "drop_table"
    assert "nonexistent" in (r.clarification_message or "")


def test_drop_database():
    r = run("drop database another")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "drop_database"
    assert r.frame.database == "another"


def test_rename_table():
    r = run("rename table employees to staff")
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "rename_table"
    assert r.frame.table == "employees"


# ─── Multi-turn continuation & context ───────────────────────────────────────

def test_continuation_fills_table_name():
    r1 = run("create a table with columns id integer")
    assert r1.status == STATUS_CLARIFY
    assert "table" in r1.frame.missing_required
    r2 = run("customers", {"pending_frame": r1.frame})
    assert r2.status == STATUS_SUCCESS
    assert r2.frame.capability_id == "create_table"
    assert r2.frame.table == "customers"
    assert r2.frame.is_clarification_response


def test_continuation_fills_columns():
    r1 = run("create a table called teams")
    assert r1.status == STATUS_CLARIFY
    assert "columns" in r1.frame.missing_required
    r2 = run("with columns name text, salary integer", {"pending_frame": r1.frame})
    assert r2.status == STATUS_SUCCESS
    assert r2.frame.capability_id == "create_table"
    assert r2.frame.table == "teams"
    assert [c.name for c in r2.frame.columns] == ["name", "salary"]


def test_fragment_for_pending_multiword_reply_uses_llm_fallback():
    """Reproduces the reported bug: a multi-word reply naming BOTH the table
    and the columns in one sentence ("name it books, and add two columns:
    name and id") skips the single-token table check and the plain-token
    column-harvest fallback entirely (guarded off when 'table' is still
    missing — see _fragment_for_pending), and must resolve via the LLM
    fallback instead. Also proves the literal word "name" survives as a real
    column — the exact word the app's own _NON_ENTITY_WORDS blocklist would
    otherwise silently drop."""
    from unittest.mock import patch

    metadata = {
        "databases": ["baby_world"],
        "active_database": "baby_world",
        "tables": [],
        "all_tables_by_db": {"baby_world": []},
        "columns_by_table": {},
        "column_types_by_table": {},
    }

    r1 = interpret_message("create a table in it", metadata, {})
    assert r1.status == STATUS_CLARIFY
    assert set(r1.frame.missing_required) == {"table", "columns"}

    with patch("agent.local_planner._call_groq_planner") as mock_groq:
        mock_groq.return_value = (
            '{"table_name": "books", "columns": '
            '[{"name": "id", "type": "INTEGER"}, {"name": "name", "type": "TEXT"}], '
            '"confidence": 0.95}',
            0.1,
        )
        r2 = interpret_message(
            "name it books, and add two columns: name and id",
            metadata,
            {
                "pending_frame": r1.frame,
                "original_request": "create a table in it",
            },
        )

    assert r2.status == STATUS_SUCCESS
    assert r2.frame.capability_id == "create_table"
    assert r2.frame.table == "books"
    col_names = {c.name for c in r2.frame.columns}
    assert col_names == {"id", "name"}


def test_pronoun_resolution_uses_prior_table():
    prior = SemanticFrame(action="select", object_type="DATA",
                          capability_id="retrieve", table="employees")
    r = run("filter it by salary", {"prior_frames": [prior]})
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "retrieve"
    assert r.frame.table == "employees"


def test_active_database_context():
    r = run("show students", {"active_database": "another"})
    assert r.status == STATUS_SUCCESS
    assert r.frame.capability_id == "retrieve"
    assert r.frame.table == "students"
    assert r.frame.database == "another"
