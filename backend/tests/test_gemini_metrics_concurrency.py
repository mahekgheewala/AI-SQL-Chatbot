"""
Phase 9.x — Gemini Metrics ContextVar Concurrency Regression Test
=================================================================
Proves that the in-flight Gemini metrics tracker is isolated per request
(contextvars.ContextVar) so two overlapping requests can never read or
overwrite each other's request context.

Simulated concurrent users:
  - User A: records router + planner Gemini calls
  - User B: records sql_generator + summarizer Gemini calls

Both run simultaneously (barrier-synchronised so they overlap mid-flight).
Verifies:
  1. A's in-flight metrics never contain B's request context (and vice versa).
  2. The ContextVar returns to its default state after each request completes.
  3. Exceptions also reset the ContextVar correctly (finally path).
  4. The aggregate counters / rolling history still behave cumulatively.

Run:
    python  tests/test_gemini_metrics_concurrency.py
    pytest   tests/test_gemini_metrics_concurrency.py -s
"""

import asyncio
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from agent import gemini_metrics as gm
from agent.gemini_metrics import _active_request

_OVERLAP_TIMEOUT = 5.0


def _assert_tracker_default(who, value):
    assert not value, f"{who}: active tracker not reset to default: {value!r}"


async def _overlapping_pair():
    """Run two barrier-synchronised requests that overlap mid-flight."""
    ready_a = asyncio.Event()
    ready_b = asyncio.Event()

    async def user_a():
        out = {}
        try:
            rid = gm.start_request("user A message")
            out["rid"] = rid
            gm.record_call("router")
            gm.record_tokens("router", 100, 20)
            ready_a.set()
            await asyncio.wait_for(ready_b.wait(), _OVERLAP_TIMEOUT)
            # B is now also in-flight: snapshot our OWN context mid-overlap.
            out["summary"] = gm.get_active_request_summary()
            out["ctx_rid"] = _active_request.get().get("request_id")
            gm.record_call("planner")
            gm.record_tokens("planner", 50, 10)
            return out
        finally:
            gm.end_request()
            out["after_end"] = _active_request.get()

    async def user_b():
        out = {}
        try:
            rid = gm.start_request("user B message")
            out["rid"] = rid
            gm.record_call("sql_generator")
            gm.record_tokens("sql_generator", 200, 30)
            ready_b.set()
            await asyncio.wait_for(ready_a.wait(), _OVERLAP_TIMEOUT)
            out["summary"] = gm.get_active_request_summary()
            out["ctx_rid"] = _active_request.get().get("request_id")
            gm.record_call("summarizer")
            gm.record_tokens("summarizer", 80, 15)
            return out
        finally:
            gm.end_request()
            out["after_end"] = _active_request.get()

    a, b = await asyncio.gather(user_a(), user_b())
    return a, b


def test_concurrent_requests_never_read_each_others_context():
    """A and B overlap while Gemini metrics are active; each sees only itself."""
    a, b = asyncio.run(_overlapping_pair())

    # A's tracker holds A's request_id (never B's) during the overlap.
    assert a["ctx_rid"] == a["rid"]
    assert a["ctx_rid"] != b["rid"]
    # A's in-flight summary shows only A's router call; B's calls are absent.
    assert a["summary"]["router"] == 1
    assert a["summary"]["sql_generator"] == 0
    assert a["summary"]["total"] == 1
    assert a["summary"]["prompt_tokens"] == 100

    # Mirror for B.
    assert b["ctx_rid"] == b["rid"]
    assert b["ctx_rid"] != a["rid"]
    assert b["summary"]["sql_generator"] == 1
    assert b["summary"]["router"] == 0
    assert b["summary"]["total"] == 1
    assert b["summary"]["prompt_tokens"] == 200


def test_no_context_leak_after_completion():
    """Each request's context returns to its default after end_request()."""
    a, b = asyncio.run(_overlapping_pair())
    _assert_tracker_default("A after completion", a["after_end"])
    _assert_tracker_default("B after completion", b["after_end"])
    _assert_tracker_default("caller context", _active_request.get())


def test_sync_context_returns_to_default_after_end_request():
    """Synchronous start/end also resets the ContextVar."""
    gm.start_request("sync message")
    try:
        assert _active_request.get().get("user_message") == "sync message"
    finally:
        gm.end_request()
    _assert_tracker_default("sync start/end", _active_request.get())


def test_exception_resets_context_via_finally():
    """An exception escaping the request still resets the ContextVar."""

    async def boom_then_check():
        after_finally = {}
        try:
            try:
                gm.start_request("boom message")
                gm.record_call("router")
                raise RuntimeError("simulated failure")
            finally:
                gm.end_request()
        except RuntimeError:
            # Same task context: the finally already reset the tracker.
            after_finally["value"] = _active_request.get()
        return after_finally

    result = asyncio.run(boom_then_check())
    _assert_tracker_default("after exception (same context)", result["value"])
    _assert_tracker_default("caller context", _active_request.get())


def test_history_and_aggregates_still_cumulative():
    """ContextVar isolation does not alter aggregate counters/history."""
    gm.reset_metrics()
    asyncio.run(_overlapping_pair())

    metrics = gm.get_metrics()
    agg = metrics["calls"]
    assert agg["router"] == 1
    assert agg["sql_generator"] == 1
    assert agg["planner"] == 1
    assert agg["summarizer"] == 1
    assert agg["total"] == 4

    by_message = {e["user_message"]: e for e in metrics["recent_requests"]}
    assert by_message["user A message"]["total_calls"] == 2
    assert by_message["user B message"]["total_calls"] == 2
    assert by_message["user A message"]["calls"] == ["router", "planner"]
    assert by_message["user B message"]["calls"] == ["sql_generator", "summarizer"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-s", "-v"]))
