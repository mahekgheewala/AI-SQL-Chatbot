import os
import json
import time

from google.api_core.exceptions import GoogleAPIError

from .prompts import GEMINI_SYSTEM_PROMPT
from .logger import log_gemini_transaction
from ai import model_manager

# Read the actually-configured model at each log site instead of a stale
# hardcoded name (GEMINI_MODEL can differ from the old default).
def _model_name():
    return model_manager.current_model_name()

def configure_api_key(api_key: str):
    """Configures the Gemini API key and re-initializes model instances."""
    model_manager.configure_api_key(api_key)


# Phase 8.1: shared retry helper (imported after genai.configure to avoid
# circular imports — gemini_retry has no dependency on gemini_service)
from agent.gemini_retry import call_with_retry  # noqa: E402

# Phase 8.5: Gemini call instrumentation
from agent import gemini_metrics  # noqa: E402
from utils.logging_config import logger_ai  # noqa: E402


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

    start_time = time.perf_counter()
    try:
        # ── Phase 8.1: rate-limit retry wrapper ──────────────────────────────
        # Phase 8.5: record this Gemini call in the metrics tracker
        gemini_metrics.record_call("sql_generator")
        response, success, rate_limit_msg = call_with_retry(
            fn=lambda: model_manager.SQL_GENERATOR_MODEL.generate_content(prompt),
            label="SQL Generator",
        )
        duration_ms = (time.perf_counter() - start_time) * 1000

        if not success:
            print(f"\n[SQL Generator] Rate limit exhausted after retry: {rate_limit_msg}")
            try:
                logger_ai.error(
                    f"AI SQL generation failed: rate limit exhausted - {rate_limit_msg}",
                    extra={
                        "category": "ai",
                        "operation_type": "AI_SQL_GENERATION",
                        "intent": RATE_LIMITED,
                        "success": False,
                        "execution_time_ms": duration_ms,
                        "error": rate_limit_msg,
                        "model": _model_name()
                    }
                )
            except Exception:
                pass
            return {
                "intent": RATE_LIMITED,
                "sql": None,
                "question": rate_limit_msg,
                "execution_database": None,
            }

        prompt_tokens = 0
        completion_tokens = 0
        cost = 0.0
        if response and hasattr(response, "usage_metadata") and response.usage_metadata:
            prompt_tokens = response.usage_metadata.prompt_token_count
            completion_tokens = response.usage_metadata.candidates_token_count
            gemini_metrics.record_tokens(
                "sql_generator",
                prompt_tokens,
                completion_tokens
            )
            cost = gemini_metrics.estimate_cost(prompt_tokens, completion_tokens)

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

        # JSON AI logging
        log_extra = {
            "category": "ai",
            "operation_type": "AI_SQL_GENERATION",
            "intent": intent,
            "success": True,
            "execution_time_ms": duration_ms,
            "prompt_length": len(prompt),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost": cost,
            "model": _model_name()
        }
        if os.getenv("LOG_FULL_PROMPTS", "false").lower() == "true":
            log_extra["prompt"] = prompt
            log_extra["response_text"] = response.text

        try:
            logger_ai.info(f"AI SQL generation completed successfully for intent '{intent}'", extra=log_extra)
        except Exception:
            pass

        return {
            "intent": intent,
            "sql": sql,
            "question": question,
            "execution_database": execution_database
        }

    except GoogleAPIError as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        print("\n========== GEMINI API ERROR ==========")
        print(str(e))
        print("======================================\n")

        try:
            logger_ai.error(
                f"AI SQL generation failed: Gemini API error - {str(e)}",
                exc_info=True,
                extra={
                    "category": "ai",
                    "operation_type": "AI_SQL_GENERATION",
                    "intent": "UNKNOWN",
                    "success": False,
                    "execution_time_ms": duration_ms,
                    "error": str(e),
                    "model": _model_name()
                }
            )
        except Exception:
            pass

        return {
            "intent": "UNKNOWN",
            "sql": None,
            "question": None,
            "execution_database": None
        }

    except json.JSONDecodeError as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        print("\n========== JSON PARSE ERROR ==========")
        print(str(e))
        print("======================================\n")

        try:
            logger_ai.error(
                f"AI SQL generation failed: JSON parse error - {str(e)}",
                exc_info=True,
                extra={
                    "category": "ai",
                    "operation_type": "AI_SQL_GENERATION",
                    "intent": "UNKNOWN",
                    "success": False,
                    "execution_time_ms": duration_ms,
                    "error": str(e),
                    "model": _model_name()
                }
            )
        except Exception:
            pass

        return {
            "intent": "UNKNOWN",
            "sql": None,
            "question": None,
            "execution_database": None
        }

    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        print("\n========== UNEXPECTED ERROR ==========")
        print(str(e))
        print("======================================\n")

        try:
            logger_ai.error(
                f"AI SQL generation failed: Unexpected error - {str(e)}",
                exc_info=True,
                extra={
                    "category": "ai",
                    "operation_type": "AI_SQL_GENERATION",
                    "intent": "UNKNOWN",
                    "success": False,
                    "execution_time_ms": duration_ms,
                    "error": str(e),
                    "model": _model_name()
                }
            )
        except Exception:
            pass

        return {
            "intent": "UNKNOWN",
            "sql": None,
            "question": None,
            "execution_database": None
        }