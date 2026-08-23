"""
Visualization Engine — Live Integration Tests (Phase 10.4)

Verifies:
  1. Sending queries via FastAPI chat_endpoint returns valid ChatResponse
  2. The `visualization` field is correctly populated with the Pydantic schema
  3. Decoupled cache works (cache HIT is logged correctly)
  4. Explicit and auto-selection modes function
  5. Column inference and priority selection rules are honored
"""

import sys
import os
import asyncio
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from routers.chat import chat_endpoint
from models.schemas import ChatRequest, ChatResponse
from state.metadata_store import get_metadata, sync_database_context
from db.executor import execute_sql

SEPARATOR = "=" * 50

async def test_live_visualization():
    print(f"\n{SEPARATOR}")
    print("  LIVE VISUALIZATION INTEGRATION TESTS")
    print(SEPARATOR)

    session_id = "test-live-viz-session"
    db_name = "hr_database"

    # Ensure context is synced
    sync_database_context(db_name)

    # 1. Setup clean table
    print("\n[Setup] Recreating test table 'salaries' and 'employees'...")
    execute_sql("DROP TABLE IF EXISTS salaries CASCADE;", db_name, intent="ADMIN")
    execute_sql("CREATE TABLE salaries (department VARCHAR(50), salary NUMERIC, hire_date DATE);", db_name, intent="ADMIN")
    execute_sql("INSERT INTO salaries VALUES ('Engineering', 120000, '2021-01-01');", db_name, intent="ADMIN")
    execute_sql("INSERT INTO salaries VALUES ('Engineering', 130000, '2021-02-01');", db_name, intent="ADMIN")
    execute_sql("INSERT INTO salaries VALUES ('Sales', 90000, '2021-03-01');", db_name, intent="ADMIN")
    execute_sql("INSERT INTO salaries VALUES ('Sales', 95000, '2021-04-01');", db_name, intent="ADMIN")
    execute_sql("INSERT INTO salaries VALUES ('Marketing', 85000, '2021-05-01');", db_name, intent="ADMIN")

    execute_sql("DROP TABLE IF EXISTS employees CASCADE;", db_name, intent="ADMIN")
    execute_sql("CREATE TABLE employees (id SERIAL, name VARCHAR(100), salary NUMERIC);", db_name, intent="ADMIN")
    execute_sql("INSERT INTO employees (name, salary) VALUES ('Alice', 100000);", db_name, intent="ADMIN")
    execute_sql("INSERT INTO employees (name, salary) VALUES ('Bob', 120000);", db_name, intent="ADMIN")
    execute_sql("INSERT INTO employees (name, salary) VALUES ('Charlie', 110000);", db_name, intent="ADMIN")
    execute_sql("INSERT INTO employees (name, salary) VALUES ('David', 95000);", db_name, intent="ADMIN")
    execute_sql("INSERT INTO employees (name, salary) VALUES ('Eva', 105000);", db_name, intent="ADMIN")
    execute_sql("INSERT INTO employees (name, salary) VALUES ('Frank', 115000);", db_name, intent="ADMIN")
    execute_sql("INSERT INTO employees (name, salary) VALUES ('Grace', 125000);", db_name, intent="ADMIN")

    # Refresh routing summaries and force schema cache reload
    from state.metadata_store import refresh_routing_summaries, _store
    refresh_routing_summaries()
    _store["cached_schema_db"] = None
    sync_database_context(db_name)

    # 2. Test Auto-Selection (Categorical + Numeric -> BAR / PIE / HORIZONTAL_BAR)
    print("\n[Test 1] Requesting: 'plot salary by department'...")
    req1 = ChatRequest(message="plot salary by department", session_id=session_id)
    res1: ChatResponse = await chat_endpoint(req1)

    assert res1.visualization is not None, "FAIL: visualization payload is missing"
    assert res1.visualization.chart.chart_type in ("BAR", "PIE", "HORIZONTAL_BAR"), f"Unexpected type: {res1.visualization.chart.chart_type}"
    assert res1.visualization.metadata.cache_status == "MISS", "Expected Cache MISS on first render"
    print(f"  PASS: Visualization generated successfully ({res1.visualization.chart.chart_type}, cache={res1.visualization.metadata.cache_status})")

    # 3. Test Cache HIT
    print("\n[Test 2] Requesting duplicate: 'plot salary by department'...")
    req2 = ChatRequest(message="plot salary by department", session_id=session_id)
    res2: ChatResponse = await chat_endpoint(req2)

    assert res2.visualization is not None, "FAIL: visualization payload is missing on cache hit"
    assert res2.visualization.metadata.cache_status == "HIT", f"Expected Cache HIT, got {res2.visualization.metadata.cache_status}"
    print(f"  PASS: Decoupled cache returned HIT successfully")

    # 4. Test Manual Override
    print("\n[Test 3] Requesting manual override: 'pie chart of salary by department'...")
    req3 = ChatRequest(message="pie chart of salary by department", session_id=session_id)
    res3: ChatResponse = await chat_endpoint(req3)

    assert res3.visualization is not None
    assert res3.visualization.chart.chart_type == "PIE"
    assert res3.visualization.metadata.selection_mode == "MANUAL"
    print(f"  PASS: Explicit manual override chart type honored (PIE)")

    # 5. Test Column Inference for single measure 'plot employee salaries'
    print("\n[Test 4] Requesting: 'plot employee salaries'...")
    req4 = ChatRequest(message="plot employee salaries", session_id=session_id)
    res4: ChatResponse = await chat_endpoint(req4)

    assert res4.visualization is not None, "FAIL: visualization payload is missing on inferred query"
    assert res4.visualization.chart.chart_type == "BAR", f"Expected BAR chart, got {res4.visualization.chart.chart_type}"
    assert "Identifier column + Numeric measure detected" in res4.visualization.metadata.selection_reason, f"Unexpected selection reason: {res4.visualization.metadata.selection_reason}"
    print(f"  PASS: Column inference successfully paired measure with category name and selected BAR chart")

    # 6. Test Histogram fallbacks (drop salaries table first to avoid ambiguity)
    print("\n[Setup Test 5] Dropping table salaries to test single-table fallback...")
    execute_sql("DROP TABLE salaries CASCADE;", db_name, intent="ADMIN")
    _store["cached_schema_db"] = None
    sync_database_context(db_name)

    print("\n[Test 5] Requesting: 'plot salary distribution'...")
    req5 = ChatRequest(message="plot salary distribution", session_id=session_id)
    res5: ChatResponse = await chat_endpoint(req5)

    assert res5.visualization is not None
    assert res5.visualization.chart.chart_type == "HISTOGRAM", f"Expected HISTOGRAM, got {res5.visualization.chart.chart_type}"
    print(f"  PASS: Explicit distribution intent correctly mapped to HISTOGRAM")

    # 7. Clean up
    print("\n[Cleanup] Dropping test tables...")
    execute_sql("DROP TABLE IF EXISTS salaries CASCADE;", db_name, intent="ADMIN")
    execute_sql("DROP TABLE IF EXISTS employees CASCADE;", db_name, intent="ADMIN")

    print(f"\n{SEPARATOR}")
    print("  ALL LIVE VISUALIZATION INTEGRATION TESTS PASSED ✓")
    print(SEPARATOR + "\n")


if __name__ == "__main__":
    asyncio.run(test_live_visualization())
