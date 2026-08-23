"""
Phase 9.5 — Log Exporter
=========================
Streams filtered log entries as a downloadable file (JSON or CSV).

Uses FastAPI's StreamingResponse so large exports never load the entire
file into memory at once. The caller passes the same filters as the
Log Explorer — only matching entries are included in the export.
"""

import csv
import io
import json
from datetime import datetime
from typing import Generator, Optional

from monitoring.log_reader import _get_log_paths, _entry_matches_filters, _parse_timestamp


# Flattened column names written to CSV exports
_CSV_COLUMNS = [
    "timestamp", "level", "logger", "message",
    "request_id", "session_id", "user_id",
    "database_name", "selected_table",
    "operation_type", "execution_time_ms",
    "intent", "error_code",
]


def _iter_matching_entries(
    log_type: str,
    *,
    level: Optional[str] = None,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    database: Optional[str] = None,
    intent: Optional[str] = None,
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
    search: Optional[str] = None,
) -> Generator[dict, None, None]:
    """
    Lazily yield matching log entries from the specified log file.
    Reads line-by-line so memory usage stays constant regardless of file size.
    """
    paths = _get_log_paths(log_type, include_rotations=False)
    for path in paths:
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
                        yield entry
        except OSError:
            continue


def stream_json(
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
) -> Generator[str, None, None]:
    """
    Yield chunks of a JSON array of matching log entries.
    Produces valid JSON: [ {...}, {...}, ... ]
    """
    first = True
    yield "[\n"
    for entry in _iter_matching_entries(
        log_type,
        level=level,
        session_id=session_id,
        request_id=request_id,
        database=database,
        intent=intent,
        from_ts=from_ts,
        to_ts=to_ts,
        search=search,
    ):
        if not first:
            yield ",\n"
        yield json.dumps(entry, ensure_ascii=False)
        first = False
    yield "\n]"


def stream_csv(
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
) -> Generator[str, None, None]:
    """
    Yield rows of a CSV file of matching log entries.
    Produces a header row followed by one data row per entry.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=_CSV_COLUMNS,
        extrasaction="ignore",
        lineterminator="\n",
    )
    # Write header
    writer.writeheader()
    yield buf.getvalue()

    for entry in _iter_matching_entries(
        log_type,
        level=level,
        session_id=session_id,
        request_id=request_id,
        database=database,
        intent=intent,
        from_ts=from_ts,
        to_ts=to_ts,
        search=search,
    ):
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=_CSV_COLUMNS,
            extrasaction="ignore",
            lineterminator="\n",
        )
        # Fill missing keys with empty string for CSV safety
        row = {col: entry.get(col, "") for col in _CSV_COLUMNS}
        writer.writerow(row)
        yield buf.getvalue()
