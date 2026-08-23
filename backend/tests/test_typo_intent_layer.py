"""
Phase 1 — Typo Preprocessing Layer Tests (Python Refactor)
============================================================

Unit and integration tests for the new Python-based preprocessor in
backend/agent/typo_intent_layer.py.

Run with:
    cd backend
    pytest tests/test_typo_intent_layer.py -v
"""

import sys
import os
import time
import unittest
from unittest.mock import patch, MagicMock

# Make backend the Python root so imports work
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from agent.typo_intent_layer import (
    process,
    _correct_text,
    _new_request_id,
    _log_prefix,
)


class TestPreprocessorBasic(unittest.TestCase):
    """Verify basic text cleaning functionality."""

    def test_whitespace_normalization(self):
        raw = "  select   *   from    employees   "
        self.assertEqual(_correct_text(raw), "select * from employees")

    def test_punctuation_normalization(self):
        raw = "select * from employees,departments"
        self.assertEqual(_correct_text(raw), "select * from employees, departments")

        raw_multi = "hello!!!!"
        self.assertEqual(_correct_text(raw_multi), "hello!")

    def test_case_preservation(self):
        # Pure SQL-grammar typos — corrected via the static keyword map,
        # independent of any connected schema.
        raw = "CREAT DATABSE"
        self.assertEqual(_correct_text(raw), "CREATE DATABASE")

        raw_title = "Creat Databse"
        self.assertEqual(_correct_text(raw_title), "Create Database")

    @patch("agent.typo_intent_layer._get_system_identifiers")
    def test_case_preservation_for_schema_identifiers(self, mock_ids):
        # "employee" is not a hardcoded keyword — it only corrects when it's
        # a real identifier on the connected schema (schema-driven, not a
        # fixed business-word list).
        mock_ids.return_value = {"employee", "employees"}
        raw = "CREAT EMPLOYE DATABSE"
        self.assertEqual(_correct_text(raw), "CREATE EMPLOYEE DATABASE")


class TestSpellingCorrection(unittest.TestCase):
    """Verify custom database keyword spell checking logic."""

    def test_direct_mapping_typos(self):
        self.assertEqual(_correct_text("creat"), "create")
        self.assertEqual(_correct_text("shw"), "show")
        self.assertEqual(_correct_text("selct"), "select")
        self.assertEqual(_correct_text("frm"), "from")
        self.assertEqual(_correct_text("databse"), "database")
        self.assertEqual(_correct_text("tabels"), "tables")

    def test_keyword_proximity_correction(self):
        # Distance-1 database keywords
        self.assertEqual(_correct_text("databses"), "databases")
        self.assertIn(_correct_text("columnz"), {"column", "columns"})

    def test_fuzzy_matching_with_rapidfuzz(self):
        self.assertEqual(_correct_text("emploooooyee"), "employee")
        self.assertEqual(_correct_text("databseeee"), "database")

    def test_valid_english_words_preserved(self):
        # Words that are valid English shouldn't be matched/corrected incorrectly
        self.assertEqual(_correct_text("all"), "all")
        self.assertEqual(_correct_text("show all employees"), "show all employees")

    @patch("agent.typo_intent_layer._get_system_identifiers")
    def test_correction_generalizes_to_a_non_hr_schema(self, mock_ids):
        # Same code path, a completely different domain (widgets, not HR) —
        # proves typo correction follows the connected schema rather than
        # any fixed business vocabulary.
        mock_ids.return_value = {"widgets", "sku", "restocked_at"}
        self.assertEqual(_correct_text("shw widgets"), "show widgets")
        self.assertEqual(_correct_text("wigdets"), "widgets")


class TestCorrelationAndMetadata(unittest.TestCase):
    """Verify request correlation ID and metadata output keys."""

    def test_request_id_generation(self):
        rid = _new_request_id()
        self.assertEqual(len(rid), 8)
        int(rid, 16)  # verify hex

    def test_prefix_formatting(self):
        rid = _new_request_id()
        self.assertIn("[TYPO_INTENT]", _log_prefix(rid))
        self.assertIn(rid, _log_prefix(rid))

    def test_metadata_keys_present(self):
        cleaned, meta = process("creat databse")
        self.assertEqual(cleaned, "create database")
        self.assertIn("request_id", meta)
        self.assertIn("original_message", meta)
        self.assertIn("cleaned_message", meta)
        self.assertIn("correction_applied", meta)
        self.assertTrue(meta["correction_applied"])
        self.assertEqual(meta["model_name"], "python-preprocessor")
        self.assertFalse(meta["fallback"])
        self.assertIn("timestamp", meta)
        self.assertEqual(meta["libraries_used"], ["pyspellchecker", "rapidfuzz", "regex"])


class TestExceptionHandling(unittest.TestCase):
    """Verify error recovery fallback works cleanly without exceptions."""

    @patch("agent.typo_intent_layer._correct_text")
    def test_graceful_fallback_on_exception(self, mock_correct):
        mock_correct.side_effect = RuntimeError("Generic library crash")
        cleaned, meta = process("hello")
        self.assertEqual(cleaned, "hello")
        self.assertTrue(meta["fallback"])
        self.assertEqual(meta["libraries_used"], ["regex"])
