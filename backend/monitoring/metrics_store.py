"""
Phase 9.5 — In-Memory Metrics Store
=====================================
Maintains ephemeral real-time server statistics for the Admin Dashboard
health panel. All data is lost on server restart — logs are the persistent
source of truth for historical analytics.

Tracks:
  - Server start time (uptime calculation)
  - Active sessions count (pulled from session_store at query time)
  - Cache generation counter (pulled from metadata_store)
  - Routing summaries count (pulled from metadata_store)

This module is intentionally thin — it delegates to the existing live
subsystems rather than duplicating their state. The primary purpose is
to provide a single aggregation point for the health endpoint.

Future compatibility (Phase 13):
  To add persistent metrics, extend this module to write snapshots to
  a metrics_daily table without changing the public API surface.
"""

import time
from datetime import datetime, timezone

# Track server start time (recorded once at import)
_SERVER_START_TIME: float = time.monotonic()
_SERVER_START_UTC: str = datetime.now(timezone.utc).isoformat()


def get_server_uptime_seconds() -> float:
    """Return the number of seconds since the server process started."""
    return time.monotonic() - _SERVER_START_TIME


def get_server_start_utc() -> str:
    """Return the UTC ISO timestamp when this server process started."""
    return _SERVER_START_UTC


def get_health_snapshot() -> dict:
    """
    Aggregate a real-time health snapshot by querying existing subsystems.

    Returns a dict suitable for the GET /api/admin/health endpoint.
    """
    # Import here to avoid circular imports at module load time
    from state.session_store import store as session_store
    from state.metadata_store import get_metadata, get_cache_generation
    from agent.gemini_metrics import get_metrics as get_gemini_metrics
    from monitoring.log_reader import get_log_file_info

    metadata = get_metadata()
    routing_summaries = metadata.get("routing_summaries", {})
    active_sessions = session_store.active_session_count()
    cache_generation = get_cache_generation()
    routing_count = len(routing_summaries)

    log_info = get_log_file_info()
    gemini_metrics = get_gemini_metrics()

    uptime_s = get_server_uptime_seconds()

    return {
        "active_sessions": active_sessions,
        "cache_generation": cache_generation,
        "routing_summaries_count": routing_count,
        "log_file_sizes": log_info,
        "server_uptime_s": round(uptime_s, 1),
        "server_start_utc": get_server_start_utc(),
        "gemini_metrics": gemini_metrics,
    }
