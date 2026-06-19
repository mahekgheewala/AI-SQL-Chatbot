"""
Phase 8.6 — Debug Router
=========================
Exposes a single read-only endpoint for inspecting Gemini API call metrics
collected by agent/gemini_metrics.py.

Endpoints:
  GET /api/debug/gemini-metrics
      Returns current cumulative counters, bypass counts, effective call
      reduction percentage, and the rolling history of the last 50 requests.

  GET /api/debug/gemini-metrics?reset=true
      Resets all counters and history to zero, then returns the zeroed state.
      Useful for isolating quota consumption during a specific test session.

This router is registered in main.py with prefix="/api".
It is intended for local development only and should be removed or
gated behind authentication before any production deployment.
"""

from fastapi import APIRouter
from agent.gemini_metrics import get_metrics, reset_metrics

router = APIRouter()


@router.get("/debug/gemini-metrics")
def gemini_metrics_endpoint(reset: bool = False) -> dict:
    """
    Return Gemini API call metrics for the current server process.

    Query Parameters:
        reset (bool): If true, resets all counters and history before returning.
    """
    if reset:
        reset_metrics()
        print("[Phase 8.6] Gemini metrics reset via debug endpoint.")

    return get_metrics()
