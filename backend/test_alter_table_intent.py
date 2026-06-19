import sys
import os

# Prevent Windows console UnicodeEncodeError for checkmark emoji (✅)
if sys.platform.startswith("win"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agent.tools import _raw_sql_intent
from validation.safety_checker import check_safety
from validation.schema_creator_validator import check_creator_sql

def test_raw_sql_intent_mapping():
    print("--- Test 1: Raw SQL Intent Mapping ---")
    
    # 1. ADD COLUMN
    sql = "ALTER TABLE employees ADD COLUMN email TEXT;"
    assert _raw_sql_intent(sql) == "ADD_COLUMN"
    
    # 2. DROP COLUMN
    sql = "ALTER TABLE employees DROP COLUMN email;"
    assert _raw_sql_intent(sql) == "DROP_COLUMN"
    
    # 3. RENAME COLUMN (explicit)
    sql = "ALTER TABLE employees RENAME COLUMN old TO new;"
    assert _raw_sql_intent(sql) == "RENAME_COLUMN"
    
    # 4. RENAME COLUMN (implicit/optional COLUMN keyword)
    sql = "ALTER TABLE employees RENAME old TO new;"
    assert _raw_sql_intent(sql) == "RENAME_COLUMN"
    
    # 5. RENAME TABLE
    sql = "ALTER TABLE employees RENAME TO employees_new;"
    assert _raw_sql_intent(sql) == "RENAME_TABLE"
    
    # 6. MODIFY COLUMN (TYPE syntax)
    sql = "ALTER TABLE employees ALTER COLUMN email TYPE TEXT;"
    assert _raw_sql_intent(sql) == "MODIFY_COLUMN"
    
    # 7. MODIFY COLUMN (SET DATA TYPE syntax)
    sql = "ALTER TABLE employees ALTER COLUMN email SET DATA TYPE TEXT;"
    assert _raw_sql_intent(sql) == "MODIFY_COLUMN"
    
    # 8. Safe Fallback
    sql = "ALTER TABLE employees;"
    assert _raw_sql_intent(sql) == "ADD_COLUMN"
    
    print("Test 1 SUCCESS!\n")

def test_safety_checker_rules():
    print("--- Test 2: Safety Checker Risk Mapping ---")
    
    # ADD_COLUMN is SAFE
    res = check_safety("ADD_COLUMN", "ALTER TABLE employees ADD COLUMN email TEXT;")
    assert res.get("risk_level") == "SAFE"
    
    # DROP_COLUMN is HIGH_RISK
    res = check_safety("DROP_COLUMN", "ALTER TABLE employees DROP COLUMN email;")
    assert res.get("risk_level") == "HIGH_RISK"
    
    # RENAME_TABLE is HIGH_RISK
    res = check_safety("RENAME_TABLE", "ALTER TABLE employees RENAME TO employees_new;")
    assert res.get("risk_level") == "HIGH_RISK"
    
    print("Test 2 SUCCESS!\n")

def test_schema_creator_validator():
    print("--- Test 3: Schema Creator Injection Check ---")
    
    # Normal RENAME_TABLE passes
    sql = "ALTER TABLE employees RENAME TO employees_new;"
    res = check_creator_sql("RENAME_TABLE", sql)
    assert res.get("valid") == True
    
    # Suspicious line comment is blocked
    sql_injection = "ALTER TABLE employees RENAME TO employees_new; -- comment"
    res = check_creator_sql("RENAME_TABLE", sql_injection)
    assert res.get("valid") == False
    assert "injection" in res.get("reason", "").lower()
    
    print("Test 3 SUCCESS!\n")

def run_all():
    print("====================================================")
    print("RUNNING ALTER TABLE INTENT VERIFICATION TESTS")
    print("====================================================\n")
    
    test_raw_sql_intent_mapping()
    test_safety_checker_rules()
    test_schema_creator_validator()
    
    print("====================================================")
    print("ALL ALTER TABLE INTENT TESTS PASSED")
    print("====================================================")

if __name__ == "__main__":
    run_all()
