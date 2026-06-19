import sys
import os
import time
import asyncio

# Set python path to backend directory
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# ─── Mock Database Access Layer ──────────────────────────────────────────────
import db.schema_fetcher
import db.table_manager
import db.executor

def mock_fetch_schema(dbname):
    if dbname == "cleanup_test":
        return {"demo": ["id"]}
    elif dbname == "hr_database":
        return {"employees": ["id", "name"]}
    return {}

def mock_fetch_tables(dbname):
    if dbname == "cleanup_test":
        return ["demo"]
    elif dbname == "hr_database":
        return ["employees"]
    return []

def mock_fetch_table_schemas(dbname):
    if dbname == "cleanup_test":
        return {"demo": {"id": "integer"}}
    elif dbname == "hr_database":
        return {"employees": {"id": "integer", "name": "varchar"}}
    return {}

def mock_execute_sql(sql, dbname):
    op = "SELECT"
    sql_upper = sql.strip().upper()
    if sql_upper.startswith("CREATE TABLE"):
        op = "CREATE TABLE"
    elif sql_upper.startswith("DROP TABLE"):
        op = "DROP TABLE"
    elif sql_upper.startswith("SELECT"):
        op = "SELECT"
    return {
        "success": True, 
        "operation": op, 
        "columns": ["id"], 
        "rows": [[1]], 
        "row_count": 1
    }

# Apply mocks before importing anything else
db.schema_fetcher.fetch_schema = mock_fetch_schema
db.schema_fetcher.fetch_table_schemas = mock_fetch_table_schemas
db.table_manager.fetch_tables = mock_fetch_tables
db.executor.execute_sql = mock_execute_sql

# ─── Imports after mocks ─────────────────────────────────────────────────────
from models.schemas import ChatRequest
from routers.chat import chat_endpoint
from state.metadata_store import get_metadata, clear as clear_meta, set_databases, refresh_routing_summaries, set_selected_db
from state.session_store import get_session, update_session

async def run_tests():
    print("====================================================")
    print("RUNNING DATABASE CONTEXT SYNC VERIFICATION TESTS")
    print("====================================================\n")

    # Initialize cache database list and summaries
    clear_meta()
    set_databases(["cleanup_test", "hr_database"])
    refresh_routing_summaries()

    session_id = "test-session-sync-999"

    # Test 1: Verify direct switching and context synchronization
    print("--- Test 1: Direct State context switching (set_selected_db) ---")
    
    print("Switching to cleanup_test...")
    set_selected_db("cleanup_test")
    meta = get_metadata()
    print("  selected_db:", meta.get("selected_db"))
    print("  cached_schema_db:", meta.get("cached_schema_db"))
    print("  schema:", meta.get("schema"))
    assert meta.get("selected_db") == "cleanup_test"
    assert meta.get("cached_schema_db") == "cleanup_test"
    assert "demo" in meta.get("schema")
    
    print("Switching to hr_database...")
    set_selected_db("hr_database")
    meta = get_metadata()
    print("  selected_db:", meta.get("selected_db"))
    print("  cached_schema_db:", meta.get("cached_schema_db"))
    print("  schema:", meta.get("schema"))
    assert meta.get("selected_db") == "hr_database"
    assert meta.get("cached_schema_db") == "hr_database"
    assert "employees" in meta.get("schema")
    print("Test 1 SUCCESS!\n")

    # Test 2: Scenario E - Repeated switching and validation check via endpoint
    print("--- Test 2: Scenario E Verification via Chat Endpoint ---")
    
    # Repeat the switch sequence twice
    for cycle in range(1, 3):
        print(f"\n>> Cycle {cycle}:")
        
        # 1. Switch to cleanup_test
        print("  1. Switch to cleanup_test")
        req = ChatRequest(message="use cleanup_test", session_id=session_id, history=[])
        res = await chat_endpoint(req)
        print("     Reply:", res.reply)
        print("     Active DB in Session:", get_session(session_id).get("selected_database"))
        print("     Schema cached DB:", get_metadata().get("cached_schema_db"))
        assert get_session(session_id).get("selected_database") == "cleanup_test"
        assert get_metadata().get("cached_schema_db") == "cleanup_test"
        
        # 2. Query demo table (validates against cleanup_test)
        print("  2. Run query on table 'demo'")
        req = ChatRequest(message="describe schema of demo", session_id=session_id, history=[])
        res = await chat_endpoint(req)
        print("     Valid:", res.valid)
        print("     Reason if invalid:", res.blocked_reason or res.reply)
        assert res.valid == True, "Failed to validate 'demo' against cleanup_test!"

        # 3. Switch to hr_database
        print("  3. Switch to hr_database")
        req = ChatRequest(message="use hr_database", session_id=session_id, history=[])
        res = await chat_endpoint(req)
        print("     Reply:", res.reply)
        print("     Active DB in Session:", get_session(session_id).get("selected_database"))
        print("     Schema cached DB:", get_metadata().get("cached_schema_db"))
        assert get_session(session_id).get("selected_database") == "hr_database"
        assert get_metadata().get("cached_schema_db") == "hr_database"
        
        # 4. Query employees table (validates against hr_database)
        print("  4. Run query on table 'employees'")
        req = ChatRequest(message="show all employees", session_id=session_id, history=[])
        res = await chat_endpoint(req)
        print("     Valid:", res.valid)
        print("     Reason if invalid:", res.blocked_reason or res.reply)
        assert res.valid == True, "Failed to validate 'employees' against hr_database!"

    print("\nScenario E Verification PASSED successfully!")
    print("No stale cache or schema contamination detected.")
    print("====================================================")
    print("ALL TESTS RUN COMPLETED SUCCESSFULLY")
    print("====================================================")

if __name__ == "__main__":
    asyncio.run(run_tests())
