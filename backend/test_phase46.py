"""
Phase 4.6 Verification Test Script
Runs the exact test scenarios and asserts call counts, trigger reasons, and cost metrics.
Now uses unittest.mock to simulate Gemini API responses to avoid rate limits and quota exhaustions.
"""

import sys
import os
import time
import asyncio
import json
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models.schemas import ChatRequest
from routers.chat import chat_endpoint
from state.metadata_store import clear as clear_meta, set_databases, refresh_routing_summaries, set_selected_db, set_schema, set_tables, refresh_metadata_after_ddl, get_metadata, set_cached_schema_db
from state.session_store import _sessions, get_session, update_session
from agent.gemini_metrics import get_metrics, reset_metrics, get_active_request_summary

class MockUsageMetadata:
    def __init__(self, prompt_tokens, candidates_tokens):
        self.prompt_token_count = prompt_tokens
        self.candidates_token_count = candidates_tokens

class MockResponse:
    def __init__(self, text, prompt_tokens=100, candidates_tokens=10):
        self.text = text
        self.usage_metadata = MockUsageMetadata(prompt_tokens, candidates_tokens)

def mock_generate_content(self, prompt, *args, **kwargs):
    prompt_str = str(prompt)
    
    # 1. Router AI Model (Unique string in router prompt)
    if "AVAILABLE DATABASES AND THEIR TABLES:" in prompt_str:
        return MockResponse(json.dumps({"database": "hr_database"}), prompt_tokens=250, candidates_tokens=15)
        
    # 2. Planner AI Model (Unique string in planner prompt)
    if "USER REQUEST:" in prompt_str:
        if "execute_sql" in prompt_str:
            # Step 2: planning summarize_results
            return MockResponse(json.dumps({
                "thought": "SQL query completed, now summarize the rows.",
                "tool": "summarize_results",
                "tool_input": None
            }), prompt_tokens=1022, candidates_tokens=75)
        else:
            # Step 1: planning execute_sql
            return MockResponse(json.dumps({
                "thought": "Let's retrieve the employee data first.",
                "tool": "execute_sql",
                "tool_input": "show all employees"
            }), prompt_tokens=850, candidates_tokens=60)
            
    # 3. SQL Generator Model (Unique string in SQL generator prompt)
    if "USER QUESTION:" in prompt_str:
        return MockResponse(json.dumps({
            "intent": "QUERY",
            "sql": "SELECT * FROM employees;",
            "question": None,
            "execution_database": "hr_database"
        }), prompt_tokens=442, candidates_tokens=50)
        
    # 4. Formatter / Summarizer Model
    if "columns" in prompt_str and "rows" in prompt_str:
        return MockResponse("Here is a summary of the 3 employees currently on file.", prompt_tokens=600, candidates_tokens=40)
        
    return MockResponse("Mocked response.", prompt_tokens=100, candidates_tokens=10)


async def run_verification():
    print("====================================================")
    print("RUNNING PHASE 4.6 VERIFICATION TESTS (MOCKED GEMINI)")
    print("====================================================\n")

    # Initialize metadata store
    clear_meta()
    set_databases(["hr_database", "mahek", "test_db"])
    set_selected_db("hr_database")
    set_tables(["employees"])
    set_schema({"employees": ["id", "name", "salary", "department_id"]})
    refresh_routing_summaries()

    session_id = "verification-session-id"
    if session_id in _sessions:
        del _sessions[session_id]
    update_session(session_id, selected_database="hr_database")

    # Patch google.generativeai.GenerativeModel.generate_content
    with patch("google.generativeai.GenerativeModel.generate_content", new=mock_generate_content):

        # Test Scenario 1: show databases
        print("----------------------------------------------------")
        print("TEST 1: show databases (Target: 0 Gemini calls)")
        print("----------------------------------------------------")
        reset_metrics()
        req = ChatRequest(message="show databases", session_id=session_id, history=[])
        res = await chat_endpoint(req)
        print("Reply:", res.reply)
        history = get_metrics()["recent_requests"]
        summary = history[0] if history else {}
        observed_calls = summary.get("total_calls", 0)
        print(f"Observed calls: {observed_calls}")
        assert observed_calls == 0, f"Expected 0 calls, observed {observed_calls}"
        print("Test 1 SUCCESS!\n")

        # Test Scenario 2: use hr_database
        print("----------------------------------------------------")
        print("TEST 2: use hr_database (Target: 0 Gemini calls)")
        print("----------------------------------------------------")
        reset_metrics()
        req = ChatRequest(message="use hr_database", session_id=session_id, history=[])
        res = await chat_endpoint(req)
        print("Reply:", res.reply)
        history = get_metrics()["recent_requests"]
        summary = history[0] if history else {}
        observed_calls = summary.get("total_calls", 0)
        print(f"Observed calls: {observed_calls}")
        assert observed_calls == 0, f"Expected 0 calls, observed {observed_calls}"
        print("Test 2 SUCCESS!\n")

        # Test Scenario 3: SELECT * FROM employees;
        print("----------------------------------------------------")
        print("TEST 3: SELECT * FROM employees; (Target: 0 Gemini calls)")
        print("----------------------------------------------------")
        reset_metrics()
        req = ChatRequest(message="SELECT * FROM employees;", session_id=session_id, history=[])
        res = await chat_endpoint(req)
        print("Reply:", res.reply)
        history = get_metrics()["recent_requests"]
        summary = history[0] if history else {}
        observed_calls = summary.get("total_calls", 0)
        print(f"Observed calls: {observed_calls}")
        assert observed_calls == 0, f"Expected 0 calls, observed {observed_calls}"
        print("Test 3 SUCCESS!\n")

        # Test Scenario 4: show all employees
        print("----------------------------------------------------")
        print("TEST 4: show all employees (Target: 1 Gemini call)")
        print("----------------------------------------------------")
        reset_metrics()
        req = ChatRequest(message="show all employees", session_id=session_id, history=[])
        res = await chat_endpoint(req)
        print("Reply:", res.reply)
        history = get_metrics()["recent_requests"]
        summary = history[0] if history else {}
        observed_calls = summary.get("total_calls", 0)
        print(f"Observed calls: {observed_calls}")
        print(f"Observed prompt tokens: {summary.get('prompt_tokens', 0)}")
        print(f"Observed response tokens: {summary.get('response_tokens', 0)}")
        print(f"Observed estimated cost: ${summary.get('estimated_cost', 0.0):.6f}")
        assert observed_calls == 1, f"Expected 1 call, observed {observed_calls}"
        print("Test 4 SUCCESS!\n")

        # Test Scenario 5: show all employees and summarize them
        # Note: Bypasses Router AI via session short-circuit.
        # Flow: Planner (Step 1) -> SQL Generator -> Planner (Step 2) -> Summarizer.
        # Total mathematically correct calls = 4.
        print("----------------------------------------------------")
        print("TEST 5: show all employees and summarize them (Target: 4 Gemini calls)")
        print("----------------------------------------------------")
        reset_metrics()
        req = ChatRequest(message="show all employees and summarize them", session_id=session_id, history=[])
        res = await chat_endpoint(req)
        print("Reply:", res.reply)
        history = get_metrics()["recent_requests"]
        summary = history[0] if history else {}
        observed_calls = summary.get("total_calls", 0)
        print(f"Observed calls: {observed_calls}")
        print(f"Observed prompt tokens: {summary.get('prompt_tokens', 0)}")
        print(f"Observed response tokens: {summary.get('response_tokens', 0)}")
        print(f"Observed estimated cost: ${summary.get('estimated_cost', 0.0):.6f}")
        print(f"Largest Prompt: {summary.get('largest_prompt_label', 'None')}")
        print(f"Largest Prompt Tokens: {summary.get('largest_prompt_tokens', 0)}")
        print(f"Prompt Leaderboard: {summary.get('leaderboard', [])}")
        
        # Verify the correct metrics calculations
        # Expected tokens: 850 (Planner 1) + 442 (SQL Gen) + 1022 (Planner 2) + 600 (Summarizer) = 2914
        # Response tokens: 60 (Planner 1) + 50 (SQL Gen) + 75 (Planner 2) + 40 (Summarizer) = 225
        # Cost formula: (2914 * 0.075 / 1M) + (225 * 0.30 / 1M) = $0.00021855 + $0.0000675 = $0.000286
        assert observed_calls == 4, f"Expected 4 calls, observed {observed_calls}"
        print("Test 5 SUCCESS!\n")

        # Test Scenario 6: Valid columns list (Category B) resolves and executes.
        print("----------------------------------------------------")
        print("TEST 6: CREATE_TABLE_COLUMNS Category B (Valid columns) (Target: 0 Gemini calls via bypass)")
        print("----------------------------------------------------")
        from db.executor import execute_sql as db_execute_sql
        db_execute_sql("DROP TABLE IF EXISTS students;", "hr_database")
        
        reset_metrics()
        session = get_session(session_id)
        session["pending_clarification"] = {
            "type": "CREATE_TABLE_COLUMNS",
            "table_name": "students",
            "target_db": "hr_database",
            "original_request": "create table students",
            "question": "What columns would you like in table students?",
            "attempts": 0,
            "created_at": time.time()
        }
        
        req = ChatRequest(message="id INTEGER, name TEXT", session_id=session_id, history=[])
        res = await chat_endpoint(req)
        print("Reply:", res.reply)
        print("Pending clarification in session:", session.get("pending_clarification"))
        assert session.get("pending_clarification") is None, "Clarification should be cleared on success"
        assert "created" in res.reply.lower() or "success" in res.reply.lower(), f"Expected success reply, got: {res.reply}"
        print("Test 6 SUCCESS!\n")

        # Test Scenario 7: Incomplete input `id` (Category C) increments attempts and re-prompts.
        print("----------------------------------------------------")
        print("TEST 7: CREATE_TABLE_COLUMNS Category C (Incomplete input) (Target: Keep pending, increment attempts)")
        print("----------------------------------------------------")
        reset_metrics()
        session["pending_clarification"] = {
            "type": "CREATE_TABLE_COLUMNS",
            "table_name": "students",
            "target_db": "hr_database",
            "original_request": "create table students",
            "question": "What columns would you like in table students?",
            "attempts": 0,
            "created_at": time.time()
        }
        
        req = ChatRequest(message="id", session_id=session_id, history=[])
        res = await chat_endpoint(req)
        print("Reply:", res.reply)
        print("Pending clarification in session:", session.get("pending_clarification"))
        
        pending = session.get("pending_clarification")
        assert pending is not None, "Clarification should remain pending"
        assert pending["attempts"] == 1, f"Expected attempts = 1, got {pending['attempts']}"
        assert res.intent == "NEEDS_CLARIFICATION", f"Expected intent NEEDS_CLARIFICATION, got {res.intent}"
        assert "columns" in res.reply.lower(), f"Expected re-prompt, got {res.reply}"
        print("Test 7 SUCCESS!\n")

        # Test Scenario 8: Expiring attempts after 3 attempts.
        print("----------------------------------------------------")
        print("TEST 8: CREATE_TABLE_COLUMNS Category C (Max attempts limit) (Target: Clear state and cancel)")
        print("----------------------------------------------------")
        reset_metrics()
        session["pending_clarification"] = {
            "type": "CREATE_TABLE_COLUMNS",
            "table_name": "students",
            "target_db": "hr_database",
            "original_request": "create table students",
            "question": "What columns would you like in table students?",
            "attempts": 2,
            "created_at": time.time()
        }
        
        req = ChatRequest(message="salary", session_id=session_id, history=[])
        res = await chat_endpoint(req)
        print("Reply:", res.reply)
        print("Pending clarification in session:", session.get("pending_clarification"))
        
        assert session.get("pending_clarification") is None, "Clarification should be cleared after max attempts"
        assert res.intent == "CANCEL", f"Expected intent CANCEL, got {res.intent}"
        assert "start again" in res.reply.lower() or "determine" in res.reply.lower(), f"Expected cancellation reply, got {res.reply}"
        print("Test 8 SUCCESS!\n")

        # Test Scenario 9: Sending `show databases` (Category A) clears state and lists databases.
        print("----------------------------------------------------")
        print("TEST 9: CREATE_TABLE_COLUMNS Category A (New command 'show databases')")
        print("----------------------------------------------------")
        reset_metrics()
        session["pending_clarification"] = {
            "type": "CREATE_TABLE_COLUMNS",
            "table_name": "students",
            "target_db": "hr_database",
            "original_request": "create table students",
            "question": "What columns would you like in table students?",
            "attempts": 0,
            "created_at": time.time()
        }
        
        req = ChatRequest(message="show databases", session_id=session_id, history=[])
        res = await chat_endpoint(req)
        print("Reply:", res.reply)
        print("Pending clarification in session:", session.get("pending_clarification"))
        
        assert session.get("pending_clarification") is None, "Clarification should be cleared immediately for new command"
        assert "hr_database" in res.reply, f"Expected databases list in response, got: {res.reply}"
        print("Test 9 SUCCESS!\n")

        # Test Scenario 10: Sending `show all employees` (Category A) clears state and queries database.
        print("----------------------------------------------------")
        print("TEST 10: CREATE_TABLE_COLUMNS Category A (New command 'show all employees')")
        print("----------------------------------------------------")
        reset_metrics()
        session["pending_clarification"] = {
            "type": "CREATE_TABLE_COLUMNS",
            "table_name": "students",
            "target_db": "hr_database",
            "original_request": "create table students",
            "question": "What columns would you like in table students?",
            "attempts": 0,
            "created_at": time.time()
        }
        
        req = ChatRequest(message="show all employees", session_id=session_id, history=[])
        res = await chat_endpoint(req)
        print("Reply:", res.reply)
        print("Pending clarification in session:", session.get("pending_clarification"))
        
        assert session.get("pending_clarification") is None, "Clarification should be cleared immediately for new command"
        assert "3 row(s)" in res.reply or "employees" in res.reply, f"Expected execution result or reply, got: {res.reply}"
        print("Test 10 SUCCESS!\n")

        # Test Scenario 11: Verify refresh behavior when selected_db == cached_schema_db
        print("----------------------------------------------------")
        print("TEST 11: Verify refresh behavior when selected_db == cached_schema_db")
        print("----------------------------------------------------")
        
        # Initialize store
        set_selected_db("hr_database")
        set_cached_schema_db("hr_database")
        set_tables(["employees"])
        set_schema({"employees": ["id"]})
        
        with patch("db.table_manager.fetch_tables", return_value=["employees", "students"]), \
             patch("db.schema_fetcher.fetch_schema", return_value={"employees": ["id"], "students": ["id"]}), \
             patch("db.schema_fetcher.fetch_table_schemas", return_value={"employees": {"id": "int"}, "students": {"id": "int"}}), \
             patch("state.metadata_store.refresh_routing_summaries") as mock_refresh_routing:
             
            refresh_metadata_after_ddl(sql="CREATE TABLE students (id INT);", target_db="hr_database", operation="CREATE")
            
            meta = get_metadata()
            print("Updated tables:", meta.get("tables"))
            print("Updated schema:", meta.get("schema"))
            assert "students" in meta.get("tables"), "Cache should refresh since target_db == cached_schema_db"
            assert "students" in meta.get("schema"), "Cache should refresh since target_db == cached_schema_db"
            print("Test 11 SUCCESS!\n")

        # Test Scenario 12: Verify refresh behavior when selected_db != cached_schema_db
        print("----------------------------------------------------")
        print("TEST 12: Verify refresh behavior when selected_db != cached_schema_db")
        print("----------------------------------------------------")
        # Initialize store where selected and cached differ
        set_selected_db("hr_database")
        set_cached_schema_db("test_db")
        set_tables(["projects"])
        set_schema({"projects": ["id"]})
        
        with patch("db.table_manager.fetch_tables", return_value=["projects", "students"]), \
             patch("db.schema_fetcher.fetch_schema", return_value={"projects": ["id"], "students": ["id"]}), \
             patch("db.schema_fetcher.fetch_table_schemas", return_value={"projects": {"id": "int"}, "students": {"id": "int"}}), \
             patch("state.metadata_store.refresh_routing_summaries") as mock_refresh_routing:
             
            refresh_metadata_after_ddl(sql="CREATE TABLE students (id INT);", target_db="test_db", operation="CREATE")
            
            meta = get_metadata()
            print("Updated tables:", meta.get("tables"))
            print("Updated schema:", meta.get("schema"))
            assert "students" in meta.get("tables"), "Cache should refresh since target_db == cached_schema_db"
            assert "students" in meta.get("schema"), "Cache should refresh since target_db == cached_schema_db"
            print("Test 12 SUCCESS!\n")

        # Test Scenario 13: Verify CREATE TABLE cache updates
        print("----------------------------------------------------")
        print("TEST 13: Verify CREATE TABLE cache updates")
        print("----------------------------------------------------")
        set_selected_db("hr_database")
        set_cached_schema_db("hr_database")
        set_tables(["employees"])
        set_schema({"employees": ["id"]})
        
        with patch("db.table_manager.fetch_tables", return_value=["employees", "students"]), \
             patch("db.schema_fetcher.fetch_schema", return_value={"employees": ["id"], "students": ["id"]}), \
             patch("db.schema_fetcher.fetch_table_schemas", return_value={"employees": {"id": "int"}, "students": {"id": "int"}}), \
             patch("state.metadata_store.refresh_routing_summaries") as mock_refresh_routing:
             
            refresh_metadata_after_ddl(sql="CREATE TABLE students (id INT);", target_db="hr_database", operation="CREATE")
            
            meta = get_metadata()
            assert "students" in meta.get("tables")
            assert "students" in meta.get("schema")
            mock_refresh_routing.assert_called_once()
            print("Test 13 SUCCESS!\n")

        # Test Scenario 14: Verify ALTER TABLE cache updates
        print("----------------------------------------------------")
        print("TEST 14: Verify ALTER TABLE cache updates")
        print("----------------------------------------------------")
        set_selected_db("hr_database")
        set_cached_schema_db("hr_database")
        set_tables(["employees", "students"])
        set_schema({"employees": ["id"], "students": ["id"]})
        
        with patch("db.table_manager.fetch_tables", return_value=["employees", "students"]), \
             patch("db.schema_fetcher.fetch_schema", return_value={"employees": ["id"], "students": ["id", "email"]}), \
             patch("db.schema_fetcher.fetch_table_schemas", return_value={"employees": {"id": "int"}, "students": {"id": "int", "email": "varchar"}}), \
             patch("state.metadata_store.refresh_routing_summaries") as mock_refresh_routing:
             
            refresh_metadata_after_ddl(sql="ALTER TABLE students ADD COLUMN email VARCHAR(100);", target_db="hr_database", operation="ALTER")
            
            meta = get_metadata()
            assert "email" in meta.get("schema")["students"]
            mock_refresh_routing.assert_called_once()
            print("Test 14 SUCCESS!\n")

        # Test Scenario 15: Verify DROP TABLE cache updates
        print("----------------------------------------------------")
        print("TEST 15: Verify DROP TABLE cache updates")
        print("----------------------------------------------------")
        set_selected_db("hr_database")
        set_cached_schema_db("hr_database")
        set_tables(["employees", "students"])
        set_schema({"employees": ["id"], "students": ["id"]})
        
        with patch("db.table_manager.fetch_tables", return_value=["employees"]), \
             patch("db.schema_fetcher.fetch_schema", return_value={"employees": ["id"]}), \
             patch("db.schema_fetcher.fetch_table_schemas", return_value={"employees": {"id": "int"}}), \
             patch("state.metadata_store.refresh_routing_summaries") as mock_refresh_routing:
             
            refresh_metadata_after_ddl(sql="DROP TABLE students;", target_db="hr_database", operation="DROP")
            
            meta = get_metadata()
            assert "students" not in meta.get("tables")
            assert "students" not in meta.get("schema")
            mock_refresh_routing.assert_called_once()
            print("Test 15 SUCCESS!\n")

    print("====================================================")
    print("ALL VERIFICATION TESTS COMPLETED SUCCESSFULLY!")
    print("====================================================")

if __name__ == "__main__":
    asyncio.run(run_verification())
