import sys
import os
import re

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from routers.chat import (
    is_valid_column_definition,
    resolve_pending_clarification,
    split_column_definitions
)

def test_comma_parsing():
    print("Running Comma Parsing Tests...")
    
    # Simple datatype test
    assert is_valid_column_definition("id INTEGER") == True
    assert is_valid_column_definition("name VARCHAR(255)") == True
    
    # Comma inside parenthesis
    assert is_valid_column_definition("salary NUMERIC(10,2)") == True
    assert is_valid_column_definition("price DECIMAL(12,4)") == True
    
    # Multiple columns, some with parenthesis commas
    assert is_valid_column_definition("id INTEGER, name TEXT") == True
    assert is_valid_column_definition("id SERIAL PRIMARY KEY, salary NUMERIC(10,2)") == True
    
    # Mismatched/Invalid input
    assert is_valid_column_definition("price NUMERIC(10") == False
    
    # Split column function validation
    res = split_column_definitions("price NUMERIC(10,2), name TEXT")
    assert res == ["price NUMERIC(10,2)", "name TEXT"]
    
    print("Comma Parsing Tests PASSED.")

def test_missing_datatypes():
    print("Running Missing Datatypes Whitelist Tests...")
    
    assert is_valid_column_definition("col bigint") == True
    assert is_valid_column_definition("col smallint") == True
    assert is_valid_column_definition("col jsonb") == True
    assert is_valid_column_definition("col bytea") == True
    assert is_valid_column_definition("col timestamptz") == True
    assert is_valid_column_definition("col character varying") == True
    assert is_valid_column_definition("col character varying(255)") == True
    assert is_valid_column_definition("col character") == True
    
    # Verify existing datatypes still work
    assert is_valid_column_definition("col int") == True
    assert is_valid_column_definition("col uuid") == True
    assert is_valid_column_definition("col boolean") == True
    
    print("Missing Datatypes Whitelist Tests PASSED.")

def test_option_shadowing():
    print("Running Option Shadowing Matching Tests...")
    
    pending = {
        "type": "MISSING_DATABASE",
        "options": ["mahek", "mahek_test"],
        "original_request": "Describe schema of girl",
        "metadata": {},
        "attempts": 0
    }
    
    # Exact match mahek_test
    res = resolve_pending_clarification("mahek_test", pending)
    assert res["resolved"] == True
    assert res["selected_option"] == "mahek_test"
    
    # Exact match mahek
    res = resolve_pending_clarification("mahek", pending)
    assert res["resolved"] == True
    assert res["selected_option"] == "mahek"
    
    # User message contains mahek_test as word (longest option matches)
    res = resolve_pending_clarification("I want to use mahek_test", pending)
    assert res["resolved"] == True
    assert res["selected_option"] == "mahek_test"
    
    # User message contains mahek as word
    res = resolve_pending_clarification("I want to use mahek", pending)
    assert res["resolved"] == True
    assert res["selected_option"] == "mahek"
    
    # User message contains mahek_test_db (substring matches mahek_test, not mahek)
    res = resolve_pending_clarification("use mahek_test_db", pending)
    assert res["resolved"] == True
    assert res["selected_option"] == "mahek_test"
    
    print("Option Shadowing Matching Tests PASSED.")

def test_confirmation_rejection():
    print("Running Confirmation Rejection Handling Tests...")
    
    pending = {
        "type": "CONFIRMATION",
        "target_db": "hr_database",
        "original_request": "DROP TABLE employees",
        "metadata": {"sql": "DROP TABLE employees;"},
        "attempts": 0
    }
    
    for cancel_input in ["no", "n", "reject", "deny", "stop"]:
        res = resolve_pending_clarification(cancel_input, pending)
        assert res["resolved"] == True
        assert res["selected_option"] == "cancel"
        assert res["reconstructed_request"] is None
        
    print("Confirmation Rejection Handling Tests PASSED.")

def test_universal_datatypes_and_composite_constraints():
    print("Running Universal Datatypes & Composite Constraints Tests...")
    
    # Universal datatypes checklist
    assert is_valid_column_definition("id bigint") == True
    assert is_valid_column_definition("id smallint") == True
    assert is_valid_column_definition("price numeric(10,2)") == True
    assert is_valid_column_definition("price decimal(12,4)") == True
    assert is_valid_column_definition("name varchar(255)") == True
    assert is_valid_column_definition("name character varying(100)") == True
    assert is_valid_column_definition("id uuid") == True
    assert is_valid_column_definition("metadata json") == True
    assert is_valid_column_definition("metadata jsonb") == True
    assert is_valid_column_definition("data bytea") == True
    assert is_valid_column_definition("created_at timestamp") == True
    assert is_valid_column_definition("created_at timestamptz") == True
    assert is_valid_column_definition("is_active boolean") == True
    assert is_valid_column_definition("price double precision") == True
    
    # Composite constraints checklist
    assert is_valid_column_definition("primary key (id)") == True
    assert is_valid_column_definition("primary key (id, name)") == True
    assert is_valid_column_definition("foreign key (user_id) references users(id)") == True
    assert is_valid_column_definition("unique (email)") == True
    assert is_valid_column_definition("check (price > 0)") == True
    assert is_valid_column_definition("constraint pk_products primary key (id)") == True
    
    # Mixed scenario with composite constraints
    mixed_input = "id integer, user_id integer, price numeric(10,2), primary key (id), foreign key (user_id) references users(id)"
    assert is_valid_column_definition(mixed_input) == True
    
    print("Universal Datatypes & Composite Constraints Tests PASSED.")

def test_create_table_resolution_formatting_and_validation():
    print("Running CREATE_TABLE_COLUMNS Resolution Formatting & Validation Tests...")
    
    # Validation Scenario: Happy Path A
    pending_a = {
        "type": "CREATE_TABLE_COLUMNS",
        "table_name": "products",
        "target_db": "test_db",
        "original_request": "create table products",
        "attempts": 0
    }
    res_a = resolve_pending_clarification("id integer, price numeric(10,2)", pending_a)
    assert res_a["resolved"] == True
    expected_sql_a = (
        "CREATE TABLE products (\n"
        "    id integer,\n"
        "    price numeric(10,2)\n"
        ");"
    )
    assert res_a["reconstructed_request"] == expected_sql_a
    
    # Validation Scenario: Happy Path D (Composite constraints)
    pending_d = {
        "type": "CREATE_TABLE_COLUMNS",
        "table_name": "orders",
        "target_db": "test_db",
        "original_request": "create table orders",
        "attempts": 0
    }
    input_d = (
        "id integer,\n"
        "user_id integer,\n"
        "price numeric(10,2),\n"
        "primary key (id),\n"
        "foreign key (user_id) references users(id)"
    )
    res_d = resolve_pending_clarification(input_d, pending_d)
    assert res_d["resolved"] == True
    expected_sql_d = (
        "CREATE TABLE orders (\n"
        "    id integer,\n"
        "    user_id integer,\n"
        "    price numeric(10,2),\n"
        "    primary key (id),\n"
        "    foreign key (user_id) references users(id)\n"
        ");"
    )
    assert res_d["reconstructed_request"] == expected_sql_d
    
    # Validation Scenario: Missing table_name
    pending_missing_tbl = {
        "type": "CREATE_TABLE_COLUMNS",
        "target_db": "test_db",
        "original_request": "create table products",
        "attempts": 0
    }
    res_missing = resolve_pending_clarification("id integer", pending_missing_tbl)
    assert res_missing["resolved"] == False
    
    # Validation Scenario: Invalid table_name (SQL injection pattern / invalid identifier)
    pending_invalid_tbl = {
        "type": "CREATE_TABLE_COLUMNS",
        "table_name": "products; drop table users",
        "target_db": "test_db",
        "original_request": "create table products",
        "attempts": 0
    }
    res_invalid = resolve_pending_clarification("id integer", pending_invalid_tbl)
    assert res_invalid["resolved"] == False
    
    # Validation Scenario: Empty column definition
    res_empty = resolve_pending_clarification("   ", pending_a)
    assert res_empty["resolved"] == False
    
    print("CREATE_TABLE_COLUMNS Resolution Formatting & Validation Tests PASSED.")

if __name__ == "__main__":
    test_comma_parsing()
    test_missing_datatypes()
    test_option_shadowing()
    test_confirmation_rejection()
    test_universal_datatypes_and_composite_constraints()
    test_create_table_resolution_formatting_and_validation()
    print("\nALL CLARIFICATION FRAMEWORK BUG FIXES SUCCESSFULLY VERIFIED!")
