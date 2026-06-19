import sys
import os
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db.executor import (
    execute_sql,
    requires_autocommit,
    requires_superdb,
    _clean_sql
)
from state.metadata_store import get_metadata, clear as clear_meta, set_databases, refresh_routing_summaries

def test_sql_cleaning_and_helpers():
    print("--- Test 1: SQL Cleaning & Helpers ---")
    
    # 1. Block comments
    sql1 = "/* some comment */ DROP DATABASE test_db;"
    print(f"Cleaned 1: '{_clean_sql(sql1)}'")
    assert _clean_sql(sql1) == "DROP DATABASE test_db;"
    assert requires_superdb(sql1) == True
    assert requires_autocommit(sql1) == True

    # 2. Single-line comments
    sql2 = "-- cleanup\nDROP DATABASE test_db;"
    print(f"Cleaned 2: '{_clean_sql(sql2)}'")
    assert _clean_sql(sql2) == "DROP DATABASE test_db;"
    assert requires_superdb(sql2) == True
    assert requires_autocommit(sql2) == True

    # 3. Create index concurrently
    sql3 = "/* index create */ CREATE UNIQUE INDEX CONCURRENTLY idx_users_email ON users(email);"
    print(f"Cleaned 3: '{_clean_sql(sql3)}'")
    assert requires_autocommit(sql3) == True
    assert requires_superdb(sql3) == False

    # 4. Standard commands
    sql4 = "CREATE TABLE students (id int);"
    assert requires_autocommit(sql4) == False
    assert requires_superdb(sql4) == False

    print("Test 1 SUCCESS!\n")

def test_database_creation_and_drop():
    print("--- Test 2: CREATE / DROP DATABASE Execution ---")
    
    # Cleanup any leftovers
    execute_sql("DROP DATABASE IF EXISTS test_non_tx_db;", "hr_database")
    
    # Create database
    print("Creating database test_non_tx_db with comments...")
    sql_create = "-- new db\nCREATE DATABASE test_non_tx_db;"
    res = execute_sql(sql_create, "hr_database")
    print("Create DB Result success:", res.get("success"), "error:", res.get("error"))
    assert res.get("success") == True, f"Failed to create database: {res.get('error')}"
    assert res.get("operation") == "CREATE DATABASE"

    # Drop database
    print("Dropping database test_non_tx_db with comments...")
    sql_drop = "/* delete db */ DROP DATABASE test_non_tx_db;"
    res = execute_sql(sql_drop, "hr_database")
    print("Drop DB Result success:", res.get("success"), "error:", res.get("error"))
    assert res.get("success") == True, f"Failed to drop database: {res.get('error')}"
    assert res.get("operation") == "DROP DATABASE"

    print("Test 2 SUCCESS!\n")

def test_repeated_execution_state():
    print("--- Test 3: Repeated CREATE / DROP DATABASE Execution ---")
    
    for cycle in range(1, 4):
        print(f"Cycle {cycle}: Creating database temp_tx_db...")
        res_create = execute_sql("CREATE DATABASE temp_tx_db;", "hr_database")
        assert res_create.get("success") == True, f"Cycle {cycle} Create failed: {res_create.get('error')}"
        
        print(f"Cycle {cycle}: Dropping database temp_tx_db...")
        res_drop = execute_sql("DROP DATABASE temp_tx_db;", "hr_database")
        assert res_drop.get("success") == True, f"Cycle {cycle} Drop failed: {res_drop.get('error')}"

    print("Test 3 SUCCESS!\n")

def test_concurrent_index_operations():
    print("--- Test 4: Concurrent Index Operations ---")
    
    # Create test table in hr_database
    execute_sql("DROP TABLE IF EXISTS test_idx_table;", "hr_database")
    res_table = execute_sql("CREATE TABLE test_idx_table (id serial primary key, email varchar(255));", "hr_database")
    assert res_table.get("success") == True
    
    # Create index concurrently
    print("Creating index concurrently...")
    res_idx = execute_sql("CREATE INDEX CONCURRENTLY idx_test_email ON test_idx_table(email);", "hr_database")
    print("Create Index result:", res_idx.get("success"), "error:", res_idx.get("error"))
    assert res_idx.get("success") == True, f"Concurrent index creation failed: {res_idx.get('error')}"
    
    # Drop index concurrently
    print("Dropping index concurrently...")
    res_drop_idx = execute_sql("DROP INDEX CONCURRENTLY idx_test_email;", "hr_database")
    print("Drop Index result:", res_drop_idx.get("success"), "error:", res_drop_idx.get("error"))
    assert res_drop_idx.get("success") == True, f"Concurrent index drop failed: {res_drop_idx.get('error')}"
    
    # Clean up test table
    execute_sql("DROP TABLE test_idx_table;", "hr_database")
    
    print("Test 4 SUCCESS!\n")

def run_all():
    print("====================================================")
    print("RUNNING NON-TRANSACTIONAL COMMAND VERIFICATION TESTS")
    print("====================================================\n")
    
    test_sql_cleaning_and_helpers()
    test_database_creation_and_drop()
    test_repeated_execution_state()
    test_concurrent_index_operations()
    
    print("====================================================")
    print("ALL NON-TRANSACTIONAL TESTS PASSED")
    print("====================================================")

if __name__ == "__main__":
    run_all()
