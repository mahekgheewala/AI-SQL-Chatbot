"""
Phase 8.1 — Shared Gemini Retry Helper
=======================================
A single, reusable function that wraps any callable Gemini API call with
a one-shot retry on 429 Resource Exhausted / rate-limit errors.

Rules:
  * Reads the retry_delay from the error metadata when available.
  * Falls back to a configurable default wait if no delay is embedded.
  * Retries exactly ONCE — no infinite loops.
  * If the retry also fails, returns a sentinel value (None) so callers
    can produce a user-friendly message rather than an UNKNOWN intent.
  * Does NOT silently convert rate-limit failures into UNKNOWN intents.
  * Never touches the database, validation pipeline, or session memory.
"""

import time
import re
import os
from typing import Any, Callable, Optional


# Default wait in seconds when no retry_delay is found in the error
_DEFAULT_RETRY_WAIT = 15
# Extra buffer added on top of the suggested retry_delay (seconds)
_RETRY_BUFFER = 1


def _extract_retry_delay(error: Exception) -> Optional[int]:
    """
    Attempt to parse a retry_delay from a Gemini 429 error.

    The Google AI SDK typically surfaces quota errors as:
      google.api_core.exceptions.ResourceExhausted
    with a string representation containing 'retry_delay { seconds: N }'.

    Falls back to None if the pattern is not found.
    """
    error_text = str(error)

    # Pattern: retry_delay { seconds: 18 }  or  retryDelay: "18s"
    match = re.search(r"retry_delay\s*\{\s*seconds:\s*(\d+)", error_text)
    if match:
        return int(match.group(1))

    match = re.search(r'"retryDelay"\s*:\s*"(\d+)s?"', error_text)
    if match:
        return int(match.group(1))

    # Some versions embed it as plain text: "Retry after 18 seconds"
    match = re.search(r"[Rr]etry after (\d+)", error_text)
    if match:
        return int(match.group(1))

    return None


def _is_rate_limit_error(error: Exception) -> bool:
    """Return True if the error is a Gemini 429 / quota-exhausted error."""
    error_text = str(error).lower()
    error_type = type(error).__name__.lower()
    return (
        "429" in error_text
        or "resourceexhausted" in error_type
        or "resource_exhausted" in error_text
        or "quota" in error_text
        or "rate limit" in error_text
        or "generaterequests" in error_text  # matches the specific quota metric names
    )


def _is_auth_error(error: Exception) -> bool:
    """Return True if the error is related to invalid/expired API key or authentication."""
    error_text = str(error).lower()
    error_type = type(error).__name__.lower()
    return (
        "api key not valid" in error_text
        or "invalid api key" in error_text
        or "api_key_invalid" in error_text
        or "permissiondenied" in error_type
        or "unauthenticated" in error_type
        or "unauthorized" in error_text
        or "forbidden" in error_text
        or "invalid argument" in error_text
    )


_has_fallen_back = False


def reset_fallback_state():
    """Resets the fallback state tracker (for testing purposes)."""
    global _has_fallen_back
    _has_fallen_back = False


def _is_retryable_error(error: Exception) -> bool:
    """Return True if the error is a Gemini 429 / quota-exhausted error, or transient server error (500/502/503/504)."""
    if _is_rate_limit_error(error):
        return True
    
    error_text = str(error).lower()
    error_type = type(error).__name__.lower()
    
    transient_indicators = [
        "500", "502", "503", "504",
        "internal server error",
        "service unavailable",
        "gateway timeout",
        "bad gateway",
        "unavailable",
        "deadline exceeded",
        "deadlineexceeded",
        "serviceunavailable",
        "internalservererror"
    ]
    return any(indicator in error_text or indicator in error_type for indicator in transient_indicators)


def _infer_gemini_reason(label: str) -> str:
    lbl = label.lower()
    if "planner" in lbl:
        return "Complex SQL / Step Planning"
    if "router" in lbl:
        return "Database Routing"
    if "sql generator" in lbl:
        return "SQL Query Generation"
    if "summarizer" in lbl:
        return "Dataset Natural Language Summarization"
    if "report" in lbl:
        return "Markdown Report Generation"
    return "AI Reasoning"


def _get_operation_specific_fallback(label: str) -> str:
    lbl = label.lower()
    if "planner" in lbl:
        return "The AI planner is temporarily busy. Please try again in a few seconds."
    if "sql generator" in lbl:
        return "The AI query generation service is temporarily busy. Please try again in a few seconds."
    if "router" in lbl:
        return "The AI database routing service is temporarily unavailable. Please try again in a few seconds."
    if "summarizer" in lbl:
        return "The AI explanation service is temporarily unavailable. I can still display the table schema."
    if "report" in lbl:
        return "The AI report generation service is temporarily busy. Please try again in a few seconds."
    return "The AI assistant service is temporarily unavailable. Please try again in a few seconds."


def call_with_retry(
    fn: Callable[[], Any],
    label: str = "Gemini",
    default_wait: int = _DEFAULT_RETRY_WAIT,
) -> tuple[Any, bool, str | None]:
    """
    Execute fn() with dynamic fallback support and up to 2 automatic retries (3 attempts total)
    on rate-limit or temporary server errors.
    """
    global _has_fallen_back
    max_attempts = 3
    backoff_base = 2
    metric_label = label.lower().replace(" ", "_")

    for attempt in range(1, max_attempts + 1):
        # 1. Print structured GEMINI REQUEST block (Requirement 7)
        print("\n====================================")
        print("GEMINI REQUEST")
        print("====================================")
        print(f"Component:\n{label}")
        print(f"Reason:\n{_infer_gemini_reason(label)}")
        print(f"Retry:\n{attempt - 1} / {max_attempts - 1}")
        print("====================================\n")

        try:
            res = fn()
            if attempt > 1:
                print(f"[Phase 10.4.x] {label} attempt {attempt} succeeded.")
            return res, True, None

        except Exception as error:
            is_auth = _is_auth_error(error)
            is_rate = _is_rate_limit_error(error)

            # Fallback to secondary API key if configured and not yet used
            if (is_auth or is_rate) and not _has_fallen_back:
                fallback_key = os.getenv("GEMINI_FALLBACK_API_KEY")
                if fallback_key:
                    print(f"\n[Phase 8.1] {label} failed on attempt {attempt}. Attempting dynamic fallback to fallback API key...")
                    try:
                        from ai.model_manager import configure_api_key
                        configure_api_key(fallback_key)
                        _has_fallen_back = True
                        print(f"[Phase 8.1] Reconfigured with fallback API key. Retrying immediately...")
                        res = fn()
                        return res, True, None
                    except Exception as fallback_err:
                        print(f"[Phase 8.1] Dynamic fallback failed: {fallback_err}. Continuing with retry sequence.")

            # If not a retryable error, raise it immediately
            if not _is_retryable_error(error):
                print(f"\n[Phase 10.4.x] {label} non-retryable error encountered: {error}")
                raise

            # Record metrics
            from agent import gemini_metrics
            gemini_metrics.record_rate_limit(metric_label)
            gemini_metrics.record_retry(metric_label)

            # If maximum attempts reached, return the friendly operation-specific fallback
            if attempt == max_attempts:
                fallback_msg = _get_operation_specific_fallback(label)
                print(f"\n[Phase 10.4.x] {label} failed after {max_attempts} attempts. Returning fallback: '{fallback_msg}'")
                return None, False, fallback_msg

            # Exponential backoff wait (2s -> 4s -> 8s)
            delay = (backoff_base ** attempt) + _RETRY_BUFFER
            quota_delay = _extract_retry_delay(error)
            if quota_delay and quota_delay > delay:
                delay = quota_delay + _RETRY_BUFFER

            print(
                f"\n[Phase 10.4.x] {label} attempt {attempt} failed ({error}). "
                f"Retrying in {delay}s (Attempt {attempt + 1}/{max_attempts})…"
            )
            time.sleep(delay)
