import os
import sys
import subprocess
import unittest
from unittest.mock import patch, MagicMock

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

class TestFrameworkIssues(unittest.TestCase):
    
    def setUp(self):
        # Save original environment to restore later
        self.original_env = dict(os.environ)

    def tearDown(self):
        # Restore environment
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_env_loading_different_directories(self):
        """Verify that running from different directories loads the same .env configuration."""
        test_script = f"import sys; sys.path.append(r'{backend_dir}'); import os; from utils.config_loader import init_env; init_env(); print('LOADED_KEY:', os.getenv('GEMINI_API_KEY'))"
        
        # Run from backend directory
        res_backend = subprocess.run(
            [sys.executable, "-c", test_script],
            cwd=backend_dir,
            capture_output=True,
            text=True
        )
        out_backend = res_backend.stdout.strip()
        
        # Run from workspace root directory
        project_root = os.path.dirname(backend_dir)
        res_root = subprocess.run(
            [sys.executable, "-c", test_script],
            cwd=project_root,
            capture_output=True,
            text=True
        )
        out_root = res_root.stdout.strip()
        
        print("Backend run output:", out_backend)
        print("Root run output:", out_root)
        
        self.assertEqual(out_backend, out_root, "Environment loading differed between working directories.")
        self.assertTrue("LOADED_KEY:" in out_backend, "Centralized init_env failed to load variables.")

    def test_fail_fast_on_missing_api_keys(self):
        """Verify model_manager raises clear RuntimeError when api keys are missing."""
        # Clear gemini keys from environment
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GEMINI_FALLBACK_API_KEY", None)
        
        # Re-import and run initialize_models to test raising error
        # Since model_manager might be imported already, we run it in a subprocess
        fail_fast_script = f"""
import sys
sys.path.append(r"{backend_dir}")
import dotenv
# Mock load_dotenv so it does not load the disk .env file
dotenv.load_dotenv = lambda *args, **kwargs: None
import os
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GEMINI_FALLBACK_API_KEY", None)
try:
    from ai import model_manager
    model_manager._models_initialized = False
    model_manager.initialize_models()
except RuntimeError as e:
    print("RUNTIME_ERROR_RAISED")
    print(str(e))
    sys.exit(0)
sys.exit(1)
"""
        res = subprocess.run(
            [sys.executable, "-c", fail_fast_script],
            cwd=backend_dir,
            capture_output=True,
            text=True
        )
        output = res.stdout.strip()
        print("Fail fast run output:", output)
        print("Fail fast run error:", res.stderr.strip())
        self.assertIn("RUNTIME_ERROR_RAISED", output, f"Process stderr: {res.stderr.strip()}")
        self.assertIn("Gemini API configuration missing.", output)
        self.assertIn("Neither GEMINI_API_KEY nor GEMINI_FALLBACK_API_KEY is configured.", output)
        self.assertIn("Please update backend/.env.", output)

    def test_circular_imports_different_import_orders(self):
        """Verify no errors occur when modules are imported in different orders."""
        orders = [
            ("import ai.gemini_service", "import ai.model_manager", "import agent.agent_coordinator"),
            ("import agent.agent_coordinator", "import ai.gemini_service", "import ai.model_manager"),
            ("import ai.model_manager", "import ai.gemini_service", "import agent.agent_coordinator")
        ]
        
        for order in orders:
            imports_cmd = "; ".join(order)
            script = f"import sys; sys.path.append(r'{backend_dir}'); {imports_cmd}; print('SUCCESS')"
            res = subprocess.run(
                [sys.executable, "-c", script],
                cwd=backend_dir,
                capture_output=True,
                text=True
            )
            self.assertEqual(res.returncode, 0, f"Failed on import order: {order}\nError: {res.stderr}")
            self.assertIn("SUCCESS", res.stdout)

    def test_find_table_database_intent_regex(self):
        """Verify the deterministic FIND_TABLE_DATABASE regex matches all variations."""
        from agent.agent_coordinator import _FIND_TABLE_DB_RULE
        
        examples = [
            "where is employees",
            "which db contains employees",
            "which database has employees",
            "where can i find employees",
            "where is table employees",
            "where can i find table employees",
            "  where is employees; "
        ]
        
        for ex in examples:
            match = _FIND_TABLE_DB_RULE.match(ex)
            self.assertIsNotNone(match, f"Regex failed to match: '{ex}'")
            self.assertEqual(match.group("table_name").strip('; '), "employees")

    def test_find_table_database_tool_behavior(self):
        """Verify find_table_database tool returns correct mapping of databases."""
        from agent.tools import find_table_database
        
        mock_summaries = {
            "hr_database": ["employees", "departments"],
            "finance_db": ["accounts", "salaries"],
            "new_interns": ["employees", "intern_records"]
        }
        
        with patch("agent.tools.get_metadata") as mock_get_metadata:
            mock_get_metadata.return_value = {"routing_summaries": mock_summaries}
            
            # Case 1: Multiple matches
            res_multi = find_table_database("where is employees")
            self.assertEqual(res_multi["intent"], "FIND_TABLE_DATABASE")
            self.assertEqual(res_multi["table_name"], "employees")
            self.assertEqual(res_multi["databases"], ["hr_database", "new_interns"])
            
            # Case 2: Single match
            res_single = find_table_database("which db has accounts")
            self.assertEqual(res_single["databases"], ["finance_db"])
            
            # Case 3: Zero matches
            res_none = find_table_database("where is products")
            self.assertEqual(res_none["databases"], [])

    def test_find_table_database_formatting(self):
        """Verify the coordinator formats find_table_database output as expected."""
        from agent.agent_coordinator import run as agent_run
        
        mock_summaries = {
            "hr_database": ["employees", "departments"],
            "finance_db": ["accounts", "salaries"],
            "new_interns": ["employees", "intern_records"]
        }
        
        with patch("agent.tools.get_metadata") as mock_get_metadata, \
             patch("agent.agent_coordinator.get_metadata") as mock_coord_metadata:
            
            mock_get_metadata.return_value = {"routing_summaries": mock_summaries, "databases": list(mock_summaries.keys())}
            mock_coord_metadata.return_value = {"routing_summaries": mock_summaries, "databases": list(mock_summaries.keys())}
            
            session = {"selected_database": None, "last_successful_intent": None}
            
            # Test 1: Single match formatting
            res_single = agent_run(
                user_message="where is accounts",
                router_db=None,
                session=session,
                session_id="test_sess",
                history=[],
                target_db=None
            )
            self.assertEqual(res_single["intent"], "FIND_TABLE_DATABASE")
            self.assertIn("Table 'accounts' exists in database: finance_db.", res_single["reply"])
            
            # Test 2: Multiple matches formatting
            res_multi = agent_run(
                user_message="where is employees",
                router_db=None,
                session=session,
                session_id="test_sess",
                history=[],
                target_db=None
            )
            self.assertEqual(res_multi["intent"], "FIND_TABLE_DATABASE")
            self.assertIn("Table 'employees' exists in:", res_multi["reply"])
            self.assertIn("* hr_database", res_multi["reply"])
            self.assertIn("* new_interns", res_multi["reply"])
            
            # Test 3: Zero matches formatting
            res_none = agent_run(
                user_message="where is products",
                router_db=None,
                session=session,
                session_id="test_sess",
                history=[],
                target_db=None
            )
            self.assertEqual(res_none["intent"], "FIND_TABLE_DATABASE")
            self.assertIn("Table 'products' was not found.", res_none["reply"])

    def test_show_table_vs_show_tables_routing(self):
        """Verify "show table <table_name>" vs "show tables" routing outcomes.

        Pipeline consolidation note: "show tables" (plural, no name) is now a
        semantic_frame 'list_tables' capability, routed DIRECT by the gateway
        before agent_coordinator.run() is ever invoked — not a coordinator-level
        dispatch decision any more. "show table <name>" (singular, named) has
        no semantic_frame capability yet, so it's still the narrow
        _find_table_location_tool check inside the coordinator.
        """
        from agent.agent_coordinator import _find_table_location_tool
        from agent.universal_gateway import decide

        # "show table <name>" — no semantic_frame capability yet, routes via
        # the coordinator's narrow _SHOW_TABLE_RULE check.
        find_cases = [
            "show table employees",
            "show table students",
            "display table employees",
            "list table employees"
        ]
        for msg in find_cases:
            self.assertEqual(_find_table_location_tool(msg), "get_schema_info", f"Failed for: {msg}")

        # "show tables" (plural) — a real semantic_frame capability, routed
        # DIRECT by the gateway itself.
        list_cases = [
            "show tables",
            "show all tables",
            "list tables",
            "display tables"
        ]
        for msg in list_cases:
            decision = decide(user_message=msg, raw_message=msg, session={})
            self.assertEqual(decision.route, "DIRECT", f"Failed for: {msg}")
            self.assertEqual(decision.tool_name, "list_tables", f"Failed for: {msg}")

if __name__ == "__main__":
    unittest.main()
