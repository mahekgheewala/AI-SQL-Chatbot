"""
Tests for the LLM-fallback wiring added to agent/pending_resolution.py.

Covers: CREATE_TABLE_COLUMNS falling back to the LLM resolver when the
regex column-definition parser can't parse a reply, add_sample_data routing
through the LLM resolver instead of naive string concatenation, and every
other capability's table-resolution reconstruction staying untouched.
"""

import os
import sys
from unittest.mock import patch, MagicMock

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from agent.pending_resolution import resolve_pending_clarification
from agent.clarification_resolver import TableColumnsResolution, SampleDataResolution


def _fake_metadata_store(monkeypatch):
    """metadata_store.get_metadata() is called lazily inside the resolver
    wiring — stub it so these tests don't need a live DB connection."""
    monkeypatch.setattr(
        "state.metadata_store.get_metadata",
        lambda: {
            "tables": ["employees"],
            "schema": {"employees": ["id", "name", "salary"]},
            "table_schemas": {"employees": {"id": "integer", "name": "text", "salary": "numeric"}},
        },
    )


def test_create_table_columns_falls_back_to_llm_on_invalid_syntax(monkeypatch):
    _fake_metadata_store(monkeypatch)
    pending = {
        "type": "CREATE_TABLE_COLUMNS",
        "table_name": "books",
        "target_db": "hr_database",
        "original_request": "create a table in it",
    }
    with patch(
        "agent.clarification_resolver.resolve_create_table_slots"
    ) as mock_resolve:
        mock_resolve.return_value = TableColumnsResolution(
            ok=True,
            columns=[__import__("models.schemas", fromlist=["ColumnSpec"]).ColumnSpec(name="id", type="INTEGER"),
                     __import__("models.schemas", fromlist=["ColumnSpec"]).ColumnSpec(name="name", type="TEXT")],
        )
        result = resolve_pending_clarification("id and name please", pending)

    assert result["resolved"] is True
    assert "CREATE TABLE books" in result["reconstructed_request"]
    assert "id INTEGER" in result["reconstructed_request"]
    assert "name TEXT" in result["reconstructed_request"]


def test_create_table_columns_stays_unresolved_when_llm_also_fails(monkeypatch):
    _fake_metadata_store(monkeypatch)
    pending = {
        "type": "CREATE_TABLE_COLUMNS",
        "table_name": "books",
        "target_db": "hr_database",
        "original_request": "create a table in it",
    }
    with patch("agent.clarification_resolver.resolve_create_table_slots") as mock_resolve:
        mock_resolve.return_value = TableColumnsResolution(ok=False, reason="LLM call failed")
        result = resolve_pending_clarification("gibberish reply", pending)

    assert result["resolved"] is False


def test_add_sample_data_routes_through_resolver_not_concat(monkeypatch):
    _fake_metadata_store(monkeypatch)
    pending = {
        "type": "MISSING_TABLE",
        "options": ["employees"],
        "target_db": "hr_database",
        "original_request": "add sample data in it",
        "capability_id": "add_sample_data",
    }
    with patch("agent.clarification_resolver.resolve_sample_data_request") as mock_resolve:
        mock_resolve.return_value = SampleDataResolution(
            ok=True, table="employees",
            insert_sql="INSERT INTO employees (id, name) VALUES (1, 'Alice');",
        )
        result = resolve_pending_clarification("employees", pending)

    assert result["resolved"] is True
    # Must be the resolver's real INSERT SQL, never the naive
    # "<original request> <selected option>" concatenation.
    assert result["reconstructed_request"] == "INSERT INTO employees (id, name) VALUES (1, 'Alice');"
    assert "add sample data in it employees" not in result["reconstructed_request"]


def test_add_sample_data_fails_closed_when_resolver_fails(monkeypatch):
    _fake_metadata_store(monkeypatch)
    pending = {
        "type": "MISSING_TABLE",
        "options": ["employees"],
        "target_db": "hr_database",
        "original_request": "add sample data in it",
        "capability_id": "add_sample_data",
    }
    with patch("agent.clarification_resolver.resolve_sample_data_request") as mock_resolve:
        mock_resolve.return_value = SampleDataResolution(ok=False, reason="table not grounded")
        result = resolve_pending_clarification("employees", pending)

    assert result["resolved"] is False
    # Must not fall back to naive concatenation for this capability.
    assert result["reconstructed_request"] is None


def test_other_capability_table_resolution_unaffected():
    """A retrieve/no-capability_id pending clarification must keep using the
    existing naive-concat reconstruction — this wiring only special-cases
    add_sample_data."""
    pending = {
        "type": "MISSING_TABLE",
        "options": ["employees", "salaries"],
        "target_db": "hr_database",
        "original_request": "show me everything",
    }
    result = resolve_pending_clarification("employees", pending)
    assert result["resolved"] is True
    assert result["reconstructed_request"] == "show me everything employees"
