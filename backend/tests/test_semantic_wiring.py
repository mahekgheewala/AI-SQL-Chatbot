"""SemanticFrame wiring into the gateway + coordinator.

The gateway (agent.universal_gateway) runs interpret_message() exactly once
and grounds the frame; the coordinator (agent.agent_coordinator) only maps an
already-grounded, already-successful frame to a tool call — it never
re-parses or re-grounds. This file tests both halves of that seam:
  - agent.semantic_frame's gateway-integration helpers (metadata shaping, SQL
    construction, instruction reconstruction) directly.
  - agent.agent_coordinator._tool_from_frame's tool-selection mapping, given
    a frame produced by interpret_message().

Clarification-generation itself (does this message resolve or ask a
question) is agent.semantic_frame's job and is covered exhaustively in
test_semantic_frame.py — not duplicated here.

Run with:
    pytest backend/tests/test_semantic_wiring.py -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from agent.agent_coordinator import _tool_from_frame, _SEMANTIC_TOOL_MAP
from agent.semantic_frame import (
    interpret_message, build_grounding_metadata, frame_to_query_intent,
    instruction_for_frame,
)
from agent.deterministic_sql_builder import build_sql
from models.schemas import SemanticFrame, QueryAggregation, QueryFilter, ColumnSpec, AlterSpec
from utils.logging_config import user_id_var
from state import metadata_store

USER = "wiring-test"


def _seed_metadata():
    store = metadata_store._default_store()
    store.update({
        "databases": ["hr_database", "another"],
        "selected_db": "hr_database",
        "schema": {
            "employees": ["id", "name", "department", "salary", "hire_date"],
            "departments": ["id", "name"],
        },
        "tables": ["employees", "departments"],
        "table_schemas": {
            "employees": {"id": "integer", "name": "text", "department": "text",
                          "salary": "numeric", "hire_date": "date"},
            "departments": {"id": "integer", "name": "text"},
        },
        "routing_summaries": {
            "hr_database": ["employees", "departments"],
            "another": ["students"],
        },
    })
    metadata_store._user_stores[USER] = store


@pytest.fixture(autouse=True)
def _wiring_env():
    user_id_var.set(USER)
    _seed_metadata()
    yield
    metadata_store._user_stores[USER] = metadata_store._default_store()
    user_id_var.set(None)


def ground(message, session=None, active_db="hr_database"):
    """Mirror what universal_gateway.process_universal_semantic_gateway() does:
    build grounding metadata from the live store and interpret the message."""
    meta = build_grounding_metadata(metadata_store.get_metadata(), active_db)
    context = {
        "pending_frame": (session or {}).get("semantic_pending_frame"),
        "prior_frames": (session or {}).get("semantic_prior_frames") or [],
        "active_database": active_db,
    }
    return interpret_message(message, meta, context)


def dispatch(message, pipeline_hint=None, session=None):
    """End-to-end: ground the message, then let the coordinator pick a tool."""
    result = ground(message, session=session)
    return _tool_from_frame(result.frame, pipeline_hint, message)


# ─── Tool map & metadata conversion ─────────────────────────────────────────

def test_tool_map_covers_semantic_capabilities():
    assert _SEMANTIC_TOOL_MAP["retrieve"] == "execute_sql"
    assert _SEMANTIC_TOOL_MAP["create_database"] == "create_database"
    assert _SEMANTIC_TOOL_MAP["list_tables"] == "list_tables"
    assert _SEMANTIC_TOOL_MAP["describe_table"] == "get_schema_info"
    assert _SEMANTIC_TOOL_MAP["switch_database"] == "switch_database"
    assert _SEMANTIC_TOOL_MAP["add_column"] == "alter_table"


def test_grounding_metadata_shape():
    meta = build_grounding_metadata(metadata_store.get_metadata(), "hr_database")
    assert meta["tables"] == ["employees", "departments"]
    assert meta["columns_by_table"]["employees"] == [
        "id", "name", "department", "salary", "hire_date",
    ]
    assert meta["active_database"] == "hr_database"
    assert meta["databases"] == ["hr_database", "another"]
    assert meta["column_types_by_table"]["employees"]["salary"] == "numeric"


# ─── SQL construction helpers ───────────────────────────────────────────────

def test_frame_to_query_intent_plain():
    frame = SemanticFrame(action="select", object_type="DATA", capability_id="retrieve",
                          table="employees", limit=5)
    assert build_sql(frame_to_query_intent(frame)) == "SELECT * FROM employees LIMIT 5;"


def test_frame_to_query_intent_filter():
    frame = SemanticFrame(action="select", object_type="DATA", capability_id="retrieve",
                          table="employees",
                          filters=[QueryFilter(column="department", operator="=",
                                               value="engineering")])
    assert build_sql(frame_to_query_intent(frame)) == (
        "SELECT * FROM employees WHERE department = 'engineering';"
    )


def test_frame_to_query_intent_columns():
    frame = SemanticFrame(action="select", object_type="DATA", capability_id="retrieve",
                          table="employees",
                          columns=[ColumnSpec(name="name"), ColumnSpec(name="salary")])
    assert build_sql(frame_to_query_intent(frame)) == "SELECT name, salary FROM employees;"


def test_frame_to_query_intent_includes_aggregation():
    # Unlike the old coordinator-local builder, aggregations/group-by ARE
    # included — deterministic_sql_builder already supports them, so there's
    # no reason to force these to the LLM planner.
    frame = SemanticFrame(action="select", object_type="DATA", capability_id="retrieve",
                          table="employees",
                          aggregations=[QueryAggregation(function="AVG", column="salary")])
    assert build_sql(frame_to_query_intent(frame)) == "SELECT AVG(salary) FROM employees;"


def test_frame_to_query_intent_none_without_table():
    frame = SemanticFrame(action="select", object_type="DATA", capability_id="retrieve")
    assert frame_to_query_intent(frame) is None


# ─── Instruction reconstruction ─────────────────────────────────────────────

def test_instruction_for_create_table():
    frame = SemanticFrame(action="create", object_type="TABLE", capability_id="create_table",
                          table="sales_orders",
                          columns=[ColumnSpec(name="id", type="INTEGER"),
                                   ColumnSpec(name="name", type="TEXT")])
    assert instruction_for_frame(frame, "user instruction") == (
        "CREATE TABLE sales_orders (id INTEGER, name TEXT)"
    )


def test_instruction_for_drop_table():
    frame = SemanticFrame(action="drop", object_type="TABLE", capability_id="drop_table",
                          table="employees")
    assert instruction_for_frame(frame, "user instruction") == "DROP TABLE employees"


def test_instruction_for_alter():
    add = SemanticFrame(action="alter", object_type="TABLE", capability_id="add_column",
                        table="employees",
                        alter=AlterSpec(operation="ADD_COLUMN", column="email"))
    assert instruction_for_frame(add, "add a column") == "ALTER TABLE employees ADD COLUMN email"

    drop = SemanticFrame(action="alter", object_type="TABLE", capability_id="drop_column",
                         table="employees",
                         alter=AlterSpec(operation="DROP_COLUMN", column="email"))
    assert instruction_for_frame(drop, "drop a column") == "ALTER TABLE employees DROP COLUMN email"


# ─── Coordinator dispatch: already-grounded frame -> tool ───────────────────

def test_retrieve_routes_to_execute_sql():
    r = dispatch("show me all employees")
    assert r["tool"] == "execute_sql"
    assert r["tool_input"] == "SELECT * FROM employees;"
    assert r["legacy_intent"] == "QUERY"


def test_retrieve_filter_routes_to_execute_sql():
    r = dispatch("show me all employees where department = engineering")
    assert r["tool"] == "execute_sql"
    assert r["tool_input"] == (
        "SELECT * FROM employees WHERE department = 'engineering';"
    )


def test_create_database_routes():
    r = dispatch("create a database called sales")
    assert r["tool"] == "create_database"
    assert r["tool_input"] == "CREATE DATABASE sales"


def test_list_tables_routes():
    r = dispatch("show tables")
    assert r["tool"] == "list_tables"


def test_describe_table_routes():
    r = dispatch("describe employees")
    assert r["tool"] == "get_schema_info"
    assert r["tool_input"] == "describe table employees"


def test_switch_database_routes():
    r = dispatch("switch to another")
    assert r["tool"] == "switch_database"
    assert r["tool_input"] == "another"


def test_use_database_routes():
    # "use X" — a database-switch phrasing the frame must recognize (it did
    # not, until a regression test caught the gap during pipeline consolidation).
    r = dispatch("use another")
    assert r["tool"] == "switch_database"
    assert r["tool_input"] == "another"


# ─── Frame not (yet) success: coordinator never invents a tool ─────────────

def test_clarifying_frame_yields_no_tool():
    result = ground("drop table nonexistent_tbl")
    assert result.status != "SUCCESS"
    r = _tool_from_frame(result.frame, None, "drop table nonexistent_tbl")
    assert r["tool"] is None


def test_aggregation_and_group_by_are_deterministically_executable():
    # Both supported by deterministic_sql_builder — routes DIRECT, not to the planner.
    msg = "average salary grouped by department"
    result = ground(msg)
    assert result.status == "SUCCESS"
    r = _tool_from_frame(result.frame, None, msg)
    assert r["tool"] == "execute_sql"
    assert "AVG(salary)" in r["tool_input"]
    assert "GROUP BY department" in r["tool_input"]


def test_aggregation_without_explicit_table_infers_it_from_column():
    # "salary" only exists on employees — no table named directly.
    msg = "average salary"
    result = ground(msg)
    assert result.status == "SUCCESS"
    assert result.frame.table == "employees"


# ─── Fall-through guarantees ────────────────────────────────────────────────

def test_visualize_capability_has_no_direct_tool():
    result = ground("plot salary by department")
    assert result.frame.capability_id == "visualize"
    r = _tool_from_frame(result.frame, None, "plot salary by department")
    assert r["tool"] is None


def test_visualization_pipeline_hint_suppresses_dispatch():
    result = ground("show me all employees")
    r = _tool_from_frame(result.frame, "VISUALIZATION", "show me all employees")
    assert r["tool"] is None


def test_analytics_pipeline_hint_suppresses_dispatch():
    result = ground("show me all employees")
    r = _tool_from_frame(result.frame, "ANALYTICS_ENGINE", "show me all employees")
    assert r["tool"] is None


def test_none_frame_yields_no_tool():
    assert _tool_from_frame(None, None, "anything")["tool"] is None
