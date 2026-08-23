import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from agent.ddl_parser import parse_simple_ddl, DDLParseResult

def test_parse_simple_ddl_create_database():
    # Basic
    res = parse_simple_ddl("Create database company")
    assert res is not None
    assert res.operation == "CREATE_DATABASE"
    assert res.sql == "CREATE DATABASE company;"
    assert res.deterministic is True
    assert res.clarification_needed is False

    # Polite prefixes
    res = parse_simple_ddl("Please create a database called company")
    assert res is not None
    assert res.sql == "CREATE DATABASE company;"

    # Polite suffixes / company first
    res = parse_simple_ddl("Can you create company database")
    assert res is not None
    assert res.sql == "CREATE DATABASE company;"

    # Unsupported
    res = parse_simple_ddl("Create a database suitable for HR")
    assert res is None


def test_parse_simple_ddl_drop_database():
    res = parse_simple_ddl("Drop database sales")
    assert res is not None
    assert res.operation == "DROP_DATABASE"
    assert res.sql == "DROP DATABASE sales;"

    res = parse_simple_ddl("Could you delete sales database")
    assert res is not None
    assert res.sql == "DROP DATABASE sales;"


def test_parse_simple_ddl_drop_table():
    res = parse_simple_ddl("Drop table employees")
    assert res is not None
    assert res.operation == "DROP_TABLE"
    assert res.sql == "DROP TABLE employees;"

    res = parse_simple_ddl("Delete employees table")
    assert res is not None
    assert res.sql == "DROP TABLE employees;"


def test_parse_simple_ddl_rename_table():
    res = parse_simple_ddl("Rename employees table to staff")
    assert res is not None
    assert res.operation == "RENAME_TABLE"
    assert res.sql == "ALTER TABLE employees RENAME TO staff;"

    res = parse_simple_ddl("Rename table employees to staff")
    assert res is not None
    assert res.sql == "ALTER TABLE employees RENAME TO staff;"


def test_parse_simple_ddl_create_table_no_columns():
    res = parse_simple_ddl("Create employees table")
    assert res is not None
    assert res.operation == "CREATE_TABLE"
    assert res.clarification_needed is True
    assert res.table_name == "employees"


def test_parse_simple_ddl_create_table_with_columns():
    # Omitted types (sensible defaults)
    res = parse_simple_ddl("Create table employees with columns id, name, salary")
    assert res is not None
    assert res.operation == "CREATE_TABLE"
    assert "id SERIAL PRIMARY KEY" in res.sql
    assert "name VARCHAR(255)" in res.sql
    assert "salary NUMERIC" in res.sql
    assert res.deterministic is True

    # Explicit types
    res = parse_simple_ddl("Create table employees with columns id INT, name VARCHAR, salary DECIMAL(10,2)")
    assert res is not None
    assert "id INT" in res.sql
    assert "name VARCHAR" in res.sql
    assert "salary DECIMAL(10,2)" in res.sql


def test_parse_simple_ddl_alter_table():
    session = {"selected_table": "employees"}

    # Add Column
    res = parse_simple_ddl("Add salary column", session)
    assert res is not None
    assert res.sql == "ALTER TABLE employees ADD COLUMN salary NUMERIC;"

    # Remove Column
    res = parse_simple_ddl("Remove age column", session)
    assert res is not None
    assert res.sql == "ALTER TABLE employees DROP COLUMN age;"

    # Rename Column
    res = parse_simple_ddl("Rename column salary to monthly_salary", session)
    assert res is not None
    assert res.sql == "ALTER TABLE employees RENAME COLUMN salary TO monthly_salary;"
