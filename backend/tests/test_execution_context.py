"""
test_execution_context.py — Phase 10.4.x Regression Tests
===========================================================
Verifies that build_execution_context() constructs the correct
ExecutionContext metadata for each tool/intent.

Run with:
    pytest backend/tests/test_execution_context.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from agent.tools import build_execution_context, ExecutionContext


CASES = [
    # (tool_name, instruction, expected_op, expected_scope, req_db, req_super, req_confirm)
    (
        "create_database", 
        "Create a database called company_analytics", 
        "CREATE_DATABASE", 
        "SERVER", 
        False, 
        True, 
        False
    ),
    (
        "create_database", 
        "CREATE DATABASE test_db;", 
        "CREATE_DATABASE", 
        "SERVER", 
        False, 
        True, 
        False
    ),
    (
        "execute_sql", 
        "CREATE DATABASE raw_db;", 
        "CREATE_DATABASE", 
        "SERVER", 
        False, 
        True, 
        False
    ),
    (
        "execute_sql", 
        "DROP DATABASE raw_db;", 
        "DROP_DATABASE", 
        "SERVER", 
        False, 
        True, 
        True
    ),
    (
        "create_table", 
        "Create table employees", 
        "CREATE_TABLE", 
        "DATABASE", 
        True, 
        False, 
        False
    ),
    (
        "execute_sql", 
        "CREATE TABLE test_table (id SERIAL PRIMARY KEY);", 
        "CREATE_TABLE", 
        "DATABASE", 
        True, 
        False, 
        False
    ),
    (
        "execute_sql", 
        "SELECT * FROM employees;", 
        "QUERY", 
        "DATABASE", 
        True, 
        False, 
        False
    ),
    (
        "execute_sql", 
        "DROP TABLE employees;", 
        "DROP_TABLE", 
        "DATABASE", 
        True, 
        False, 
        True
    ),
    (
        "execute_sql", 
        "Delete the database company_archive", 
        "DROP_DATABASE", 
        "SERVER", 
        False, 
        True, 
        True
    ),
    (
        "execute_sql", 
        "make a new database named test_db", 
        "CREATE_DATABASE", 
        "SERVER", 
        False, 
        True, 
        False
    ),
    (
        "list_databases", 
        "", 
        "LIST_DATABASES", 
        "SERVER", 
        False, 
        False, 
        False
    ),
    (
        "list_tables", 
        "", 
        "LIST_TABLES", 
        "DATABASE", 
        True, 
        False, 
        False
    ),
    (
        "get_schema_info", 
        "describe employees", 
        "GET_SCHEMA", 
        "DATABASE", 
        True, 
        False, 
        False
    ),
]


@pytest.mark.parametrize(
    "tool_name, instruction, expected_op, expected_scope, req_db, req_super, req_confirm",
    CASES
)
def test_build_execution_context(
    tool_name: str, 
    instruction: str, 
    expected_op: str, 
    expected_scope: str, 
    req_db: bool, 
    req_super: bool, 
    req_confirm: bool
) -> None:
    context = build_execution_context(tool_name, instruction)
    assert context.operation_type == expected_op
    assert context.resource_scope == expected_scope
    assert context.requires_database_context == req_db
    assert context.requires_superdb == req_super
    assert context.requires_confirmation == req_confirm
