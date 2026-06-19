import os
import json

from dotenv import load_dotenv

import google.generativeai as genai
from google.api_core.exceptions import GoogleAPIError

from .prompts import GEMINI_SYSTEM_PROMPT
from .logger import log_gemini_transaction

# --------------------------------------------------
# Load Environment Variables
# --------------------------------------------------

load_dotenv()

# --------------------------------------------------
# Load Gemini API Key
# --------------------------------------------------

_model_name = "gemini-2.5-flash"
_api_key = None
_model = None

def configure_api_key(api_key: str):
    """Configures the Gemini API key and re-initializes model instances."""
    global _api_key, _model
    _api_key = api_key
    genai.configure(api_key=api_key)
    _model = genai.GenerativeModel(
        model_name=_model_name,
        system_instruction=GEMINI_SYSTEM_PROMPT,
        generation_config=genai.types.GenerationConfig(
            temperature=0.0,
            response_mime_type="application/json"
        )
    )
    # Dynamically reinit router model if router_service is already imported
    import sys
    if "ai.router_service" in sys.modules:
        try:
            from ai import router_service
            router_service.reinit_router_model()
        except Exception as e:
            print(f"Error reinitializing router model: {e}")

_primary_key = os.getenv("GEMINI_API_KEY")
_fallback_key = os.getenv("GEMINI_FALLBACK_API_KEY") or "AQ.Ab8RN6LNggi88Q6LKdA0uCkJYHI8RraMIerscxr93VOIYE61tA"

# Determine starting key
_api_key_to_use = _primary_key
_using_fallback = False

if not _api_key_to_use or _api_key_to_use == "YOUR_GEMINI_API_KEY_HERE":
    _api_key_to_use = _fallback_key
    _using_fallback = True

print("\n========== GEMINI STARTUP ==========")
if _using_fallback:
    print("API KEY SOURCE: Fallback Key")
    print("API KEY PREFIX (FALLBACK):", _api_key_to_use[:10] + "...")
else:
    print("API KEY SOURCE: Primary Key")
    print("API KEY PREFIX (PRIMARY):", _api_key_to_use[:10] + "...")

print("MODEL:", _model_name)

configure_api_key(_api_key_to_use)

print("Gemini model initialized successfully.")
print("====================================\n")

# Phase 8.1: shared retry helper (imported after genai.configure to avoid
# circular imports — gemini_retry has no dependency on gemini_service)
from agent.gemini_retry import call_with_retry  # noqa: E402

# Phase 8.5: Gemini call instrumentation
from agent import gemini_metrics  # noqa: E402


# Sentinel intent used when the API is rate-limited after one retry.
# Distinct from UNKNOWN so the coordinator can produce a user-friendly message.
RATE_LIMITED = "RATE_LIMITED"


def generate_sql_response(
    user_input: str,
    dynamic_context: str,
    selected_db: str,
    selected_table: str,
    history: list = None
) -> dict:
    """
    Generates SQL + Intent + Clarification from Gemini.

    Expected Gemini JSON output:

    {
        "intent": "QUERY",
        "sql": "SELECT * FROM students;",
        "question": "Which table?"
    }
    """
    history_lines = []
    if history:
        history_lines.append("CONVERSATION HISTORY:")
        for msg in history:
            role_label = "User" if msg.role == "user" else "Assistant"
            history_lines.append(f"{role_label}: {msg.text}")
        history_lines.append("")
    
    history_context = "\n".join(history_lines)
    prompt = f"{dynamic_context}\n{history_context}USER QUESTION: {user_input}"

    print("\n========== GEMINI REQUEST ==========")
    print("Selected DB:", selected_db)
    print("Selected Table:", selected_table)
    print("Question:", user_input)
    print("Prompt Length:", len(prompt))
    print("====================================\n")

    try:
        # ── Phase 8.1: rate-limit retry wrapper ──────────────────────────────
        # Phase 8.5: record this Gemini call in the metrics tracker
        gemini_metrics.record_call("sql_generator")
        response, success, rate_limit_msg = call_with_retry(
            fn=lambda: _model.generate_content(prompt),
            label="SQL Generator",
        )

        if not success:
            print(f"\n[SQL Generator] Rate limit exhausted after retry: {rate_limit_msg}")
            return {
                "intent": RATE_LIMITED,
                "sql": None,
                "question": rate_limit_msg,
                "execution_database": None,
            }

        if response and hasattr(response, "usage_metadata") and response.usage_metadata:
            gemini_metrics.record_tokens(
                "sql_generator",
                response.usage_metadata.prompt_token_count,
                response.usage_metadata.candidates_token_count
            )

        print("\n========== GEMINI RAW RESPONSE ==========")
        print(response.text)
        print("=========================================\n")

        result = json.loads(response.text)

        intent = result.get("intent", "UNKNOWN")
        sql = result.get("sql")
        question = result.get("question")
        execution_database = result.get("execution_database")

        log_gemini_transaction(
            selected_db=selected_db,
            selected_table=selected_table,
            intent=intent,
            user_question=user_input,
            generated_sql=sql
        )

        return {
            "intent": intent,
            "sql": sql,
            "question": question,
            "execution_database": execution_database
        }

    except GoogleAPIError as e:

        print("\n========== GEMINI API ERROR ==========")
        print(str(e))
        print("======================================\n")

        return {
            "intent": "UNKNOWN",
            "sql": None,
            "question": None,
            "execution_database": None
        }

    except json.JSONDecodeError as e:

        print("\n========== JSON PARSE ERROR ==========")
        print(str(e))
        print("======================================\n")

        return {
            "intent": "UNKNOWN",
            "sql": None,
            "question": None,
            "execution_database": None
        }

    except Exception as e:

        print("\n========== UNEXPECTED ERROR ==========")
        print(str(e))
        print("======================================\n")

        return {
            "intent": "UNKNOWN",
            "sql": None,
            "question": None,
            "execution_database": None
        }