"""
Phase 9.5 — Admin Router
==========================
Exposes diagnostic and monitoring endpoints for the Admin Dashboard.

All endpoints are prefixed /api/admin and are protected by admin role-based
access control (ADMIN or SUPER_ADMIN) via get_current_admin.

Endpoints:
  GET /api/admin/logs         — Paginated, filtered log explorer
  GET /api/admin/analytics    — Aggregated metrics from logs + in-memory
  GET /api/admin/health       — Real-time system health snapshot
  GET /api/admin/export       — Streaming log download (JSON or CSV)

WARNING: These endpoints expose raw log data including SQL, session IDs,
         and database names. Only ADMIN/SUPER_ADMIN roles may access them.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from auth.dependencies import get_current_admin
from models.domain import User
from monitoring.log_reader import read_logs
from monitoring.analytics import get_analytics
from monitoring.metrics_store import get_health_snapshot
from monitoring.exporter import stream_json, stream_csv

router = APIRouter(prefix="/admin", tags=["admin"])


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO datetime string to a timezone-aware datetime, or return None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ─── Log Explorer ─────────────────────────────────────────────────────────────

@router.get("/logs")
async def get_logs(
    current_admin: User = Depends(get_current_admin),
    log_type: str = Query("app", description="Log file: app | error | audit"),
    level: Optional[str] = Query(None, description="Filter by log level: INFO | ERROR | WARNING"),
    session_id: Optional[str] = Query(None, description="Filter by session_id (substring)"),
    request_id: Optional[str] = Query(None, description="Filter by request_id (substring)"),
    database: Optional[str] = Query(None, description="Filter by database_name (substring)"),
    intent: Optional[str] = Query(None, description="Filter by intent (substring)"),
    from_ts: Optional[str] = Query(None, description="Start datetime (ISO 8601)"),
    to_ts: Optional[str] = Query(None, description="End datetime (ISO 8601)"),
    search: Optional[str] = Query(None, description="Free-text search in message field"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=200, description="Entries per page (max 200)"),
) -> dict:
    """
    Return paginated, filtered log entries from the specified log file.

    Supports filtering by level, session/request IDs, database name, intent,
    time range, and free-text search on the message field.
    """
    return read_logs(
        log_type=log_type,
        level=level,
        session_id=session_id,
        request_id=request_id,
        database=database,
        intent=intent,
        from_ts=_parse_datetime(from_ts),
        to_ts=_parse_datetime(to_ts),
        search=search,
        page=page,
        page_size=page_size,
    )


# ─── Analytics Dashboard ──────────────────────────────────────────────────────

@router.get("/analytics")
async def get_analytics_endpoint(
    current_admin: User = Depends(get_current_admin),
    from_ts: Optional[str] = Query(None, description="Start datetime (ISO 8601, default: 24h ago)"),
    to_ts: Optional[str] = Query(None, description="End datetime (ISO 8601, default: now)"),
) -> dict:
    """
    Return aggregated metrics for the given time window.

    Parses app.log and error.log asynchronously (in a thread pool) and
    combines the results with in-memory Gemini call counters.
    """
    return await get_analytics(
        from_ts=_parse_datetime(from_ts),
        to_ts=_parse_datetime(to_ts),
    )


# ─── System Health ────────────────────────────────────────────────────────────

@router.get("/health")
async def get_health(current_admin: User = Depends(get_current_admin)) -> dict:
    """
    Return a real-time snapshot of system health:
      - Active session count
      - Cache generation counter
      - Routing summaries count
      - Log file sizes and last-modified timestamps
      - Server uptime in seconds
      - Raw Gemini metrics (from in-memory counters)
    """
    return get_health_snapshot()


# ─── Log Export ───────────────────────────────────────────────────────────────

@router.get("/export")
async def export_logs(
    current_admin: User = Depends(get_current_admin),
    log_type: str = Query("app", description="Log file: app | error | audit"),
    format: str = Query("json", description="Export format: json | csv"),
    level: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    request_id: Optional[str] = Query(None),
    database: Optional[str] = Query(None),
    intent: Optional[str] = Query(None),
    from_ts: Optional[str] = Query(None, description="Start datetime (ISO 8601)"),
    to_ts: Optional[str] = Query(None, description="End datetime (ISO 8601)"),
    search: Optional[str] = Query(None),
) -> StreamingResponse:
    """
    Stream a filtered log export as a downloadable file.

    Returns:
      - JSON: a JSON array of log entry objects
      - CSV: a CSV file with standard columns

    The export uses server-sent streaming so large files are delivered
    without loading them fully into server memory.
    """
    parsed_from = _parse_datetime(from_ts)
    parsed_to = _parse_datetime(to_ts)

    filters = dict(
        level=level,
        session_id=session_id,
        request_id=request_id,
        database=database,
        intent=intent,
        from_ts=parsed_from,
        to_ts=parsed_to,
        search=search,
    )

    if format.lower() == "csv":
        filename = f"logs_export_{log_type}.csv"
        return StreamingResponse(
            stream_csv(log_type, **filters),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    else:
        filename = f"logs_export_{log_type}.json"
        return StreamingResponse(
            stream_json(log_type, **filters),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
