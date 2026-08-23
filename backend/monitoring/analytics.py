"""
Phase 9.5 — Analytics
======================
Computes aggregated metrics from log files on-demand.
No persistence — all computations are derived from the log files at query time.
Results are computed in a background thread to avoid blocking the event loop.

Metrics provided:
  - Total requests (HTTP_REQUEST entries in app.log)
  - SQL queries executed
  - Gemini call counts by type (from gemini_metrics in-memory)
  - Average response latency (ms)
  - Token usage (prompt / response)
  - Estimated API cost (USD)
  - Top intents (top 10 by frequency)
  - Top databases (top 10 by frequency)
  - Error count and error rate percentage
"""

import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from monitoring.log_reader import read_logs
from agent.gemini_metrics import get_metrics as get_gemini_metrics


def _compute_analytics(
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
) -> dict:
    """
    Parse app.log and error.log to derive analytics for the given time window.
    Called in a thread via asyncio.to_thread to avoid blocking the event loop.
    """
    if to_ts is None:
        to_ts = datetime.now(timezone.utc)
    if from_ts is None:
        from_ts = to_ts - timedelta(hours=24)

    # Fetch all app log entries in the time range (no pagination - get all)
    app_result = read_logs(
        "app",
        from_ts=from_ts,
        to_ts=to_ts,
        page=1,
        page_size=200,
    )
    # Fetch all entries by reading up to 10,000 entries for analytics
    all_app_entries = []
    page = 1
    while True:
        batch = read_logs("app", from_ts=from_ts, to_ts=to_ts, page=page, page_size=200)
        all_app_entries.extend(batch["entries"])
        if page >= batch["pages"] or page >= 50:  # Safety cap at 10,000
            break
        page += 1

    error_result_page1 = read_logs("error", from_ts=from_ts, to_ts=to_ts, page=1, page_size=200)
    all_error_entries = error_result_page1["entries"]
    # Collect more error pages if needed
    for ep in range(2, error_result_page1["pages"] + 1):
        if ep > 50:
            break
        all_error_entries.extend(
            read_logs("error", from_ts=from_ts, to_ts=to_ts, page=ep, page_size=200)["entries"]
        )

    # Counters
    total_requests = 0
    sql_queries = 0
    response_times: list[float] = []
    sql_times: list[float] = []
    ai_times: list[float] = []
    intent_counter: Counter = Counter()
    database_counter: Counter = Counter()
    prompt_tokens = 0
    response_tokens = 0
    estimated_cost = 0.0

    for entry in all_app_entries:
        op = entry.get("operation_type", "")

        if op == "HTTP_REQUEST":
            total_requests += 1

        elif op == "HTTP_RESPONSE":
            ms = entry.get("execution_time_ms")
            if isinstance(ms, (int, float)):
                response_times.append(float(ms))

        elif op == "SQL_EXECUTED":
            sql_queries += 1
            ms = entry.get("execution_time_ms")
            if isinstance(ms, (int, float)):
                sql_times.append(float(ms))

        elif op == "GEMINI_CALL":
            ms = entry.get("execution_time_ms")
            if isinstance(ms, (int, float)):
                ai_times.append(float(ms))
            pt = entry.get("prompt_tokens", 0) or 0
            rt = entry.get("response_tokens", 0) or 0
            cost = entry.get("estimated_cost", 0.0) or 0.0
            prompt_tokens += int(pt)
            response_tokens += int(rt)
            estimated_cost += float(cost)

        # Count intents
        intent = entry.get("intent")
        if intent and intent not in ("UNKNOWN", "NEEDS_CLARIFICATION", None):
            intent_counter[intent] += 1

        # Count database usage
        db = entry.get("database_name")
        if db:
            database_counter[db] += 1

    error_count = len(all_error_entries)
    error_rate_pct = round(error_count / total_requests * 100, 1) if total_requests > 0 else 0.0

    # Gemini live metrics from in-memory store
    gemini_live = get_gemini_metrics()
    gemini_calls_live = gemini_live.get("calls", {})

    return {
        "total_requests": total_requests,
        "sql_queries": sql_queries,
        "gemini_calls": {
            "total": gemini_calls_live.get("total", 0),
            "router": gemini_calls_live.get("router", 0),
            "planner": gemini_calls_live.get("planner", 0),
            "sql_generator": gemini_calls_live.get("sql_generator", 0),
            "summarizer": gemini_calls_live.get("summarizer", 0),
            "report_formatter": gemini_calls_live.get("report_formatter", 0),
        },
        "avg_response_ms": round(sum(response_times) / len(response_times), 1) if response_times else 0.0,
        "avg_sql_ms": round(sum(sql_times) / len(sql_times), 1) if sql_times else 0.0,
        "avg_ai_ms": round(sum(ai_times) / len(ai_times), 1) if ai_times else 0.0,
        "token_usage": {
            "prompt": prompt_tokens,
            "response": response_tokens,
            "total": prompt_tokens + response_tokens,
        },
        "estimated_cost_usd": round(estimated_cost, 6),
        "top_intents": intent_counter.most_common(10),
        "top_databases": database_counter.most_common(10),
        "error_count": error_count,
        "error_rate_pct": error_rate_pct,
    }


async def get_analytics(
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
) -> dict:
    """
    Async wrapper — runs log parsing in a thread pool to avoid blocking
    the FastAPI event loop during disk I/O.
    """
    return await asyncio.to_thread(_compute_analytics, from_ts, to_ts)
