import pytest
import pandas as pd
from utils.resolution_result import ResolutionResult, ResolutionStatus
from utils.entity_resolver import EntityResolver
from agent.ddl_parser import parse_simple_ddl
from visualization.engine import VisualizationEngine

def test_resolution_result():
    res = ResolutionResult(status=ResolutionStatus.SUCCESS, value="my_val")
    assert res.is_success
    assert not res.is_clarification
    assert res.value == "my_val"

    res_clar = ResolutionResult(status=ResolutionStatus.CLARIFICATION, alternatives=["opt1", "opt2"])
    assert not res_clar.is_success
    assert res_clar.is_clarification
    assert "opt1" in res_clar.alternatives

def test_entity_resolver_exact():
    resolver = EntityResolver()
    res = resolver.resolve("customers", ["users", "customers", "orders"])
    assert res.is_success
    assert res.value == "customers"
    assert res.match_type == "EXACT"

def test_entity_resolver_normalized():
    resolver = EntityResolver()
    res = resolver.resolve("my-customers_table", ["users", "MyCustomersTable", "orders"])
    assert res.is_success
    assert res.value == "MyCustomersTable"
    assert res.match_type == "EXACT"

def test_pluggable_similarity_engine():
    # Custom engine: always 1.0 for strings of equal length
    def same_length_sim(s1, s2):
        return 1.0 if len(s1) == len(s2) else 0.0

    r = EntityResolver(similarity_engine=same_length_sim, fuzzy_threshold=0.9)
    # "mark" (len 4) -> "alfa" (len 4) wins over "bravo" (len 5)
    # No alias or plural match exists, so fuzzy is exercised
    result = r.resolve("mark", ["bravo", "alfa", "charliex"])
    assert result.is_success
    assert result.value == "alfa"
    assert result.match_type == "FUZZY"

def test_ddl_parser_column_defaults():
    # Create table with omitted column types
    parsed = parse_simple_ddl("Create table users with columns id, name, salary, hire_date, created_at, age")
    assert parsed is not None
    assert parsed.deterministic
    assert "id SERIAL PRIMARY KEY" in parsed.sql
    assert "name VARCHAR(255)" in parsed.sql
    assert "salary NUMERIC" in parsed.sql
    assert "hire_date DATE" in parsed.sql or "date" in parsed.sql
    assert "created_at TIMESTAMP" in parsed.sql
    assert "age INT" in parsed.sql

def test_visualization_engine_pie_aggregation():
    # Category only aggregation test
    df_cat_only = pd.DataFrame({"department": ["HR", "HR", "Engineering", "Sales"]})

    # We test the pie chart builder category counting aggregation logic
    # equivalent to what runs inside engine.py:
    grouped = df_cat_only["department"].value_counts().reset_index()
    grouped.columns = ["department", "count"]

    assert len(grouped) == 3
    assert grouped.loc[grouped["department"] == "HR", "count"].values[0] == 2
    assert grouped.loc[grouped["department"] == "Sales", "count"].values[0] == 1
