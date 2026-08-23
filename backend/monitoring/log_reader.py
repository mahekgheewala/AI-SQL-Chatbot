"""
Phase 9.5 — Log Reader
========================
Parses structured JSONL log files (app.log, error.log, audit.log) and
provides paginated, filtered access to log entries for the Admin Dashboard.

All log files are assumed to be one JSON object per line (JSONL format) as
produced by SafeJSONFormatter in utils/logging_config.py.

Features:
  - Parse single or multiple log files (including rotated backups)
  - Filter by: level, session_id, request_id, database, intent, time range, free text
  - Paginate results
  - Return total count for UI pagination controls
"""

import os
import json
from datetime import datetime, timezone
from typing import Optional

# ─── Log file paths ───────────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGS_DIR = os.path.join(_BASE_DIR, "logs")

LOG_FILES = {
    "app":   os.path.join(_LOGS_DIR, "app.log"),
    "error": os.path.join(_LOGS_DIR, "error.log"),
    "audit": os.path.join(_LOGS_DIR, "audit.log"),
}

# Rotated backup file pattern: app.log.1, app.log.2, etc.
_MAX_ROTATIONS = 5


def _get_log_paths(log_type: str, include_rotations: bool = False) -> list[str]:
    """
    Return the ordered list of log file paths to read.
    If include_rotations is True, appends rotated backup files (most recent first).
    """
    base = LOG_FILES.get(log_type)
    if not base:
        return []

    paths = [base]
    if include_rotations:
        for i in range(1, _MAX_ROTATIONS + 1):
            rotated = f"{base}.{i}"
            if os.path.exists(rotated):
                paths.append(rotated)
    return paths


def _parse_timestamp(ts_str: Optional[str]) -> Optional[datetime]:
    """
    Parse an ISO 8601 timestamp string to a timezone-aware datetime.
    Returns None if parsing fails.
    """
    if not ts_str:
        return None
    try:
        # Handle both "Z" suffix and "+00:00" offset
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def _entry_matches_filters(
    entry: dict,
    *,
    level: Optional[str] = None,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    database: Optional[str] = None,
    intent: Optional[str] = None,
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
    search: Optional[str] = None,
) -> bool:
    """
    Return True if a log entry dict passes all active filters.
    All string filters are case-insensitive prefix/substring matches.
    """
    # Level filter
    if level and entry.get("level", "").upper() != level.upper():
        return False

    # Session ID filter (substring)
    if session_id:
        entry_sid = entry.get("session_id") or ""
        if session_id.lower() not in entry_sid.lower():
            return False

    # Request ID filter (substring)
    if request_id:
        entry_rid = entry.get("request_id") or ""
        if request_id.lower() not in entry_rid.lower():
            return False

    # Database filter (substring)
    if database:
        entry_db = entry.get("database_name") or ""
        if database.lower() not in entry_db.lower():
            return False

    # Intent filter (substring)
    if intent:
        entry_intent = entry.get("intent") or ""
        if intent.lower() not in entry_intent.lower():
            return False

    # Timestamp range filters
    entry_ts = _parse_timestamp(entry.get("timestamp"))
    if entry_ts:
        if from_ts and entry_ts < from_ts:
            return False
        if to_ts and entry_ts > to_ts:
            return False

    # Free-text search on message field
    if search:
        msg = entry.get("message") or ""
        if search.lower() not in msg.lower():
            return False

    return True


def read_logs(
    log_type: str = "app",
    *,
    level: Optional[str] = None,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    database: Optional[str] = None,
    intent: Optional[str] = None,
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    include_rotations: bool = False,
) -> dict:
    """
    Read and filter log entries from the specified log file.

    Returns a dict with:
      entries   — list of matching log entry dicts (paginated)
      total     — total matching entry count (for pagination UI)
      page      — current page (1-indexed)
      page_size — entries per page
      pages     — total page count
    """
    page = max(1, page)
    page_size = min(max(1, page_size), 200)

    paths = _get_log_paths(log_type, include_rotations=include_rotations)
    matched: list[dict] = []

    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for raw_line in fh:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        entry = json.loads(raw_line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if _entry_matches_filters(
                        entry,
                        level=level,
                        session_id=session_id,
                        request_id=request_id,
                        database=database,
                        intent=intent,
                        from_ts=from_ts,
                        to_ts=to_ts,
                        search=search,
                    ):
                        matched.append(entry)
        except OSError:
            continue

    # Sort by timestamp descending (newest first), entries without timestamps go last
    def sort_key(e: dict):
        ts = _parse_timestamp(e.get("timestamp"))
        return ts if ts else datetime.min.replace(tzinfo=timezone.utc)

    matched.sort(key=sort_key, reverse=True)

    total = len(matched)
    pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "entries": matched[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def get_log_file_info() -> dict:
    """
    Return metadata about each log file: size in human-readable form and
    last modified timestamp.
    """
    result = {}
    for log_type, path in LOG_FILES.items():
        if os.path.exists(path):
            stat = os.stat(path)
            size_bytes = stat.st_size
            # Human-readable size
            if size_bytes < 1024:
                human = f"{size_bytes} B"
            elif size_bytes < 1024 * 1024:
                human = f"{size_bytes / 1024:.1f} KB"
            else:
                human = f"{size_bytes / (1024 * 1024):.2f} MB"
            last_modified = datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z"
            result[f"{log_type}.log"] = {
                "size_bytes": size_bytes,
                "size_human": human,
                "last_modified": last_modified,
            }
        else:
            result[f"{log_type}.log"] = {
                "size_bytes": 0,
                "size_human": "0 B",
                "last_modified": None,
            }
    return result
