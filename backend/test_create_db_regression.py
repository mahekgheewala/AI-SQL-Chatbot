import sys
import os
import re

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from state.metadata_store import (
    get_metadata,
    clear as clear_meta,
    set_databases,
    refresh_routing_summaries
)
from state.session_store import (
    get_session,
    update_session
)
from agent.tools import execute_sql
from db.executor import execute_sql as db_execute_sql

def test_create_database_regression_fixes():
    print("Running CREATE DATABASE Regression Tests...")

    session_id = "test-reg-session-456"
    session = get_session(session_id)

    # Reset metadata and session state
    clear_meta()
    session["selected_database"] = None
    session["selected_table"] = None
    session["recent_databases"] = []
    session["recent_tables"] = []

    # Clean up any leftover databases from previous runs
    print("Cleaning up test databases if they exist...")
    db_execute_sql("DROP DATABASE IF EXISTS session_test;", "postgres")
    db_execute_sql("DROP DATABASE IF EXISTS session_test2;", "postgres")

    # Verify that target_db is None initially
    target_db = session.get("selected_database")
    assert target_db is None

    # ── Test Scenario 1: CREATE and DROP with no selected DB ──
    print("\n--- Test Scenario 1: CREATE and DROP with no selected DB ---")
    
    # 1. CREATE DATABASE
    print("Executing: CREATE DATABASE session_test;")
    result_create = execute_sql(
        instruction="CREATE DATABASE session_test;",
        target_db=None,
        session=session,
        session_id=session_id,
        history=[]
    )
    
    print("CREATE Result:")
    print("  Success:", result_create.get("execution", {}).get("success"))
    print("  Failure Reason:", result_create.get("failure_reason"))
    print("  SQL:", result_create.get("sql"))
    
    assert result_create.get("valid") is True, "Validation failed!"
    assert result_create.get("failure_reason") is None, f"Expected no failure reason, got: {result_create.get('failure_reason')}"
    assert result_create.get("execution", {}).get("success") is True, "Database creation failed!"

    # 2. SHOW DATABASES (we can execute SELECT datname FROM pg_database; to simulate SHOW DATABASES / check databases list)
    # Refresh routing summaries so it shows up in metadata databases list
    from db.schema_fetcher import fetch_all_databases
    set_databases(fetch_all_databases())
    
    meta = get_metadata()
    assert "session_test" in meta.get("databases", []), "session_test not found in metadata database list!"

    # 3. DROP DATABASE
    # (Note: DROP DATABASE requires user confirmation because it is CRITICAL_RISK)
    print("\nExecuting: DROP DATABASE session_test;")
    result_drop = execute_sql(
        instruction="DROP DATABASE session_test;",
        target_db=None,
        session=session,
        session_id=session_id,
        history=[]
    )
    
    print("DROP Result:")
    print("  Valid:", result_drop.get("valid"))
    print("  Requires Confirmation:", result_drop.get("requires_confirmation"))
    print("  Failure Reason:", result_drop.get("failure_reason"))
    
    assert result_drop.get("valid") is True, "Validation failed!"
    assert result_drop.get("requires_confirmation") is True, "DROP DATABASE should require confirmation!"
    assert result_drop.get("failure_reason") is None, f"Expected no failure reason, got: {result_drop.get('failure_reason')}"

    # Manually execute the drop to clean up the DB
    db_res = db_execute_sql("DROP DATABASE session_test;", "postgres")
    assert db_res.get("success") is True, "Manual DROP DATABASE failed!"

    # Refresh metadata to verify it is gone
    set_databases(fetch_all_databases())
    meta = get_metadata()
    assert "session_test" not in meta.get("databases", []), "session_test still present in database list!"
    print("Test Scenario 1 SUCCESS!")


    # ── Test Scenario 2: Active database selected ──
    print("\n--- Test Scenario 2: Active database selected ---")
    
    # 1. CREATE DATABASE
    print("Executing: CREATE DATABASE session_test2;")
    result_create2 = execute_sql(
        instruction="CREATE DATABASE session_test2;",
        target_db=None,
        session=session,
        session_id=session_id,
        history=[]
    )
    assert result_create2.get("execution", {}).get("success") is True, "CREATE DATABASE session_test2 failed!"

    # 2. Set active database to hr_database
    session["selected_database"] = "hr_database"
    print("Active database in session set to: hr_database")

    # 3. DROP DATABASE session_test2
    print("Executing: DROP DATABASE session_test2;")
    result_drop2 = execute_sql(
        instruction="DROP DATABASE session_test2;",
        target_db="hr_database",
        session=session,
        session_id=session_id,
        history=[]
    )
    
    print("DROP Result 2:")
    print("  Valid:", result_drop2.get("valid"))
    print("  Requires Confirmation:", result_drop2.get("requires_confirmation"))
    print("  Failure Reason:", result_drop2.get("failure_reason"))
    
    assert result_drop2.get("valid") is True, "Validation failed!"
    assert result_drop2.get("requires_confirmation") is True, "DROP DATABASE should require confirmation!"
    assert result_drop2.get("failure_reason") is None, f"Expected no failure reason, got: {result_drop2.get('failure_reason')}"

    # Manually execute the drop to clean up the DB
    db_res2 = db_execute_sql("DROP DATABASE session_test2;", "postgres")
    assert db_res2.get("success") is True, "Manual DROP DATABASE 2 failed!"
    print("Test Scenario 2 SUCCESS!")

    print("\nAll CREATE/DROP DATABASE regression tests PASSED.")

if __name__ == "__main__":
    test_create_database_regression_fixes()
