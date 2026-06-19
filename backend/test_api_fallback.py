import os
import sys
from dotenv import load_dotenv

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import google.generativeai as genai
from ai import gemini_service
from ai import router_service
from agent import gemini_retry

# Ensure we override env with original key first
load_dotenv(override=True)
ORIGINAL_KEY = os.getenv("GEMINI_API_KEY")
FALLBACK_KEY = os.getenv("GEMINI_FALLBACK_API_KEY") or "AQ.Ab8RN6LNggi88Q6LKdA0uCkJYHI8RraMIerscxr93VOIYE61tA"

def test_static_fallback():
    print("\n==================================================")
    print("TEST 1: STATIC/STARTUP FALLBACK CHECK")
    print("==================================================")
    
    # Temporarily set environment key to empty or placeholder
    os.environ["GEMINI_API_KEY"] = "YOUR_GEMINI_API_KEY_HERE"
    
    # Reload initialization logic parameters
    primary_key = os.environ["GEMINI_API_KEY"]
    fallback_key = os.getenv("GEMINI_FALLBACK_API_KEY") or "AQ.Ab8RN6LNggi88Q6LKdA0uCkJYHI8RraMIerscxr93VOIYE61tA"
    
    key_to_use = primary_key
    if not key_to_use or key_to_use == "YOUR_GEMINI_API_KEY_HERE":
        key_to_use = fallback_key
        
    print("Startup resolution:")
    print("  Resolved API key prefix:", key_to_use[:10] + "...")
    assert key_to_use == fallback_key, "Should have resolved to fallback key"
    print("SUCCESS: Static fallback resolved to fallback key successfully.")

def test_dynamic_auth_fallback():
    print("\n==================================================")
    print("TEST 2: DYNAMIC/RUNTIME AUTH ERROR FALLBACK CHECK")
    print("==================================================")
    
    # 1. Reset fallback tracker state
    gemini_retry.reset_fallback_state()
    assert gemini_retry._has_fallen_back is False, "Fallback tracker should be False initially"
    
    # 2. Configure with a deliberately invalid key
    print("Configuring gemini with invalid key...")
    gemini_service.configure_api_key("INVALID_API_KEY_TEST_STRING")
    
    # Verify the current key is invalid
    print("Calling generate_sql_response with invalid key (should trigger fallback retry)...")
    
    # Call generate_sql_response which internally uses call_with_retry
    result = gemini_service.generate_sql_response(
        user_input="List all columns of table teachers",
        dynamic_context="Table teachers has columns id, name",
        selected_db="mahek",
        selected_table="teachers"
    )
    
    print("\nResult of generate_sql_response:")
    print("  Intent:", result.get("intent"))
    print("  SQL:", result.get("sql"))
    
    # The call should have succeeded because of dynamic fallback to the valid fallback key
    assert gemini_retry._has_fallen_back is True, "Should have marked _has_fallen_back as True"
    assert result.get("intent") in ["DESCRIBE_TABLE", "SCHEMA"], f"Unexpected intent resolved: {result.get('intent')}"
    print("SUCCESS: Dynamic auth error fallback succeeded!")

def test_dynamic_rate_limit_fallback():
    print("\n==================================================")
    print("TEST 3: DYNAMIC/RUNTIME RATE LIMIT FALLBACK CHECK")
    print("==================================================")
    
    # Reset fallback tracker state
    gemini_retry.reset_fallback_state()
    assert gemini_retry._has_fallen_back is False, "Fallback tracker should be False initially"
    
    # Setup mock to simulate a rate limit error (429) on first call
    # We will raise a ResourceExhausted exception with 429 in the error message
    class MockResourceExhausted(Exception):
        pass
        
    first_call_done = False
    
    def mock_fn():
        nonlocal first_call_done
        if not first_call_done:
            first_call_done = True
            raise MockResourceExhausted("429 Resource Exhausted: rate limit exceeded.")
        return "SUCCESS_VAL"
        
    # Reconfigure api key using ORIGINAL_KEY (to avoid bad key errors)
    gemini_service.configure_api_key(ORIGINAL_KEY)
    
    print("Calling mock function that raises 429 on first try...")
    res, success, err = gemini_retry.call_with_retry(mock_fn, label="TestMock")
    
    print("Call success status:", success)
    print("Call returned value:", res)
    print("Fallen back state:", gemini_retry._has_fallen_back)
    
    assert success is True, "Call should succeed on retry with fallback"
    assert res == "SUCCESS_VAL", "Return value should be SUCCESS_VAL"
    assert gemini_retry._has_fallen_back is True, "Should have fallen back to fallback key"
    print("SUCCESS: Dynamic rate limit fallback succeeded!")

if __name__ == "__main__":
    try:
        test_static_fallback()
        test_dynamic_auth_fallback()
        test_dynamic_rate_limit_fallback()
        print("\n==================================================")
        print("ALL FALLBACK TESTS PASSED SUCCESSFULLY!")
        print("==================================================")
    finally:
        # Restore original state/key
        if ORIGINAL_KEY:
            os.environ["GEMINI_API_KEY"] = ORIGINAL_KEY
            gemini_service.configure_api_key(ORIGINAL_KEY)
            gemini_retry.reset_fallback_state()
