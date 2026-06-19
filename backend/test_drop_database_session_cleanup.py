import sys
import os
import re

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from state.metadata_store import (
    get_metadata,
    clear as clear_meta,
    set_routing_summaries
)
from state.session_store import (
    get_session,
    update_session,
    clear_session_database
)

def test_drop_database_session_cleanup():
    print("Running DROP DATABASE Session Cleanup Tests...")

    session_id = "test-drop-db-session-123"

    # 1. Initialize metadata store with routing summaries
    clear_meta()
    set_routing_summaries({
        "tx_test": ["demo", "other_demo"],
        "unrelated_db": ["unrelated_tbl"]
    })

    # 2. Initialize session store
    session = get_session(session_id)
    session["selected_database"] = "tx_test"
    session["selected_table"] = "demo"
    session["recent_databases"] = ["tx_test", "unrelated_db"]
    session["recent_tables"] = ["demo", "other_demo", "unrelated_tbl"]

    print("Initial session state:")
    print("  selected_database:", session.get("selected_database"))
    print("  selected_table:", session.get("selected_table"))
    print("  recent_databases:", session.get("recent_databases"))
    print("  recent_tables:", session.get("recent_tables"))

    assert session.get("selected_database") == "tx_test"
    assert session.get("selected_table") == "demo"
    assert "tx_test" in session.get("recent_databases", [])
    assert "demo" in session.get("recent_tables", [])
    assert "other_demo" in session.get("recent_tables", [])
    assert "unrelated_tbl" in session.get("recent_tables", [])

    # 3. Simulate dropping the database 'tx_test'
    # Step A: Capture the database name from comment-resilient SQL and its tables from routing summaries
    sql = "-- Dropping database after test run\nDROP DATABASE tx_test;"
    from db.executor import _clean_sql
    cleaned_sql = _clean_sql(sql)
    
    op = "DROP DATABASE"
    is_drop_db = (
        op == "DROP DATABASE"
        or (op == "DROP" and "DATABASE" in cleaned_sql.upper())
    )
    
    assert is_drop_db, "Should identify DROP DATABASE operation correctly even with comments"
    
    match = re.search(r"DROP\s+DATABASE\s+(?:IF\s+EXISTS\s+)?([a-zA-Z0-9_\"'`]+)", cleaned_sql, re.IGNORECASE)
    assert match is not None, "Should extract database name correctly from comment-stripped SQL"
    
    dropped_db = match.group(1).strip('`"\'')
    assert dropped_db == "tx_test", f"Extracted database name should be 'tx_test', got: {dropped_db}"

    # Step B: Get tables of dropped database from routing summaries
    meta = get_metadata()
    routing_summaries = meta.get("routing_summaries", {})
    db_tables = []
    for d_name, t_names in routing_summaries.items():
        if d_name.lower() == dropped_db.lower():
            db_tables = t_names
            break

    assert db_tables == ["demo", "other_demo"], f"Should retrieve tables for tx_test, got: {db_tables}"

    # Step C: Call clear_session_database
    clear_session_database(session_id, dropped_db, db_tables)

    # 4. Verify that session has been cleaned up correctly
    print("\nPost-drop session state:")
    print("  selected_database:", session.get("selected_database"))
    print("  selected_table:", session.get("selected_table"))
    print("  recent_databases:", session.get("recent_databases"))
    print("  recent_tables:", session.get("recent_tables"))

    assert session.get("selected_database") is None, "selected_database was not cleared!"
    assert session.get("selected_table") is None, "selected_table was not cleared!"
    
    # Verify dropped database is removed from recent_databases
    assert "tx_test" not in session.get("recent_databases", []), "tx_test was not removed from recent_databases!"
    assert "unrelated_db" in session.get("recent_databases", []), "unrelated_db was incorrectly removed!"
    
    # Verify tables of dropped database are removed from recent_tables
    assert "demo" not in session.get("recent_tables", []), "demo table was not removed!"
    assert "other_demo" not in session.get("recent_tables", []), "other_demo table was not removed!"
    assert "unrelated_tbl" in session.get("recent_tables", []), "unrelated_tbl was incorrectly removed!"

    print("\nDROP DATABASE Session Cleanup Tests PASSED.")

if __name__ == "__main__":
    test_drop_database_session_cleanup()
