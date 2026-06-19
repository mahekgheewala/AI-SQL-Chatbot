"""
Phase 8.5 — Gemini API Call Metrics
=====================================
Lightweight, thread-safe instrumentation module.

Tracks:
  - Cumulative Gemini call counts per call site (router, planner, sql_generator,
    summarizer, report_formatter).
  - Cumulative bypass counts (router_bypassed, planner_bypassed) — calls that were
    skipped due to deterministic dispatch, session short-circuit, or explicit DB match.
  - A rolling in-memory history of the most recent 50 requests, each recording the
    ordered sequence of Gemini calls made for that request.

CONCURRENCY NOTE:
  _active_request is process-global state. This implementation is intended for local
  development and single-process debugging only (Uvicorn with --workers=1).
  If the application is later run with multiple concurrent workers (e.g. Uvicorn
  --workers > 1, Gunicorn), request-scoped storage (e.g. contextvars.ContextVar)
  should replace the global _active_request tracker. No redesign is required now —
  this is a documentation-only note.
"""

from collections import deque
import threading
import uuid
from typing import Any

_lock = threading.Lock()

# ── Cumulative call counters ──────────────────────────────────────────────────
_calls: dict[str, Any] = {
    "router":           0,
    "planner":          0,
    "sql_generator":    0,
    "summarizer":       0,
    "report_formatter": 0,
    "total":            0,
    "prompt_tokens":    0,
    "response_tokens":  0,
    "total_tokens":     0,
    "estimated_cost":   0.0,
    "rate_limit_events": 0,
    "retry_attempts":   0,
}

# ── Cumulative bypass counters ────────────────────────────────────────────────
_bypasses: dict[str, int] = {
    "router_bypassed":  0,
    "planner_bypassed": 0,
}

# ── Per-request rolling history ───────────────────────────────────────────────
# Retains the last 50 requests in memory only. No persistence — resets on restart.
# Each worker maintains its own independent deque if running multi-process.
_MAX_HISTORY = 50
_request_history: deque = deque(maxlen=_MAX_HISTORY)

# ── Active request tracker ────────────────────────────────────────────────────
# Process-global. Safe for single-worker development. See CONCURRENCY NOTE above.
_active_request: dict = {}

GC_MAPPING = {
    "router": "GC-01 Router",
    "planner": "GC-02 Planner",
    "sql_generator": "GC-03 SQL Generator",
    "summarizer": "GC-04 Summarizer",
    "report_formatter": "GC-05 Report Formatter",
}


# ─── Public API ──────────────────────────────────────────────────────────────

def start_request(user_message: str) -> str:
    """
    Call at the very start of each /chat request.

    Initialises the active request tracker and returns a short unique request_id
    that can be printed in logs for correlation.
    """
    request_id = str(uuid.uuid4())[:8]
    with _lock:
        _active_request.clear()
        _active_request["request_id"]  = request_id
        _active_request["user_message"] = user_message[:120]   # truncate for safety
        _active_request["calls"]        = []
        _active_request["prompt_tokens"] = 0
        _active_request["response_tokens"] = 0
        _active_request["estimated_cost"] = 0.0
        _active_request["rate_limit_events"] = 0
        _active_request["retry_attempts"] = 0
        _active_request["calls_with_tokens"] = []
    return request_id


def get_active_request_summary() -> dict:
    """
    Return call counts and estimated tokens/cost for the currently active request.
    """
    with _lock:
        calls = list(_active_request.get("calls", []))
        prompt_tokens = _active_request.get("prompt_tokens", 0)
        response_tokens = _active_request.get("response_tokens", 0)
        estimated_cost = _active_request.get("estimated_cost", 0.0)
        rate_limit_events = _active_request.get("rate_limit_events", 0)
        retry_attempts = _active_request.get("retry_attempts", 0)
        calls_with_tokens = list(_active_request.get("calls_with_tokens", []))
        
        # Sort leaderboard descending by prompt tokens
        leaderboard = []
        for label, tokens in calls_with_tokens:
            mapped = GC_MAPPING.get(label, label)
            leaderboard.append((mapped, tokens))
        leaderboard.sort(key=lambda x: x[1], reverse=True)
        
        # Determine largest prompt
        if leaderboard:
            largest_prompt_label, largest_prompt_tokens = leaderboard[0]
        else:
            largest_prompt_label, largest_prompt_tokens = "None", 0
            
    return {
        "router":           calls.count("router"),
        "planner":          calls.count("planner"),
        "sql_generator":    calls.count("sql_generator"),
        "summarizer":       calls.count("summarizer"),
        "report_formatter": calls.count("report_formatter"),
        "total":            len(calls),
        "prompt_tokens":    prompt_tokens,
        "response_tokens":  response_tokens,
        "total_tokens":     prompt_tokens + response_tokens,
        "estimated_cost":   estimated_cost,
        "rate_limit_events": rate_limit_events,
        "retry_attempts":   retry_attempts,
        "leaderboard":      leaderboard,
        "largest_prompt_label": largest_prompt_label,
        "largest_prompt_tokens": largest_prompt_tokens,
    }


def end_request() -> None:
    """
    Call immediately before returning the response from /chat.

    Commits the active request entry to the rolling history deque and clears
    the active tracker for the next request.
    """
    with _lock:
        if not _active_request:
            return
        prompt_t = _active_request.get("prompt_tokens", 0)
        response_t = _active_request.get("response_tokens", 0)
        calls = list(_active_request.get("calls", []))
        calls_with_tokens = list(_active_request.get("calls_with_tokens", []))
        
        # Sort leaderboard descending by prompt tokens
        leaderboard = []
        for label, tokens in calls_with_tokens:
            mapped = GC_MAPPING.get(label, label)
            leaderboard.append((mapped, tokens))
        leaderboard.sort(key=lambda x: x[1], reverse=True)
        
        # Determine largest prompt
        if leaderboard:
            largest_prompt_label, largest_prompt_tokens = leaderboard[0]
        else:
            largest_prompt_label, largest_prompt_tokens = "None", 0
            
        entry = {
            "request_id":  _active_request.get("request_id", "?"),
            "user_message": _active_request.get("user_message", ""),
            "calls":        calls,
            "total_calls":  len(calls),
            "prompt_tokens": prompt_t,
            "response_tokens": response_t,
            "total_tokens": prompt_t + response_t,
            "estimated_cost": _active_request.get("estimated_cost", 0.0),
            "rate_limit_events": _active_request.get("rate_limit_events", 0),
            "retry_attempts": _active_request.get("retry_attempts", 0),
            "leaderboard":      leaderboard,
            "largest_prompt_label": largest_prompt_label,
            "largest_prompt_tokens": largest_prompt_tokens,
        }
        _request_history.appendleft(entry)
        _active_request.clear()


def record_call(label: str) -> None:
    """
    Increment the cumulative counter for a Gemini call site and append the label
    to the current request's call sequence.

    Valid labels: "router", "planner", "sql_generator", "summarizer", "report_formatter"
    """
    with _lock:
        _calls[label]    = _calls.get(label, 0) + 1
        _calls["total"]  = _calls.get("total", 0) + 1
        if "calls" in _active_request:
            _active_request["calls"].append(label)


def record_tokens(label: str, prompt_tokens: int, response_tokens: int) -> None:
    """
    Record token usage and estimated cost for a Gemini call.
    """
    with _lock:
        _calls["prompt_tokens"] = _calls.get("prompt_tokens", 0) + prompt_tokens
        _calls["response_tokens"] = _calls.get("response_tokens", 0) + response_tokens
        _calls["total_tokens"] = _calls.get("total_tokens", 0) + prompt_tokens + response_tokens
        
        # Cost logic: prompt $0.075/1M, response $0.30/1M
        cost = (prompt_tokens * 0.075 / 1_000_000) + (response_tokens * 0.30 / 1_000_000)
        _calls["estimated_cost"] = _calls.get("estimated_cost", 0.0) + cost
        
        if "prompt_tokens" in _active_request:
            _active_request["prompt_tokens"] += prompt_tokens
        if "response_tokens" in _active_request:
            _active_request["response_tokens"] += response_tokens
        if "estimated_cost" in _active_request:
            _active_request["estimated_cost"] += cost
            
        if "calls_with_tokens" in _active_request:
            _active_request["calls_with_tokens"].append((label, prompt_tokens))


def record_rate_limit(label: str) -> None:
    """
    Record a rate-limit event for the active request and globally.
    """
    with _lock:
        _calls["rate_limit_events"] = _calls.get("rate_limit_events", 0) + 1
        if "rate_limit_events" in _active_request:
            _active_request["rate_limit_events"] += 1


def record_retry(label: str) -> None:
    """
    Record a retry attempt for the active request and globally.
    """
    with _lock:
        _calls["retry_attempts"] = _calls.get("retry_attempts", 0) + 1
        if "retry_attempts" in _active_request:
            _active_request["retry_attempts"] += 1


def record_bypass(label: str) -> None:
    """
    Increment the bypass counter for a Gemini call site.
    Called when a call is deterministically skipped.

    Valid labels: "router", "planner"
    The counter key is automatically formatted as "<label>_bypassed".
    """
    with _lock:
        key = f"{label}_bypassed"
        _bypasses[key] = _bypasses.get(key, 0) + 1


def get_metrics() -> dict:
    """
    Return a snapshot of all counters and the rolling request history.

    Also computes effective_call_reduction_pct:
      bypasses / (calls + bypasses) × 100
    """
    with _lock:
        total_calls    = _calls.get("total", 0)
        total_bypasses = sum(_bypasses.values())
        total_possible = total_calls + total_bypasses

        reduction_pct = (
            round(total_bypasses / total_possible * 100, 1)
            if total_possible > 0 else 0.0
        )

        return {
            "calls":                       dict(_calls),
            "bypasses":                    dict(_bypasses),
            "effective_call_reduction_pct": reduction_pct,
            "recent_requests":             list(_request_history),
        }


def reset_metrics() -> None:
    """
    Reset all counters and history to zero.
    Intended for use via GET /api/debug/gemini-metrics?reset=true.
    """
    with _lock:
        for k in list(_calls):
            if k == "estimated_cost":
                _calls[k] = 0.0
            else:
                _calls[k] = 0
        for k in list(_bypasses):
            _bypasses[k] = 0
        _request_history.clear()
        _active_request.clear()
