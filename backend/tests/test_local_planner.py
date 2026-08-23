"""
Phase 2 — Local Planner Tests  (Planner v1.0.0 — Refinements 1–6)
===================================================================

Unit and integration tests for backend/agent/local_planner.py.

Refinements verified:
  R1  — LOCAL_PLANNER_MODEL independent of TYPO_INTENT_MODEL
  R2  — PLANNER_VERSION = 1.0.0 (Planner tracks its own evolution)
  R3  — Invalid planning_mode triggers WARNING + fallback (not silent normalization)
  R4  — planner_confidence field (0.0–1.0, clamped)
  R5  — estimated_execution_type field (6 valid values)
  R6  — Docstring clarifies Planner → Planning Document → Gemini Executor flow

Run with:
    cd backend
    pytest tests/test_local_planner.py -v
"""

import json
import sys
import os
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

# Setup path
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from agent.local_planner import (
    plan,
    _build_planner_prompt,
    _parse_planner_response,
    _call_groq_planner,
    _get_fallback_plan,
    SCHEMA_VERSION,
    PLANNER_VERSION,
    REQUIRED_PLANNING_DOC_KEYS,
    VALID_PLANNING_MODES,
    VALID_EXECUTION_TYPES,
)


class TestLocalPlannerUnit(unittest.TestCase):

    def test_build_planner_prompt(self):
        prompt = _build_planner_prompt(
            user_message="Find active invoices",
            intent_meta={"detected_intent": "Database Query", "confidence": 1.0},
            schema_context="ACTIVE DATABASE: sales_db",
            history=[{"role": "user", "text": "hello"}]
        )
        self.assertIn("Find active invoices", prompt)
        self.assertIn("sales_db", prompt)
        self.assertIn("Database Query", prompt)
        self.assertIn("/no_think", prompt)
        # Three-stage structure
        self.assertIn("STAGE 1: REPOSITORY ANALYSIS", prompt)
        self.assertIn("STAGE 2: DATABASE CONTEXT ANALYSIS", prompt)
        self.assertIn("STAGE 3: PLAN GENERATION", prompt)
        self.assertIn("DOMAIN SEPARATION RULE", prompt)
        self.assertIn("CLARIFICATION DECISION MATRIX", prompt)
        # All 6 planning modes declared
        for mode in VALID_PLANNING_MODES:
            self.assertIn(mode, prompt)
        # R4/R5: new fields in prompt
        self.assertIn("planner_confidence", prompt)
        self.assertIn("estimated_execution_type", prompt)
        self.assertIn("ESTIMATED EXECUTION TYPE", prompt)
        self.assertIn("PLANNER CONFIDENCE", prompt)

    # ---------------------------------------------------------------------------
    # Helper: build a minimal valid planning_document dict
    # ---------------------------------------------------------------------------
    @staticmethod
    def _make_raw(_context_mode, **overrides):
        """
        Build a minimal valid JSON string accepted by _parse_planner_response.

        _context_mode is used ONLY for setting default context blocks:
          'Database Planning' -> base_db_ctx is set
          'Codebase Planning' -> base_repo_ctx is set
          anything else       -> both are None

        Use overrides to set any planning_document field, including planning_mode.
        """
        base_db_ctx = {
            "target_database": "test_db",
            "required_schema_objects": [],
            "required_metadata_elements": []
        } if _context_mode == "Database Planning" else None
        base_repo_ctx = {
            "involved_modules": ["backend/agent/tools.py"],
            "architectural_layers": ["Agent"]
        } if _context_mode == "Codebase Planning" else None
        doc = {
            "planning_mode": _context_mode,
            "user_intent": "Database Query",
            "planning_required": True,
            "complexity": "Medium",
            "goal": "Test goal",
            "planner_confidence": 0.9,
            "estimated_execution_type": "sql_query",
            "repository_context": base_repo_ctx,
            "database_context": base_db_ctx,
            "execution_steps": ["Step 1"],
            "warnings": [],
            "assumptions": [],
            "clarification_required": False,
            "clarification_question": None,
        }
        doc.update(overrides)
        return json.dumps({
            "schema_version": "1.0",
            "planner_metadata": {},
            "planning_document": doc
        })

    def test_parse_planner_response_clean(self):
        raw = self._make_raw("Database Planning")
        parsed = _parse_planner_response(raw)
        self.assertEqual(parsed["schema_version"], "1.0")
        pdoc = parsed["planning_document"]
        self.assertTrue(pdoc["planning_required"])
        self.assertEqual(pdoc["complexity"], "Medium")
        self.assertEqual(pdoc["planning_mode"], "Database Planning")
        # R4/R5 new fields present
        self.assertIn("planner_confidence", pdoc)
        self.assertIn("estimated_execution_type", pdoc)

    def test_parse_planner_response_with_think_and_markdown(self):
        inner = self._make_raw("General Reasoning", planning_required=False,
                               complexity="Low", goal="Simple test",
                               estimated_execution_type="general_reasoning")
        raw = f"""<think>
        some chain of thought reasoning
        </think>
        ```json
        {inner}
        ```"""
        parsed = _parse_planner_response(raw)
        self.assertEqual(parsed["schema_version"], "1.0")
        self.assertFalse(parsed["planning_document"]["planning_required"])
        self.assertEqual(parsed["planning_document"]["planning_mode"], "General Reasoning")

    def test_parse_planner_response_missing_keys(self):
        raw = '{"goal": "invalid plan"}'
        with self.assertRaises(ValueError):
            _parse_planner_response(raw)

    def test_get_fallback_plan(self):
        f = _get_fallback_plan("Test error message", "rid-123", "timestamp-123", 45.2)
        self.assertEqual(f["schema_version"], "1.0")
        self.assertEqual(f["planner_metadata"]["planning_duration_ms"], 45.2)
        pdoc = f["planning_document"]
        self.assertFalse(pdoc["planning_required"])
        # Both context blocks are None (domain separation)
        self.assertIsNone(pdoc["database_context"])
        self.assertIsNone(pdoc["repository_context"])
        self.assertIn("Local Planner fallback triggered", pdoc["warnings"][0])
        self.assertEqual(pdoc["planning_mode"], "General Reasoning")
        # R3: fallback triggers clarification, not silent pass-through
        self.assertTrue(pdoc["clarification_required"])
        self.assertIsNotNone(pdoc["clarification_question"])
        # R4/R5: new fields present in fallback
        self.assertEqual(pdoc["planner_confidence"], 0.0)
        self.assertEqual(pdoc["estimated_execution_type"], "clarification")

    def test_planner_version_is_independent(self):
        """R2: PLANNER_VERSION tracks planner evolution, not project phase."""
        self.assertEqual(PLANNER_VERSION, "1.0.0")

    def test_valid_planning_modes_count(self):
        """6 authorised planning modes."""
        self.assertEqual(len(VALID_PLANNING_MODES), 6)
        for mode in ["Database Planning", "Codebase Planning", "Conversation",
                     "Clarification", "Explanation", "General Reasoning"]:
            self.assertIn(mode, VALID_PLANNING_MODES)

    def test_valid_execution_types_count(self):
        """R5: Exactly 6 valid execution types."""
        self.assertEqual(len(VALID_EXECUTION_TYPES), 6)
        for et in ["sql_query", "database_management", "clarification",
                   "explanation", "codebase_analysis", "general_reasoning"]:
            self.assertIn(et, VALID_EXECUTION_TYPES)

    def test_domain_separation_database_planning(self):
        """Database Planning mode: database_context populated, repository_context null."""
        raw = self._make_raw(
            "Database Planning",
            repository_context={"involved_modules": ["backend/agent/tools.py"], "architectural_layers": ["Agent"]},
            database_context={"target_database": "hr_db", "required_schema_objects": ["employees", "departments"], "required_metadata_elements": ["employees.salary"]},
        )
        parsed = _parse_planner_response(raw)
        pdoc = parsed["planning_document"]
        # database_context kept, repository_context forced null by domain separation
        self.assertIsNotNone(pdoc["database_context"])
        self.assertEqual(pdoc["database_context"]["target_database"], "hr_db")
        self.assertIsNone(pdoc["repository_context"])

    def test_domain_separation_codebase_planning(self):
        """Codebase Planning mode: repository_context populated, database_context null."""
        raw = self._make_raw(
            "Codebase Planning",
            repository_context={"involved_modules": ["backend/agent/tools.py"], "architectural_layers": ["Agent Orchestration"]},
            database_context={"target_database": "my_db", "required_schema_objects": ["users"], "required_metadata_elements": []},
        )
        parsed = _parse_planner_response(raw)
        pdoc = parsed["planning_document"]
        # repository_context kept, database_context forced null by domain separation
        self.assertIsNotNone(pdoc["repository_context"])
        self.assertEqual(pdoc["repository_context"]["involved_modules"], ["backend/agent/tools.py"])
        self.assertIsNone(pdoc["database_context"])

    def test_domain_separation_conversation_mode(self):
        """Conversation mode: both context blocks forced null."""
        raw = self._make_raw(
            "Conversation", planning_required=False, complexity="Low",
            goal="Respond to greeting",
            estimated_execution_type="general_reasoning",
            repository_context={"involved_modules": ["backend/agent/tools.py"], "architectural_layers": []},
            database_context={"target_database": "db", "required_schema_objects": [], "required_metadata_elements": []},
        )
        parsed = _parse_planner_response(raw)
        pdoc = parsed["planning_document"]
        self.assertIsNone(pdoc["database_context"])
        self.assertIsNone(pdoc["repository_context"])
        self.assertEqual(pdoc["planning_mode"], "Conversation")

    def test_invalid_planning_mode_raises_and_triggers_fallback(self):
        """
        R3: Invalid planning_mode must NOT be silently normalized.
        _parse_planner_response must raise ValueError.
        plan() must catch it and return a fallback with clarification_required=True.
        """
        # Use "" as positional arg (no context defaults), override planning_mode via kwarg
        raw = self._make_raw("", planning_mode="TOTALLY_INVALID_MODE",
                             database_context=None)
        # _parse_planner_response raises
        with self.assertRaises(ValueError) as ctx:
            _parse_planner_response(raw)
        self.assertIn("TOTALLY_INVALID_MODE", str(ctx.exception))

    @patch("ai.model_manager.PLANNER_MODEL", None)
    @patch("agent.local_planner._call_groq_planner")
    def test_invalid_planning_mode_causes_fallback_via_plan(self, mock_call):
        """R3: plan() returns a fallback (clarification_required=True) when mode is invalid."""
        # Model returns an invalid planning_mode JSON
        mock_call.return_value = (
            self._make_raw("", planning_mode="UNKNOWN_MODE", database_context=None),
            0.1
        )
        res = plan("test query", {}, "context", [])
        pdoc = res["planning_document"]
        # Fallback plan is returned
        self.assertFalse(pdoc["planning_required"])
        # R3: clarification_required=True in fallback
        self.assertTrue(pdoc["clarification_required"])
        self.assertIsNotNone(pdoc["clarification_question"])
        # R4/R5 fields present in fallback
        self.assertEqual(pdoc["planner_confidence"], 0.0)
        self.assertEqual(pdoc["estimated_execution_type"], "clarification")

    def test_planner_confidence_clamped(self):
        """R4: planner_confidence is clamped to [0.0, 1.0]."""
        # Confidence above 1.0 → clamped to 1.0
        raw = self._make_raw("General Reasoning", planner_confidence=9.99,
                             estimated_execution_type="general_reasoning")
        parsed = _parse_planner_response(raw)
        self.assertEqual(parsed["planning_document"]["planner_confidence"], 1.0)

        # Confidence below 0.0 → clamped to 0.0
        raw2 = self._make_raw("General Reasoning", planner_confidence=-5.0,
                              estimated_execution_type="general_reasoning")
        parsed2 = _parse_planner_response(raw2)
        self.assertEqual(parsed2["planning_document"]["planner_confidence"], 0.0)

        # Normal value 0.85 → preserved
        raw3 = self._make_raw("General Reasoning", planner_confidence=0.85,
                              estimated_execution_type="general_reasoning")
        parsed3 = _parse_planner_response(raw3)
        self.assertEqual(parsed3["planning_document"]["planner_confidence"], 0.85)

    def test_estimated_execution_type_normalised_on_unknown(self):
        """R5: Unknown estimated_execution_type defaults to 'general_reasoning'."""
        raw = self._make_raw("General Reasoning",
                             estimated_execution_type="TOTALLY_WRONG_TYPE",
                             planner_confidence=0.5)
        parsed = _parse_planner_response(raw)
        self.assertEqual(parsed["planning_document"]["estimated_execution_type"], "general_reasoning")

    def test_estimated_execution_type_valid_values_accepted(self):
        """R5: All 6 valid execution types are accepted without modification."""
        for exec_type in VALID_EXECUTION_TYPES:
            mode = "Database Planning" if exec_type in {"sql_query", "database_management", "clarification"} else "General Reasoning"
            raw = self._make_raw(mode, estimated_execution_type=exec_type, planner_confidence=0.9)
            parsed = _parse_planner_response(raw)
            self.assertEqual(parsed["planning_document"]["estimated_execution_type"], exec_type,
                             f"Expected execution type '{exec_type}' to be preserved")

    def test_model_name_independent_of_typo_intent(self):
        """R1: LOCAL_PLANNER_MODEL must not fall back to TYPO_INTENT_MODEL."""
        import agent.local_planner as lp
        # The default must be 'openai/gpt-oss-20b' directly (not via TYPO_INTENT_MODEL)
        self.assertEqual(lp._MODEL_NAME, os.getenv("GROQ_MODEL", os.getenv("LOCAL_PLANNER_MODEL", "openai/gpt-oss-20b")))

    @patch("agent.local_planner._call_groq_planner")
    def test_plan_success(self, mock_call):
        mock_call.return_value = (
            self._make_raw(
                "Database Planning",
                complexity="High",
                goal="Ok",
                planner_confidence=0.95,
                estimated_execution_type="sql_query",
            ),
            0.15
        )
        res = plan("hello", {}, "context", [])
        self.assertEqual(res["schema_version"], "1.0")
        pdoc = res["planning_document"]
        self.assertEqual(pdoc["complexity"], "High")
        self.assertFalse(pdoc["clarification_required"])
        # R4/R5 fields present in successful plan
        self.assertIn("planner_confidence", pdoc)
        self.assertIn("estimated_execution_type", pdoc)
        self.assertEqual(pdoc["estimated_execution_type"], "sql_query")

    @patch("ai.model_manager.PLANNER_MODEL", None)
    @patch("agent.local_planner._call_groq_planner")
    def test_plan_fallback_on_exception(self, mock_call):
        mock_call.side_effect = urllib.error.URLError("Connection refused")
        res = plan("hello", {}, "context", [])
        self.assertEqual(res["schema_version"], "1.0")
        pdoc = res["planning_document"]
        self.assertFalse(pdoc["planning_required"])
        self.assertIn("Local Planner fallback triggered", pdoc["warnings"][0])
        # R3: fallback sets clarification_required=True
        self.assertTrue(pdoc["clarification_required"])
        # R4/R5: new fields in fallback
        self.assertEqual(pdoc["planner_confidence"], 0.0)
        self.assertEqual(pdoc["estimated_execution_type"], "clarification")


class TestPlannerIntegration(unittest.TestCase):
    """
    Test agent_coordinator integration with the Qwen Local Planner.
    Uses the _make_raw helper from TestLocalPlannerUnit for valid JSON fixtures.
    """

    # Reuse the helper from unit tests directly
    _make_raw = staticmethod(TestLocalPlannerUnit._make_raw)

    @patch("agent.local_planner._call_groq_planner")
    @patch("ai.model_manager.PLANNER_MODEL.generate_content")
    def test_coordinator_clarification_handling(self, mock_gemini, mock_groq):
        """
        If Local Planner sets clarification_required to True,
        AgentCoordinator must short-circuit immediately and return NEEDS_CLARIFICATION.
        """
        mock_groq.return_value = (
            TestLocalPlannerUnit._make_raw(
                "",
                planning_mode="Database Planning",
                planning_required=True,
                complexity="Medium",
                goal="Create Table",
                planner_confidence=0.6,
                estimated_execution_type="clarification",
                database_context={"target_database": "sales_db", "required_schema_objects": ["invoices"], "required_metadata_elements": []},
                clarification_required=True,
                clarification_question="What columns?",
            ),
            0.05
        )

        from agent.agent_coordinator import run as coordinator_run
        session = {"last_intent_meta": {"detected_intent": "DDL Operation"}}

        res = coordinator_run(
            user_message="Please create a new table invoices",
            router_db="sales_db",
            session=session,
            session_id="session-123",
            history=[],
            target_db="sales_db"
        )

        self.assertEqual(res["intent"], "NEEDS_CLARIFICATION")
        self.assertEqual(res["reply"], "What columns?")
        self.assertEqual(res["question"], "What columns?")
        self.assertTrue(res["valid"])
        self.assertIsNone(res["risk_level"])
        self.assertEqual(res["clarification_data"]["type"], "AMBIGUOUS_TABLE")

        # Gemini should not have been called at all
        mock_gemini.assert_not_called()

    @patch("agent.local_planner._call_groq_planner")
    @patch("ai.model_manager.PLANNER_MODEL.generate_content")
    def test_coordinator_executor_execution(self, mock_gemini, mock_groq):
        """
        If Local Planner sets clarification_required to False,
        AgentCoordinator must proceed to step loop and pass plan to Gemini.
        """
        # Groq returns successful plan
        mock_groq.return_value = (
            TestLocalPlannerUnit._make_raw(
                "",
                planning_mode="Database Planning",
                planning_required=True,
                complexity="High",
                goal="Find users",
                planner_confidence=0.92,
                estimated_execution_type="sql_query",
                database_context={"target_database": "db", "required_schema_objects": ["users"], "required_metadata_elements": []},
                execution_steps=["step 1"],
                clarification_required=False,
                clarification_question=None,
            ),
            0.05
        )

        # Gemini Executor mock response (final response)
        mock_gemini_resp = MagicMock()
        mock_gemini_resp.text = '{"thought": "Deciding to reply directly", "tool": "final_response", "tool_input": "Here are your users"}'
        mock_gemini_resp.usage_metadata = MagicMock(prompt_token_count=10, candidates_token_count=20)
        mock_gemini.return_value = mock_gemini_resp

        def side_effect_call_with_retry(fn, label):
            fn()
            return (mock_gemini_resp, True, "")

        from agent.agent_coordinator import run as coordinator_run
        session = {"last_intent_meta": {"detected_intent": "Database Query"}}

        # Patch call_with_retry to execute fn lambda and return Gemini's response
        with patch("agent.agent_coordinator.call_with_retry", side_effect=side_effect_call_with_retry):
            res = coordinator_run(
                user_message="Query database to show all registered user information",
                router_db="db",
                session=session,
                session_id="session-123",
                history=[],
                target_db="db"
            )

        self.assertEqual(res["reply"], "Here are your users")
        self.assertEqual(res["intent"], "UNKNOWN")

        # Verify Gemini was called with the plan included in the prompt
        called_args = mock_gemini.call_args
        self.assertIsNotNone(called_args)
        executor_prompt = called_args[0][0]
        self.assertIn("PLANNING DOCUMENT", executor_prompt)
        self.assertIn("Find users", executor_prompt)
        self.assertIn("db", executor_prompt)
