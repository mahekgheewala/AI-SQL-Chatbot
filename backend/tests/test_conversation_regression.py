import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import asyncio
from models.schemas import ChatRequest
from routers.chat import chat_endpoint
from state.session_store import ConversationState, get_session

@pytest.mark.anyio
async def test_conversation_regression():
    from unittest.mock import patch, MagicMock
    from db.app_database import Base, engine
    from state.metadata_store import set_databases
    import models.domain  # noqa: F401
    Base.metadata.create_all(bind=engine)
    from utils.logging_config import user_id_var
    user_id_var.set("test-user-id")
    set_databases(["sales_db"])
    session_id = "test-session-123"
    
    mock_user = type("User", (), {"id": "test-user-id"})()
    mock_conn = MagicMock()
    mock_conn.port = 5432
    mock_conn.host = "localhost"
    mock_conn.username = "postgres"
    mock_conn.encrypted_password = "password"
    mock_conn.default_database = "sales_db"
    
    with patch("connections.connection_manager.ConnectionManager.get_connection", return_value=mock_conn), \
         patch("db.executor.execute_sql", return_value={"success": True, "message": "Table employees successfully created", "operation": "CREATE_TABLE"}):
        # 0. Switch database to sales_db to ensure we have a valid DB context
        req = ChatRequest(session_id=session_id, message="use sales_db", history=[])
        await chat_endpoint(req, current_user=mock_user)
        
        from state.session_store import update_session
        update_session(session_id, selected_database="sales_db")
        
        # 1. Start table creation without specifying columns
        req2 = ChatRequest(session_id=session_id, message="Create employees table", history=[])
        res_data = await chat_endpoint(req2, current_user=mock_user)
        assert res_data.intent == "NEEDS_CLARIFICATION"

        # Verify session stores pending_clarification (Stage 4: pending_operation removed)
        session = get_session(session_id)
        pending = session.get("pending_clarification")
        assert pending is not None
        assert pending["type"] == "CREATE_TABLE_COLUMNS"
        assert pending["table_name"] == "employees"

        # Drop the table if it already exists to guarantee clean test execution
        from db.executor import execute_sql
        execute_sql("DROP TABLE IF EXISTS employees;", "sales_db")
        
        # 2. Complete clarification by inputting column definitions with types
        req3 = ChatRequest(session_id=session_id, message="id INTEGER, name VARCHAR(255), salary NUMERIC", history=[])
        res_data = await chat_endpoint(req3, current_user=mock_user)
        assert res_data.intent == "CONFIRMATION"
        assert "successfully" in res_data.reply.lower() or "Execution complete" in res_data.reply

        # Verify pending state is reset
        session = get_session(session_id)
        assert session.get("pending_clarification") is None
