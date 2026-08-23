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

CONCURRENCY NOTE (Phase 9.x):
  The in-flight ("active") request tracker is stored in a contextvars.ContextVar.
  Each concurrent FastAPI request runs in its own asyncio task context, so
  simultaneous requests can no longer overwrite each other's request context —
  a request only ever sees its OWN in-flight Gemini metrics. Previously this
  tracker was a single process-global dict; under concurrency one request's
  start_request() could clear and overwrite another request's in-progress
  context mid-flight, mis-attributing calls and tokens.

  The cumulative call/bypass counters and the rolling request history remain
  process-global BY DESIGN (they aggregate across all requests) and are guarded
  by _lock.
"""

from collections import deque
import threading
import uuid
from contextvars import ContextVar
from typing import Any

_lock = threading.Lock()

# ── Cost estimation ────────────────────────────────────────────────────────
# Single source of truth for Gemini pricing — was previously copy-pasted
# across gemini_metrics.py, agent_coordinator.py, tools.py, and gemini_service.py,
# so a pricing change (or a model change) required editing every call site.
_PROMPT_COST_PER_TOKEN = 0.075 / 1_000_000
_COMPLETION_COST_PER_TOKEN = 0.30 / 1_000_000


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Estimated USD cost for one Gemini call, given prompt/completion token
    counts. Pricing: $0.075/1M prompt tokens, $0.30/1M completion tokens."""
    return (prompt_tokens * _PROMPT_COST_PER_TOKEN) + (completion_tokens * _COMPLETION_COST_PER_TOKEN)


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
# Context-local: each request keeps its own tracker in its asyncio task context.
# The default is an empty dict (falsy), matching the previous cleared-global
# semantics used by end_request()/get_active_request_summary().
_active_request: ContextVar[dict] = ContextVar("_active_request", default={})

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

    Initialises the active request tracker in the CURRENT request context and
    returns a short unique request_id that can be printed in logs for correlation.
    A ContextVar token is retained on the tracker so end_request() and
    reset_active_request() can always reset the context (including from finally).

    If the current context already has an active tracker (a nested re-instrument,
    e.g. the pipeline path re-initialising the same request), the previous tracker
    is replaced — matching the historical 'clear then set' behaviour.
    """
    request_id = str(uuid.uuid4())[:8]
    ctx = {
        "request_id": request_id,
        "user_message": user_message[:120],   # truncate for safety
        "calls":        [],
        "prompt_tokens": 0,
        "response_tokens": 0,
        "estimated_cost": 0.0,
        "rate_limit_events": 0,
        "retry_attempts": 0,
        "calls_with_tokens": [],
        "_token": None,
    }
    with _lock:
        prev = _active_request.get()
        if prev:
            prev_token = prev.get("_token")
            if prev_token is not None:
                _active_request.reset(prev_token)
        token = _active_request.set(ctx)
        ctx["_token"] = token
    return request_id


def get_active_request_summary() -> dict:
    """
    Return call counts and estimated tokens/cost for the currently active request.
    """
    with _lock:
        active = _active_request.get()
        calls = list(active.get("calls", []))
        prompt_tokens = active.get("prompt_tokens", 0)
        response_tokens = active.get("response_tokens", 0)
        estimated_cost = active.get("estimated_cost", 0.0)
        rate_limit_events = active.get("rate_limit_events", 0)
        retry_attempts = active.get("retry_attempts", 0)
        calls_with_tokens = list(active.get("calls_with_tokens", []))
        
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

    Commits the active request entry to the rolling history deque and resets
    the context-local tracker back to its default state.
    """
    with _lock:
        active = _active_request.get()
        if not active:
            return
        prompt_t = active.get("prompt_tokens", 0)
        response_t = active.get("response_tokens", 0)
        calls = list(active.get("calls", []))
        calls_with_tokens = list(active.get("calls_with_tokens", []))
        
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
            "request_id":  active.get("request_id", "?"),
            "user_message": active.get("user_message", ""),
            "calls":        calls,
            "total_calls":  len(calls),
            "prompt_tokens": prompt_t,
            "response_tokens": response_t,
            "total_tokens": prompt_t + response_t,
            "estimated_cost": active.get("estimated_cost", 0.0),
            "rate_limit_events": active.get("rate_limit_events", 0),
            "retry_attempts": active.get("retry_attempts", 0),
            "leaderboard":      leaderboard,
            "largest_prompt_label": largest_prompt_label,
            "largest_prompt_tokens": largest_prompt_tokens,
        }
        _request_history.appendleft(entry)
        token = active.get("_token")
        if token is not None:
            _active_request.reset(token)


def record_call(label: str) -> None:
    """
    Increment the cumulative counter for a Gemini call site and append the label
    to the current request's call sequence.

    Valid labels: "router", "planner", "sql_generator", "summarizer", "report_formatter"
    """
    with _lock:
        _calls[label]    = _calls.get(label, 0) + 1
        _calls["total"]  = _calls.get("total", 0) + 1
        active = _active_request.get()
        if "calls" in active:
            active["calls"].append(label)


def record_tokens(label: str, prompt_tokens: int, response_tokens: int) -> None:
    """
    Record token usage and estimated cost for a Gemini call.
    """
    with _lock:
        _calls["prompt_tokens"] = _calls.get("prompt_tokens", 0) + prompt_tokens
        _calls["response_tokens"] = _calls.get("response_tokens", 0) + response_tokens
        _calls["total_tokens"] = _calls.get("total_tokens", 0) + prompt_tokens + response_tokens
        
        cost = estimate_cost(prompt_tokens, response_tokens)
        _calls["estimated_cost"] = _calls.get("estimated_cost", 0.0) + cost
        
        active = _active_request.get()
        if "prompt_tokens" in active:
            active["prompt_tokens"] += prompt_tokens
        if "response_tokens" in active:
            active["response_tokens"] += response_tokens
        if "estimated_cost" in active:
            active["estimated_cost"] += cost
            
        if "calls_with_tokens" in active:
            active["calls_with_tokens"].append((label, prompt_tokens))


def record_rate_limit(label: str) -> None:
    """
    Record a rate-limit event for the active request and globally.
    """
    with _lock:
        _calls["rate_limit_events"] = _calls.get("rate_limit_events", 0) + 1
        active = _active_request.get()
        if "rate_limit_events" in active:
            active["rate_limit_events"] += 1


def record_retry(label: str) -> None:
    """
    Record a retry attempt for the active request and globally.
    """
    with _lock:
        _calls["retry_attempts"] = _calls.get("retry_attempts", 0) + 1
        active = _active_request.get()
        if "retry_attempts" in active:
            active["retry_attempts"] += 1


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


def reset_active_request() -> None:
    """
    Reset the current context's active-request tracker WITHOUT committing an
    entry to the rolling history. Intended for finally blocks so an exception
    can never leak request context into a later request.
    """
    with _lock:
        active = _active_request.get()
        if not active:
            return
        token = active.get("_token")
        if token is not None:
            _active_request.reset(token)


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
        active = _active_request.get()
        if active:
            token = active.get("_token")
            if token is not None:
                _active_request.reset(token)
