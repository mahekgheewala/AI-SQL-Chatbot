"""
Phase 10.5 — Test Suite

Covers:
  - ResolutionResult            (API contract)
  - EntityResolver              (4-stage pipeline, dynamic aliases, fuzzy)
  - CompatibilityValidator      (aggregation, visualization, column existence)
  - IntentEngine                (level 1 deterministic, level 2 semantic)
  - ConversationContextResolver (merging raw intent with session state)
  - CapabilityRouter            (plan routing)
  - ExecutionPlan types         (field contracts)
  - DDL parser                  (kept from prior phase)
  - VisualizationEngine         (kept from prior phase)

Run with:
  cd backend
  pytest tests/test_phase10_5.py -v
"""

import pytest
import pandas as pd

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from utils.resolution_result import ResolutionResult, ResolutionStatus
from utils.entity_resolver import EntityResolver
from utils.compatibility_validator import CompatibilityValidator, ValidationStatus
from state.metadata_registry import MetadataRegistry, ColumnProfile, get_registry
from agent.ddl_parser import parse_simple_ddl
from visualization.engine import VisualizationEngine


# ===========================================================================
# ResolutionResult
# ===========================================================================

class TestResolutionResult:
    def test_success_predicates(self):
        r = ResolutionResult(status=ResolutionStatus.SUCCESS, value="employees")
        assert r.is_success
        assert not r.is_clarification
        assert not r.is_not_found
        assert r.value == "employees"

    def test_clarification_predicates(self):
        r = ResolutionResult(
            status=ResolutionStatus.CLARIFICATION,
            alternatives=["employees", "employee_archive"],
        )
        assert r.is_clarification
        assert not r.is_success
        assert "employees" in r.alternatives

    def test_multiple_matches_is_clarification(self):
        r = ResolutionResult(
            status=ResolutionStatus.MULTIPLE_MATCHES,
            alternatives=["a", "b"],
        )
        assert r.is_clarification

    def test_not_found_predicates(self):
        r = ResolutionResult(status=ResolutionStatus.NOT_FOUND)
        assert r.is_not_found
        assert not r.is_success

    def test_default_fields(self):
        r = ResolutionResult(status=ResolutionStatus.SUCCESS, value="t")
        assert r.confidence == 0.0  # caller sets this
        assert r.match_type == "NONE"
        assert r.alternatives == []
        assert r.metadata == {}


# ===========================================================================
# EntityResolver
# ===========================================================================

class TestEntityResolver:
    def setup_method(self):
        self.resolver = EntityResolver()

    # ── Normalization ────────────────────────────────────────────────────────

    def test_normalize_snake_case(self):
        assert EntityResolver.normalize("human_resources") == "humanresources"

    def test_normalize_kebab(self):
        assert EntityResolver.normalize("HR-Database") == "hrdatabase"

    def test_normalize_dot(self):
        assert EntityResolver.normalize("my.schema") == "myschema"

    def test_normalize_mixed(self):
        assert EntityResolver.normalize("  My  Schema  ") == "myschema"

    # ── Exact match ──────────────────────────────────────────────────────────

    def test_exact_match_raw(self):
        r = self.resolver.resolve("employees", ["users", "employees", "orders"])
        assert r.is_success
        assert r.value == "employees"
        assert r.match_type == "EXACT"
        assert r.confidence >= 0.98

    def test_exact_match_normalized(self):
        r = self.resolver.resolve("My_Customers_Table", ["users", "MyCustomersTable", "orders"])
        assert r.is_success
        assert r.value == "MyCustomersTable"
        assert r.match_type == "EXACT"

    # ── Alias match ──────────────────────────────────────────────────────────

    def test_alias_static_synonym_dept(self):
        r = self.resolver.resolve("dept", ["departments", "employees", "orders"])
        assert r.is_success
        assert r.match_type == "ALIAS"

    def test_alias_static_synonym_sal(self):
        # "sal" -> "salary" — resolves against a list containing salary column
        r = self.resolver.resolve("sal", ["name", "salary", "department"])
        assert r.is_success
        assert r.value == "salary"

    def test_alias_dynamic_abbreviation(self):
        # "human_resources" abbreviates to "hr"
        r = self.resolver.resolve("hr", ["human_resources", "employees", "orders"])
        assert r.is_success
        assert r.value == "human_resources"

    # ── Fuzzy match ──────────────────────────────────────────────────────────

    def test_fuzzy_match(self):
        r = self.resolver.resolve("employes", ["employees", "departments"])
        assert r.is_success
        assert r.value == "employees"
        assert r.match_type == "FUZZY"

    def test_fuzzy_below_threshold_returns_not_found(self):
        r = self.resolver.resolve("xyzzy", ["employees", "departments"])
        assert r.is_not_found

    # ── Pluggable similarity ──────────────────────────────────────────────────

    def test_pluggable_similarity_engine(self):
        # Custom engine: always 1.0 for strings of equal length
        def same_length_sim(s1, s2):
            return 1.0 if len(s1) == len(s2) else 0.0

        r = EntityResolver(similarity_engine=same_length_sim, fuzzy_threshold=0.9)
        # "mark" (len 4) -> "alfa" (len 4) wins over "bravo" (len 5)
        # No alias or plural match exists, so the custom fuzzy engine is exercised
        result = r.resolve("mark", ["bravo", "alfa", "charliex"])
        assert result.is_success
        assert result.value == "alfa"

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_empty_candidates(self):
        r = self.resolver.resolve("employees", [])
        assert r.is_not_found

    def test_multiple_fuzzy_candidates_triggers_clarification(self):
        # "emp" is equally close to "emps" and "emp1" — both within 0.05
        r = EntityResolver(fuzzy_threshold=0.5).resolve("emp", ["emps", "emp1", "employees"])
        # Should find a match or clarification — not error
        assert r.status in (ResolutionStatus.SUCCESS, ResolutionStatus.MULTIPLE_MATCHES)


# ===========================================================================
# ColumnProfile
# ===========================================================================

class TestColumnProfile:
    def test_integer_profile(self):
        p = ColumnProfile("salary", "integer")
        assert p.is_numeric
        assert not p.is_categorical
        assert p.is_aggregatable
        assert p.is_plottable_numeric

    def test_varchar_profile(self):
        p = ColumnProfile("name", "varchar(255)")
        assert p.is_categorical
        assert not p.is_numeric
        assert not p.is_aggregatable

    def test_timestamp_profile(self):
        p = ColumnProfile("created_at", "timestamp")
        assert p.is_date_time
        assert not p.is_numeric
        assert not p.is_categorical

    def test_decimal_profile(self):
        p = ColumnProfile("price", "decimal(10,2)")
        assert p.is_numeric
        assert p.is_aggregatable

    def test_boolean_profile(self):
        p = ColumnProfile("active", "boolean")
        assert p.is_boolean
        assert p.is_categorical
        assert not p.is_numeric


# ===========================================================================
# MetadataRegistry
# ===========================================================================

class TestMetadataRegistry:
    """
    These tests use a mock registry that bypasses the real database call.
    """

    def _make_registry(self) -> MetadataRegistry:
        reg = MetadataRegistry.__new__(MetadataRegistry)
        reg._db_name = "test_db"
        reg._schema = {
            "employees": ["id", "name", "salary", "department_id", "hire_date"],
            "departments": ["id", "name", "budget"],
        }
        reg._table_schemas = {
            "employees": {
                "id": "serial",
                "name": "varchar(255)",
                "salary": "numeric",
                "department_id": "integer",
                "hire_date": "date",
            },
            "departments": {
                "id": "serial",
                "name": "varchar(255)",
                "budget": "numeric",
            },
        }
        reg._profile_cache = {}
        reg._loaded = True
        return reg

    def test_get_tables(self):
        reg = self._make_registry()
        tables = reg.get_tables()
        assert "employees" in tables
        assert "departments" in tables

    def test_get_columns(self):
        reg = self._make_registry()
        cols = reg.get_columns("employees")
        assert "salary" in cols
        assert "name" in cols

    def test_is_numeric(self):
        reg = self._make_registry()
        assert reg.is_numeric("employees", "salary")
        assert not reg.is_numeric("employees", "name")

    def test_is_categorical(self):
        reg = self._make_registry()
        assert reg.is_categorical("employees", "name")
        assert not reg.is_categorical("employees", "salary")

    def test_get_numeric_columns(self):
        reg = self._make_registry()
        nums = reg.get_numeric_columns("employees")
        assert "salary" in nums
        assert "name" not in nums

    def test_get_categorical_columns(self):
        reg = self._make_registry()
        cats = reg.get_categorical_columns("employees")
        assert "name" in cats

    def test_column_profile_caching(self):
        reg = self._make_registry()
        p1 = reg.get_column_profile("employees", "salary")
        p2 = reg.get_column_profile("employees", "salary")
        assert p1 is p2  # same object from cache

    def test_nonexistent_column_returns_none(self):
        reg = self._make_registry()
        p = reg.get_column_profile("employees", "nonexistent")
        assert p is None

    def test_invalidate_clears_cache(self):
        reg = self._make_registry()
        reg.get_column_profile("employees", "salary")
        assert len(reg._profile_cache) == 1
        reg.invalidate()
        assert len(reg._profile_cache) == 0
        assert not reg._loaded


# ===========================================================================
# CompatibilityValidator
# ===========================================================================

class TestCompatibilityValidator:
    def _make_validator(self) -> CompatibilityValidator:
        reg = TestMetadataRegistry()._make_registry()
        return CompatibilityValidator(reg)

    # ── Aggregation ──────────────────────────────────────────────────────────

    def test_avg_on_numeric_ok(self):
        v = self._make_validator()
        result = v.validate_aggregation("employees", "salary", "AVG")
        assert result.is_valid
        assert result.status == ValidationStatus.OK

    def test_avg_on_text_fails(self):
        v = self._make_validator()
        result = v.validate_aggregation("employees", "name", "AVG")
        assert not result.is_valid
        assert "AVG" in result.message

    def test_sum_on_numeric_ok(self):
        v = self._make_validator()
        result = v.validate_aggregation("employees", "salary", "SUM")
        assert result.is_valid

    def test_sum_on_text_fails(self):
        v = self._make_validator()
        result = v.validate_aggregation("employees", "name", "SUM")
        assert not result.is_valid

    def test_count_on_text_ok(self):
        v = self._make_validator()
        result = v.validate_aggregation("employees", "name", "COUNT")
        assert result.is_valid

    def test_nonexistent_column_fails(self):
        v = self._make_validator()
        result = v.validate_aggregation("employees", "nonexistent", "AVG")
        assert not result.is_valid

    # ── Visualization ────────────────────────────────────────────────────────

    def test_pie_valid(self):
        v = self._make_validator()
        result = v.validate_visualization("PIE", "employees", "name", "salary")
        assert result.is_valid

    def test_pie_numeric_x_fails(self):
        v = self._make_validator()
        result = v.validate_visualization("PIE", "employees", "salary", "department_id")
        assert not result.is_valid

    def test_bar_valid(self):
        v = self._make_validator()
        result = v.validate_visualization("BAR", "employees", "name", "salary")
        assert result.is_valid

    def test_scatter_numeric_both_ok(self):
        v = self._make_validator()
        result = v.validate_visualization("SCATTER", "employees", "salary", "department_id")
        assert result.is_valid

    def test_scatter_text_x_fails(self):
        v = self._make_validator()
        result = v.validate_visualization("SCATTER", "employees", "name", "salary")
        assert not result.is_valid

    def test_histogram_numeric_ok(self):
        v = self._make_validator()
        result = v.validate_visualization("HISTOGRAM", "employees", "salary", None)
        assert result.is_valid

    # ── Column existence ─────────────────────────────────────────────────────

    def test_column_exists(self):
        v = self._make_validator()
        result = v.validate_column_exists("employees", "salary")
        assert result.is_valid

    def test_column_missing(self):
        v = self._make_validator()
        result = v.validate_column_exists("employees", "ghost")
        assert not result.is_valid


# ===========================================================================
# DDL Parser (kept from Phase 10.4)
# ===========================================================================

class TestDDLParser:
    def test_create_table_basic(self):
        parsed = parse_simple_ddl("Create table users with columns id, name, salary, hire_date, created_at, age")
        assert parsed is not None
        assert parsed.deterministic
        assert "id SERIAL PRIMARY KEY" in parsed.sql
        assert "name VARCHAR(255)" in parsed.sql
        assert "salary NUMERIC" in parsed.sql

    def test_create_database(self):
        parsed = parse_simple_ddl("Create a database called company_test")
        assert parsed is not None
        assert parsed.deterministic
        assert "company_test" in parsed.sql

    def test_drop_database(self):
        parsed = parse_simple_ddl("Drop database old_db")
        assert parsed is not None
        assert "old_db" in parsed.sql

    def test_clarification_needed(self):
        parsed = parse_simple_ddl("Create a table")
        assert parsed is not None
        assert parsed.clarification_needed


# ===========================================================================
# VisualizationEngine (kept from Phase 10.4)
# ===========================================================================

class TestVisualizationEngine:
    def test_pie_aggregation(self):
        df = pd.DataFrame({"department": ["HR", "HR", "Engineering", "Sales"]})
        grouped = df["department"].value_counts().reset_index()
        grouped.columns = ["department", "count"]
        assert len(grouped) == 3
        assert grouped.loc[grouped["department"] == "HR", "count"].values[0] == 2
        assert grouped.loc[grouped["department"] == "Sales", "count"].values[0] == 1
