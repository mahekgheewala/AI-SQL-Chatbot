"""
Tests for agent/clarification_resolver.py — LLM-backed slot extraction used
as a fallback when the fast regex clarification-continuation paths in
agent/semantic_frame.py and agent/pending_resolution.py can't cleanly
resolve a reply.

Mocking convention matches tests/test_local_planner.py: patch
agent.local_planner._call_groq_planner (Groq) and
ai.model_manager.PLANNER_MODEL.generate_content (Gemini fallback) — no live
API keys or network required.
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from agent.clarification_resolver import (
    resolve_create_table_slots,
    resolve_sample_data_request,
    resolve_chart_slots,
    TableColumnsResolution,
    SampleDataResolution,
    ChartSlotsResolution,
    _is_identifier_like_column,
    _ensure_unique_values,
)


def _metadata():
    return {
        "databases": ["hr_database"],
        "active_database": "hr_database",
        "tables": ["employees", "salaries"],
        "all_tables_by_db": {"hr_database": ["employees", "salaries"]},
        "columns_by_table": {
            "employees": ["id", "name", "salary"],
            "salaries": ["department", "salary", "hire_date"],
        },
        "column_types_by_table": {
            "employees": {"id": "integer", "name": "text", "salary": "numeric"},
            "salaries": {"department": "character varying", "salary": "numeric", "hire_date": "date"},
        },
    }


class TestResolveCreateTableSlots(unittest.TestCase):

    @patch("agent.local_planner._call_groq_planner")
    def test_success(self, mock_groq):
        mock_groq.return_value = (
            '{"table_name": "books", "columns": '
            '[{"name": "id", "type": "INTEGER"}, {"name": "name", "type": "TEXT"}], '
            '"confidence": 0.95}',
            0.1,
        )
        result = resolve_create_table_slots(
            original_request="create a table in it",
            missing=["table", "columns"],
            user_reply="name it books, and add two columns: name and id",
            metadata=_metadata(),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.table_name, "books")
        self.assertEqual(len(result.columns), 2)
        names = {c.name for c in result.columns}
        self.assertEqual(names, {"id", "name"})
        # The literal word "name" must survive as a real column here —
        # this is the exact bug this module exists to fix.
        name_col = next(c for c in result.columns if c.name == "name")
        self.assertEqual(name_col.type, "TEXT")

    @patch("agent.local_planner._call_groq_planner")
    def test_invalid_column_name_fails_closed(self, mock_groq):
        mock_groq.return_value = (
            '{"table_name": "books", "columns": [{"name": "select", "type": "TEXT"}], "confidence": 0.9}',
            0.1,
        )
        result = resolve_create_table_slots(
            original_request="create a table", missing=["table", "columns"],
            user_reply="name it books with column select", metadata=_metadata(),
        )
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.reason)

    @patch("agent.local_planner._call_groq_planner")
    def test_invalid_column_type_fails_closed(self, mock_groq):
        mock_groq.return_value = (
            '{"table_name": "books", "columns": [{"name": "id", "type": "NOT_A_REAL_TYPE"}], "confidence": 0.9}',
            0.1,
        )
        result = resolve_create_table_slots(
            original_request="create a table", missing=["table", "columns"],
            user_reply="name it books", metadata=_metadata(),
        )
        self.assertFalse(result.ok)

    @patch("ai.model_manager.PLANNER_MODEL.generate_content")
    @patch("agent.local_planner._call_groq_planner")
    def test_groq_fails_gemini_succeeds(self, mock_groq, mock_gemini):
        mock_groq.side_effect = urllib.error.URLError("Connection refused")
        mock_response = MagicMock()
        mock_response.text = '{"table_name": "books", "columns": null, "confidence": 0.8}'
        mock_gemini.return_value = mock_response

        result = resolve_create_table_slots(
            original_request="create a table", missing=["table"],
            user_reply="books", metadata=_metadata(),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.table_name, "books")

    @patch("ai.model_manager.PLANNER_MODEL", None)
    @patch("agent.local_planner._call_groq_planner")
    def test_both_llms_fail_returns_ok_false_no_exception(self, mock_groq):
        mock_groq.side_effect = urllib.error.URLError("Connection refused")
        result = resolve_create_table_slots(
            original_request="create a table", missing=["table"],
            user_reply="books", metadata=_metadata(),
        )
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.reason)

    @patch("agent.local_planner._call_groq_planner")
    def test_malformed_json_fails_closed(self, mock_groq):
        mock_groq.return_value = ("this is not json at all", 0.1)
        result = resolve_create_table_slots(
            original_request="create a table", missing=["table"],
            user_reply="books", metadata=_metadata(),
        )
        self.assertFalse(result.ok)


class TestResolveSampleDataRequest(unittest.TestCase):

    @patch("agent.local_planner._call_groq_planner")
    def test_success_with_quote_escaping(self, mock_groq):
        mock_groq.return_value = (
            '{"table": "employees", "rows": ['
            '{"id": 1, "name": "O\'Brien", "salary": 90000}, '
            '{"id": 2, "name": "Alice", "salary": 85000}'
            '], "confidence": 0.9}',
            0.1,
        )
        result = resolve_sample_data_request(
            original_request="add sample data", user_reply="employees",
            metadata=_metadata(), table_hint="employees",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.table, "employees")
        self.assertIn("INSERT INTO employees", result.insert_sql)
        # Single quote in the value must be escaped (doubled), not raw.
        self.assertIn("O''Brien", result.insert_sql)
        self.assertNotIn("O'Brien'", result.insert_sql.replace("O''Brien", ""))

    @patch("agent.local_planner._call_groq_planner")
    def test_invented_column_fails_closed(self, mock_groq):
        mock_groq.return_value = (
            '{"table": "employees", "rows": [{"id": 1, "not_a_real_column": "x"}], "confidence": 0.9}',
            0.1,
        )
        result = resolve_sample_data_request(
            original_request="add sample data", user_reply="employees",
            metadata=_metadata(), table_hint="employees",
        )
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.reason)

    def test_ungrounded_table_fails_closed(self):
        result = resolve_sample_data_request(
            original_request="add sample data", user_reply="not_a_real_table",
            metadata=_metadata(), table_hint="not_a_real_table",
        )
        self.assertFalse(result.ok)

    def test_no_table_hint_fails_closed(self):
        result = resolve_sample_data_request(
            original_request="add sample data", user_reply="add sample data",
            metadata=_metadata(), table_hint=None,
        )
        self.assertFalse(result.ok)

    @patch("agent.local_planner._call_groq_planner")
    def test_no_rows_returned_fails_closed(self, mock_groq):
        mock_groq.return_value = ('{"table": "employees", "rows": [], "confidence": 0.5}', 0.1)
        result = resolve_sample_data_request(
            original_request="add sample data", user_reply="employees",
            metadata=_metadata(), table_hint="employees",
        )
        self.assertFalse(result.ok)

    @patch("agent.clarification_resolver._fetch_existing_column_values", return_value=set())
    @patch("agent.local_planner._call_groq_planner")
    def test_duplicate_id_values_within_batch_get_deduplicated(self, mock_groq, mock_fetch):
        # The LLM proposes the SAME id ("B001") for two different rows —
        # this must never reach the final SQL as a literal duplicate.
        mock_groq.return_value = (
            '{"table": "employees", "rows": ['
            '{"id": "B001", "name": "Alice", "salary": 90000}, '
            '{"id": "B001", "name": "Bob", "salary": 85000}'
            '], "confidence": 0.9}',
            0.1,
        )
        result = resolve_sample_data_request(
            original_request="add sample data", user_reply="employees",
            metadata=_metadata(), table_hint="employees",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.insert_sql.count("'B001'"), 1, result.insert_sql)

    @patch("agent.clarification_resolver._fetch_existing_column_values")
    @patch("agent.local_planner._call_groq_planner")
    def test_id_avoids_values_already_in_the_table(self, mock_groq, mock_fetch):
        # A PRIOR "add sample data" batch already used id "B001" — a new
        # batch reusing it must be changed to something that doesn't collide.
        mock_fetch.side_effect = lambda table, column, target_db, limit=500: (
            {"B001"} if column == "id" else set()
        )
        mock_groq.return_value = (
            '{"table": "employees", "rows": [{"id": "B001", "name": "Carl", "salary": 70000}], "confidence": 0.9}',
            0.1,
        )
        result = resolve_sample_data_request(
            original_request="add sample data", user_reply="employees",
            metadata=_metadata(), table_hint="employees",
        )
        self.assertTrue(result.ok)
        self.assertNotIn("'B001'", result.insert_sql)

    def test_is_identifier_like_column(self):
        self.assertTrue(_is_identifier_like_column("id"))
        self.assertTrue(_is_identifier_like_column("ID"))
        self.assertTrue(_is_identifier_like_column("employee_id"))
        self.assertTrue(_is_identifier_like_column("session_uuid"))
        self.assertFalse(_is_identifier_like_column("paid"))
        self.assertFalse(_is_identifier_like_column("valid"))
        self.assertFalse(_is_identifier_like_column("void"))
        self.assertFalse(_is_identifier_like_column("name"))

    def test_ensure_unique_values_numeric(self):
        rows = [{"id": "1"}, {"id": "1"}, {"id": "2"}]
        _ensure_unique_values(rows, "id", existing=set(), is_numeric=True)
        values = [r["id"] for r in rows]
        self.assertEqual(len(values), len(set(values)), values)

    def test_ensure_unique_values_text_avoids_existing(self):
        rows = [{"code": "A1"}, {"code": "A2"}]
        _ensure_unique_values(rows, "code", existing={"A1"}, is_numeric=False)
        values = [r["code"] for r in rows]
        self.assertNotIn("A1", values)
        self.assertEqual(len(values), len(set(values)), values)


class TestResolveChartSlots(unittest.TestCase):

    @patch("agent.local_planner._call_groq_planner")
    def test_success_infers_measure_and_dimension(self, mock_groq):
        mock_groq.return_value = (
            '{"table": "employees", "chart_type": "pie", "measure": "salary", '
            '"dimension": "name", "aggregation": null, "confidence": 0.9}',
            0.1,
        )
        result = resolve_chart_slots(
            original_request="show a pie chart for this table",
            user_reply="id, name, salary",
            metadata=_metadata(),
            table_hint="employees",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.table, "employees")
        self.assertEqual(result.chart_type, "pie")
        self.assertEqual(result.measure, "salary")
        self.assertEqual(result.dimension, "name")

    @patch("agent.local_planner._call_groq_planner")
    def test_no_measure_fails_closed(self, mock_groq):
        mock_groq.return_value = (
            '{"table": "employees", "chart_type": "pie", "measure": null, '
            '"dimension": "name", "aggregation": null, "confidence": 0.4}',
            0.1,
        )
        result = resolve_chart_slots(
            original_request="show a chart", user_reply="hmm not sure",
            metadata=_metadata(), table_hint="employees",
        )
        self.assertFalse(result.ok)

    @patch("agent.local_planner._call_groq_planner")
    def test_invented_column_ignored_fails_closed(self, mock_groq):
        mock_groq.return_value = (
            '{"table": "employees", "chart_type": "bar", "measure": "not_a_real_column", '
            '"dimension": null, "aggregation": null, "confidence": 0.5}',
            0.1,
        )
        result = resolve_chart_slots(
            original_request="show a chart", user_reply="not_a_real_column",
            metadata=_metadata(), table_hint="employees",
        )
        self.assertFalse(result.ok)

    def test_invalid_table_hint_fails_closed(self):
        result = resolve_chart_slots(
            original_request="show a chart", user_reply="employees",
            metadata=_metadata(), table_hint="not_a_real_table",
        )
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
