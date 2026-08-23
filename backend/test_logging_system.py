import sys
import os
import json
import logging
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.logging_config import (
    setup_logging,
    request_id_var,
    session_id_var,
    user_id_var,
    database_name_var,
    selected_table_var,
    connection_id_var,
    metadata_version_var,
    logger_audit,
    logger_query,
    logger_http,
    SafeJSONFormatter
)

def read_last_log_line(filepath):
    """Utility to read the very last line of a log file and parse as JSON."""
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
        if not lines:
            return None
        # Find the last non-empty line
        for line in reversed(lines):
            if line.strip():
                return json.loads(line.strip())
    return None

def test_logging_system():
    print("==================================================")
    print("RUNNING LOGGING SYSTEM TEST SUITE")
    print("==================================================")

    # --- Scenario 1: Setup validation ---
    print("\nScenario 1: Setup validation...")
    setup_logging()
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(base_dir, "logs")
    
    assert os.path.exists(logs_dir), "Logs directory was not created!"
    print("  Logs directory exists:", logs_dir)
    
    app_log_path = os.path.join(logs_dir, "app.log")
    error_log_path = os.path.join(logs_dir, "error.log")
    audit_log_path = os.path.join(logs_dir, "audit.log")
    
    # Send quick logs to ensure files are initialized
    logging.info("Initializing app.log")
    logging.error("Initializing error.log")
    logger_audit.info("Initializing audit.log")
    
    # Flush handlers to ensure immediate disk write
    for logger in [logging.getLogger(), logger_audit]:
        for handler in logger.handlers:
            handler.flush()
            
    assert os.path.exists(app_log_path), "app.log was not created!"
    assert os.path.exists(error_log_path), "error.log was not created!"
    assert os.path.exists(audit_log_path), "audit.log was not created!"
    print("  All log files created successfully.")
    print("Scenario 1 SUCCESS!")

    # --- Scenario 2: Test JSON Schema & ContextVars propagation ---
    print("\nScenario 2: ContextVars propagation and JSON schema validation...")
    
    # Bind context values
    req_token = request_id_var.set("test-request-999")
    sess_token = session_id_var.set("test-session-888")
    user_token = user_id_var.set("test-user-777")
    db_token = database_name_var.set("test-db")
    tbl_token = selected_table_var.set("test-table")
    conn_token = connection_id_var.set("test-conn-111")
    mv_token = metadata_version_var.set(105)
    
    test_msg = "Logging system verification query test message"
    logger_query.info(
        test_msg,
        extra={
            "operation_type": "SELECT",
            "execution_time_ms": 12.34,
            "custom_metadata": {"key": "val"}
        }
    )
    
    # Flush handlers
    for handler in logging.getLogger().handlers:
        handler.flush()
        
    log_entry = read_last_log_line(app_log_path)
    assert log_entry is not None, "Failed to read the logged line from app.log!"
    
    print("  Verifying standard fields:")
    assert log_entry["request_id"] == "test-request-999"
    assert log_entry["session_id"] == "test-session-888"
    assert log_entry["user_id"] == "test-user-777"
    assert log_entry["database_name"] == "test-db"
    assert log_entry["selected_table"] == "test-table"
    assert log_entry["connection_id"] == "test-conn-111"
    assert log_entry["metadata_version"] == 105
    print("    Standard ContextVars successfully propagated.")
    
    print("  Verifying custom extra fields:")
    assert log_entry["operation_type"] == "SELECT"
    assert log_entry["execution_time_ms"] == 12.34
    assert log_entry["custom_metadata"] == {"key": "val"}
    print("    Custom fields and dictionaries successfully serialized.")
    
    # Clean up ContextVars for next scenarios
    request_id_var.reset(req_token)
    session_id_var.reset(sess_token)
    user_id_var.reset(user_token)
    database_name_var.reset(db_token)
    selected_table_var.reset(tbl_token)
    connection_id_var.reset(conn_token)
    metadata_version_var.reset(mv_token)
    
    print("Scenario 2 SUCCESS!")

    # --- Scenario 3: Test Safe JSON Formatter fallback (serialization fail) ---
    print("\nScenario 3: Safe JSON Formatter serialization fallback...")
    
    class NonSerializableClass:
        def __init__(self, val):
            self.val = val
        def __repr__(self):
            return f"NonSerializable<{self.val}>"
            
    non_serializable_obj = NonSerializableClass("unserializable_data")
    
    # This logging statement should execute without throwing TypeError/ValueError
    logger_query.info(
        "Testing fallback on unserializable payload",
        extra={
            "broken_field": non_serializable_obj,
            "normal_field": "works"
        }
    )
    
    # Flush handlers
    for handler in logging.getLogger().handlers:
        handler.flush()
        
    log_entry = read_last_log_line(app_log_path)
    assert log_entry is not None, "Failed to read fallback log entry!"
    
    assert log_entry["normal_field"] == "works", "Normal fields were dropped during serialisation issue!"
    assert "NonSerializable<unserializable_data>" in log_entry["broken_field"], "Broken fields were not fallbacked to string!"
    print("  Safe JSON Formatter successfully serialized non-serializable object as string representation.")
    print("Scenario 3 SUCCESS!")

    # --- Scenario 4: Test Silent Failure handling ---
    print("\nScenario 4: Silent Failure behavior...")
    
    # 1. Test invalid arguments/None logging
    try:
        logging.info(None)
        logging.error(None)
        logger_audit.info(None)
        print("  Logging None as message worked silently.")
    except Exception as e:
        assert False, f"Logging None raised an exception: {e}"
        
    # 2. Test formatter exception recovery
    # We will instantiate SafeJSONFormatter directly and pass a completely broken record to format()
    formatter = SafeJSONFormatter()
    broken_record = logging.LogRecord("name", logging.INFO, "pathname", 1, "msg", (), None)
    # Set created to None to trigger an exception during utcfromtimestamp() inside format()
    broken_record.created = None
        
    formatted_str = formatter.format(broken_record)
    assert formatted_str is not None, "Formatter format() returned None!"
    parsed_fallback = json.loads(formatted_str)
    assert parsed_fallback["logger"] == "app.logging_fallback", "Formatter fallback log level was not recorded correctly!"
    print("  Formatter successfully recovered from extreme formatting error and returned valid fallback JSON.")
    
    print("Scenario 4 SUCCESS!")
    print("\n==================================================")
    print("ALL LOGGING SYSTEM TESTS PASSED")
    print("==================================================")

if __name__ == "__main__":
    test_logging_system()
