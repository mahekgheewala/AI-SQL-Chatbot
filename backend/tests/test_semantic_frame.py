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
from models.schemas import SemanticFrame

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
