"""
Verification script for Phase 8.10/8.11 improvements (async implementation).
"""

import sys
import os
import time
import asyncio

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models.schemas import ChatRequest
from routers.chat import chat_endpoint, resolve_pending_clarification
from state.metadata_store import get_metadata, clear as clear_meta, set_databases, refresh_routing_summaries, set_selected_db
from state.session_store import get_session, update_session

async def run_tests_async():
    print("====================================================")
    print("RUNNING PHASE 8.10/8.11 VERIFICATION TESTS")
    print("====================================================\n")

    # Setup database metadata mock
    clear_meta()
    set_databases(["hr_database", "mahek", "test_db"])
    set_selected_db("hr_database")
    refresh_routing_summaries()

    session_id = "test-session-12345"

    # Test 1: Deterministic DB Switch Bypass
    print("--- Test 1: Deterministic DB Switch Bypass ---")
    req = ChatRequest(message="use database mahek", session_id=session_id, history=[])
    res = await chat_endpoint(req)
    print("Reply:", res.reply)
    print("Database in Response:", res.database)
    
    # Check session updated
    session = get_session(session_id)
    print("Selected DB in Session Memory:", session.get("selected_database"))
    assert session.get("selected_database") == "mahek", "Session selected database not updated to mahek"
    print("Test 1 SUCCESS!\n")

    # Test 2: Column-less CREATE TABLE Clarification
    print("--- Test 2: Column-less CREATE TABLE Clarification ---")
    # Reset selected database to hr_database
    set_selected_db("hr_database")
    update_session(session_id, selected_database="hr_database")

    req = ChatRequest(message="Create table students", session_id=session_id, history=[])
    res = await chat_endpoint(req)
    print("Reply:", res.reply)
    print("Intent:", res.intent)
    
    session = get_session(session_id)
    pending = session.get("pending_clarification")
    print("Pending Clarification in Session:", pending)
    assert pending is not None, "Pending clarification was not stored"
    assert pending.get("type") == "CREATE_TABLE_COLUMNS", "Clarification type is not CREATE_TABLE_COLUMNS"
    assert pending.get("target_db") == "hr_database", "target_db is not hr_database"
    print("Test 2 SUCCESS!\n")

    # Test 3: Resolution ordinal and synonym parsing (CREATE TABLE Columns)
    print("--- Test 3: Resolution attempt count and failed tries ---")
    # Let's verify fails increments attempts
    # Attempt 1: cancel
    req = ChatRequest(message="cancel", session_id=session_id, history=[])
    res = await chat_endpoint(req)
    print("Cancel response reply:", res.reply)
    session = get_session(session_id)
    print("Pending after cancel:", session.get("pending_clarification"))
    assert session.get("pending_clarification") is None, "Clarification was not cleared on cancel"
    print("Cancel SUCCESS!\n")

    # Reset CREATE TABLE clarification for attempts test
    req = ChatRequest(message="Create table students", session_id=session_id, history=[])
    await chat_endpoint(req)
    
    # We will simulate AMBIGUOUS_TABLE_LOCATION to test failures since CREATE_TABLE_COLUMNS resolves any non-cancel string.
    # To test failed attempts, we set pending to AMBIGUOUS_TABLE_LOCATION manually
    session = get_session(session_id)
    session["pending_clarification"] = {
        "type": "AMBIGUOUS_TABLE_LOCATION",
        "target_db": None,
        "options": ["mahek", "test_db"],
        "original_request": "Describe schema of girl",
        "metadata": {"table_name": "girl"},
        "created_at": time.time(),
        "attempts": 0
    }
    
    print("Attempting unresolved input 1...")
    req = ChatRequest(message="banana", session_id=session_id, history=[])
    res = await chat_endpoint(req)
    print("Response reply:", res.reply)
    session = get_session(session_id)
    print("Attempts count in session:", session.get("pending_clarification", {}).get("attempts"))
    assert session.get("pending_clarification", {}).get("attempts") == 1, "Attempts count did not increment to 1"

    print("Attempting unresolved input 2...")
    req = ChatRequest(message="apple", session_id=session_id, history=[])
    res = await chat_endpoint(req)
    session = get_session(session_id)
    print("Attempts count in session:", session.get("pending_clarification", {}).get("attempts"))
    assert session.get("pending_clarification", {}).get("attempts") == 2, "Attempts count did not increment to 2"

    print("Attempting unresolved input 3 (Max Attempts)...")
    req = ChatRequest(message="cherry", session_id=session_id, history=[])
    res = await chat_endpoint(req)
    print("Response reply (should exceed attempts):", res.reply)
    session = get_session(session_id)
    print("Pending after 3rd failure:", session.get("pending_clarification"))
    assert session.get("pending_clarification") is None, "Clarification was not cleared after max attempts"
    print("Attempts failure and reset SUCCESS!\n")

    # Test 4: Ordinal / Synonym Matching
    print("--- Test 4: Ordinal / Synonym Matching ---")
    session = get_session(session_id)
    session["pending_clarification"] = {
        "type": "AMBIGUOUS_TABLE_LOCATION",
        "target_db": None,
        "options": ["mahek", "test_db"],
        "original_request": "Describe schema of girl",
        "metadata": {"table_name": "girl"},
        "created_at": time.time(),
        "attempts": 0
    }
    
    # Match "the first database" -> mahek
    req = ChatRequest(message="the first database", session_id=session_id, history=[])
    res = await chat_endpoint(req)
    print("Reply for 'the first database':", res.reply)
    print("Test 4 completed! Check stdout log for: [Phase 8.11] Clarification resolved. Type: AMBIGUOUS_TABLE_LOCATION Selection: mahek\n")

    # Test 5: Timeout Check
    print("--- Test 5: Timeout Check ---")
    session = get_session(session_id)
    session["pending_clarification"] = {
        "type": "AMBIGUOUS_TABLE_LOCATION",
        "target_db": None,
        "options": ["mahek", "test_db"],
        "original_request": "Describe schema of girl",
        "metadata": {"table_name": "girl"},
        "created_at": time.time() - 1000, # 1000 seconds ago (> 15 mins)
        "attempts": 0
    }
    req = ChatRequest(message="mahek", session_id=session_id, history=[])
    res = await chat_endpoint(req)
    session = get_session(session_id)
    print("Pending after timeout input:", session.get("pending_clarification"))
    assert session.get("pending_clarification") is None, "Clarification was not discarded after timeout"
    print("Timeout check SUCCESS!\n")

    print("====================================================")
    print("ALL TESTS RUN COMPLETED")
    print("====================================================")

if __name__ == "__main__":
    asyncio.run(run_tests_async())
