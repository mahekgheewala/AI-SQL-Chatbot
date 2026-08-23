"""
test_sql_detector.py — Phase 10.4.x Regression Tests
=======================================================
Verifies that is_raw_sql() correctly classifies natural language
instructions vs executable SQL statements.

Run with:
    pytest backend/tests/test_sql_detector.py -v
"""

import sys
import os

# Ensure the backend directory is on the path when running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from agent.sql_detector import is_raw_sql


# ─── Parametrised test cases ─────────────────────────────────────────────────

# Format: (input_message, expected_is_sql, description)
CASES = [
    # ── Natural Language DDL ──────────────────────────────────────────────────
    ("Create a new database called company_analytics.",      False, "NL: create DB with 'called'"),
    ("Create a new database called company_analytics",       False, "NL: create DB without period"),
    ("Create an employees table.",                           False, "NL: create table with article 'an'"),
    ("Create a sales table.",                                False, "NL: create table with article 'a'"),
    ("Add a salary column.",                                 False, "NL: add column with article 'a'"),
    ("Add a salary column to the employees table.",          False, "NL: add column — full sentence"),
    ("Delete the temporary table.",                          False, "NL: delete table with article 'the'"),
    ("Drop the old_logs table please.",                      False, "NL: drop table with polite phrase"),
    ("Rename the employees table to staff.",                 False, "NL: rename table with article"),
    ("Please create a table called orders;",                 False, "NL: polite + semicolon should NOT trigger SQL"),
    ("Can you show me all employees;",                       False, "NL: question word + semicolon"),
    ("Make a new database called analytics.",                False, "NL: 'make' verb synonym"),
    ("Build a table for orders.",                            False, "NL: 'build' verb synonym"),
    ("Remove the age column from employees.",                False, "NL: remove column"),
    ("Modify the salary column to be numeric.",              False, "NL: modify column"),
    ("Set up a new database named warehouse.",               False, "NL: 'set up' verb phrase + 'named'"),

    # ── Natural Language Queries ──────────────────────────────────────────────
    ("Show all employees",                                   False, "NL: show query"),
    ("Show me the top 5 employees by salary",               False, "NL: show me phrase"),
    ("How many employees are there?",                        False, "NL: question word"),
    ("Generate a report of monthly sales",                   False, "NL: generate report"),
    ("Plot employee salaries",                               False, "NL: visualization"),
    ("Count all orders from last month",                     False, "NL: count query"),

    # ── Raw SQL DDL ───────────────────────────────────────────────────────────
    ("CREATE DATABASE company_analytics;",                   True,  "SQL: CREATE DATABASE with semicolon"),
    ("CREATE DATABASE company_analytics",                    True,  "SQL: CREATE DATABASE without semicolon"),
    ("CREATE TABLE employees (id SERIAL PRIMARY KEY);",      True,  "SQL: CREATE TABLE with body"),
    ("CREATE TABLE employees (id SERIAL, name TEXT);",       True,  "SQL: CREATE TABLE multi-col"),
    ("CREATE INDEX idx_emp ON employees(name);",             True,  "SQL: CREATE INDEX"),
    ("ALTER TABLE employees ADD COLUMN salary INT;",         True,  "SQL: ALTER TABLE ADD COLUMN"),
    ("ALTER TABLE employees DROP COLUMN age;",               True,  "SQL: ALTER TABLE DROP COLUMN"),
    ("DROP TABLE temp_data;",                                True,  "SQL: DROP TABLE"),
    ("DROP TABLE IF EXISTS temp_data;",                      True,  "SQL: DROP TABLE IF EXISTS"),
    ("DROP DATABASE old_db;",                                True,  "SQL: DROP DATABASE"),

    # ── Raw SQL DML / Query ───────────────────────────────────────────────────
    ("SELECT * FROM employees;",                             True,  "SQL: SELECT *"),
    ("SELECT id, name FROM employees WHERE salary > 50000",  True,  "SQL: SELECT with WHERE"),
    ("INSERT INTO employees VALUES (1, 'Alice', 70000);",    True,  "SQL: INSERT INTO"),
    ("UPDATE employees SET salary = 80000 WHERE id = 1;",   True,  "SQL: UPDATE SET"),
    ("DELETE FROM employees WHERE id = 1;",                  True,  "SQL: DELETE FROM"),
    ("TRUNCATE TABLE employees;",                            True,  "SQL: TRUNCATE TABLE"),

    # ── Edge cases ────────────────────────────────────────────────────────────
    ("",                                                     False, "Edge: empty string"),
    ("SELECT",                                               False, "Edge: bare keyword, no object"),
    ("CREATE",                                               False, "Edge: bare keyword, no object"),
    ("select * from employees",                              True,  "SQL: lowercase SELECT"),
    (
        "CREATE TABLE orders (\n    id SERIAL PRIMARY KEY,\n    total NUMERIC\n);",
        True,
        "SQL: multi-line CREATE TABLE"
    ),
]


@pytest.mark.parametrize("message, expected, description", CASES)
def test_is_raw_sql(message: str, expected: bool, description: str) -> None:
    result, reason = is_raw_sql(message)
    assert result == expected, (
        f"[{description}]\n"
        f"  Input    : {repr(message)}\n"
        f"  Expected : {'Raw SQL' if expected else 'Natural Language'}\n"
        f"  Got      : {'Raw SQL' if result else 'Natural Language'}\n"
        f"  Reason   : {reason}"
    )
