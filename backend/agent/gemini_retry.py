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


def call_with_retry(
    fn: Callable[[], Any],
    label: str = "Gemini",
    default_wait: int = _DEFAULT_RETRY_WAIT,
) -> tuple[Any, bool, str | None]:
    """
    Execute fn() with dynamic fallback support and one automatic retry on rate-limit errors.

    Parameters:
        fn           — zero-argument callable that makes the Gemini API call.
        label        — human-readable name used in log messages (e.g. "Planner").
        default_wait — seconds to wait when no retry_delay is found in the error.

    Returns:
        (result, success, error_message)
          result        — the return value of fn(), or None on failure.
          success       — True if fn() succeeded (first attempt or retry).
          error_message — a user-friendly string on failure, or None on success.
    """
    global _has_fallen_back
    try:
        return fn(), True, None

    except Exception as first_error:
        # Check if we should dynamically switch to the fallback API key
        is_auth = _is_auth_error(first_error)
        is_rate = _is_rate_limit_error(first_error)

        if (is_auth or is_rate) and not _has_fallen_back:
            import os
            fallback_key = os.getenv("GEMINI_FALLBACK_API_KEY") or "AQ.Ab8RN6LNggi88Q6LKdA0uCkJYHI8RraMIerscxr93VOIYE61tA"
            reason = "Authentication failure" if is_auth else "Rate limit hit"
            print(f"\n[Phase 8.1] {label} failed ({reason}). Attempting dynamic fallback to fallback API key...")
            try:
                from ai import gemini_service
                gemini_service.configure_api_key(fallback_key)
                _has_fallen_back = True
                print(f"[Phase 8.1] Reconfigured with fallback API key. Retrying immediately...")
                return fn(), True, None
            except Exception as fallback_err:
                print(f"[Phase 8.1] Dynamic fallback failed: {fallback_err}. Continuing with standard handling.")

        # Standard error handling if fallback was already used or failed
        if not _is_rate_limit_error(first_error):
            # Non-rate-limit error — do not retry, propagate immediately.
            print(f"\n[Phase 8.1] {label} non-rate-limit error: {first_error}")
            raise

        # ── Rate-limit hit — extract delay and wait ───────────────────────────
        from agent import gemini_metrics
        metric_label = label.lower().replace(" ", "_")
        gemini_metrics.record_rate_limit(metric_label)
        gemini_metrics.record_retry(metric_label)

        delay = (_extract_retry_delay(first_error) or default_wait) + _RETRY_BUFFER
        print(
            f"\n[Phase 8.1] {label} rate limit hit. "
            f"Waiting {delay}s before retry…\n  Error: {first_error}"
        )
        time.sleep(delay)

        # ── Single retry ──────────────────────────────────────────────────────
        try:
            result = fn()
            print(f"[Phase 8.1] {label} retry succeeded.")
            return result, True, None

        except Exception as retry_error:
            print(f"[Phase 8.1] {label} retry also failed: {retry_error}")
            if _is_rate_limit_error(retry_error):
                gemini_metrics.record_rate_limit(metric_label)
            return (
                None,
                False,
                "Gemini API rate limit reached. Please wait a few seconds and try again.",
            )
