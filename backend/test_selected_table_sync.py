import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from state.session_store import get_session, update_session, clear_session_table, rename_session_table
from routers.chat import _update_session_from_execution

def test_selected_table_sync_fixes():
    print("Running selected_table Session Sync Tests...")
    
    session_id = "test-sync-session-999"
    session = get_session(session_id)
    
    # ── Test Scenario 1: DROP TABLE Invalidation ──
    print("\n--- Test Scenario 1: DROP TABLE Invalidation ---")
    # 1. Simulate table selection/creation
    update_session(session_id, selected_table="students")
    print("Initial session state:")
    print("  selected_table:", session.get("selected_table"))
    print("  recent_tables:", session.get("recent_tables"))
    
    assert session.get("selected_table") == "students"
    assert "students" in session.get("recent_tables", [])
    
    # 2. Simulate dropping the table "students"
    execution_result = {"success": True, "operation": "DROP"}
    _update_session_from_execution(session_id, "DROP", "DROP TABLE students;", execution_result)
    
    # 3. Verify selected_table is cleared and removed from history
    print("Post-drop session state:")
    print("  selected_table:", session.get("selected_table"))
    print("  recent_tables:", session.get("recent_tables"))
    
    assert session.get("selected_table") is None, "selected_table was not cleared!"
    assert "students" not in session.get("recent_tables", []), "Table was not removed from recent_tables!"
    print("Test Scenario 1 SUCCESS!")
    
    # ── Test Scenario 2: ALTER TABLE RENAME ──
    print("\n--- Test Scenario 2: ALTER TABLE RENAME ---")
    # 1. Simulate table selection/creation
    update_session(session_id, selected_table="students")
    print("Initial session state:")
    print("  selected_table:", session.get("selected_table"))
    print("  recent_tables:", session.get("recent_tables"))
    
    assert session.get("selected_table") == "students"
    assert "students" in session.get("recent_tables", [])
    
    # 2. Simulate renaming "students" to "pupils"
    execution_result = {"success": True, "operation": "ALTER"}
    _update_session_from_execution(session_id, "ALTER", "ALTER TABLE students RENAME TO pupils;", execution_result)
    
    # 3. Verify selected_table is updated to "pupils" and "students" is replaced in history
    print("Post-rename session state:")
    print("  selected_table:", session.get("selected_table"))
    print("  recent_tables:", session.get("recent_tables"))
    
    assert session.get("selected_table") == "pupils", "selected_table was not renamed to 'pupils'!"
    assert "pupils" in session.get("recent_tables", []), "'pupils' not found in recent_tables!"
    assert "students" not in session.get("recent_tables", []), "old name 'students' still present in recent_tables!"
    print("Test Scenario 2 SUCCESS!")
    
    print("\nAll selected_table sync checks PASSED.")

if __name__ == "__main__":
    test_selected_table_sync_fixes()
