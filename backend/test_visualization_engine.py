"""
Visualization Engine — Unit Tests (Phase 10.4)

Tests:
  1. VisualizationParser — keyword and column extraction
  2. ChartSelectionEngine — all 7 chart type rules with confidence + reason
  3. ChartBuilder — produces valid Plotly dicts for all chart types
  4. VisualizationEngine — cache hit/miss cycle
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

import pandas as pd
from visualization.parser import VisualizationParser
from visualization.selection import ChartSelectionEngine
from visualization.builder import ChartBuilder
from visualization.models import VisualizationRequest

SEPARATOR = "=" * 50

def section(title):
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)

# ─────────────────────────────────────────────────────────────────────────────
# 1. Parser Tests
# ─────────────────────────────────────────────────────────────────────────────
def test_parser():
    section("Parser Tests")

    cols        = ["name", "department", "salary", "hire_date"]
    num_cols    = ["salary"]
    cat_cols    = ["name", "department"]
    dt_cols     = ["hire_date"]

    cases = [
        ("bar chart of salary by department",  "BAR",        "department", "salary"),
        ("pie chart of salary by department",  "PIE",        "department", "salary"),
        ("line chart salary over hire_date",   "LINE",       "hire_date",  "salary"),
        ("scatter salary vs bonus",            "SCATTER",    "salary",     None),
        ("histogram of salary",                "HISTOGRAM",  "salary",     None),
        ("box plot of salary distribution",    "BOX",        "salary",     None),
        ("plot salary by department",          None,         None,         None),  # AUTO — no chart keyword
    ]

    for query, expected_ct, expected_x, expected_y in cases:
        req = VisualizationParser.parse(query, cols, num_cols, cat_cols, dt_cols)
        assert req.chart_type == expected_ct, (
            f"FAIL [{query}]: expected chart_type={expected_ct}, got {req.chart_type}"
        )
        print(f"  PASS  '{query}' -> chart_type={req.chart_type}, x={req.x_column}, y={req.y_column}")

    print("\nParser Tests: ALL PASSED ✓")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Selection Tests
# ─────────────────────────────────────────────────────────────────────────────
def test_selection():
    section("Chart Selection Tests")

    def _req(chart_type=None, x=None, y=None, cols=None, query=""):
        return VisualizationRequest(
            chart_type=chart_type, x_column=x, y_column=y,
            target_columns=cols or [], raw_query=query
        )

    # BOX — distribution keyword
    df_box = pd.DataFrame({"salary": [1000, 2000, 3000, 4000, 5000]})
    sel = ChartSelectionEngine.select(_req(cols=["salary"], query="box plot of salary"),
                                      df_box, ["salary"], [], [])
    assert sel.chart_type == "BOX", f"Expected BOX, got {sel.chart_type}"
    assert sel.confidence >= 0.90
    print(f"  PASS  BOX — {sel.reason} (conf={sel.confidence})")

    # LINE — datetime + numeric
    df_line = pd.DataFrame({"hire_date": ["2020-01", "2020-02"], "salary": [1000, 2000]})
    sel = ChartSelectionEngine.select(_req(cols=["hire_date", "salary"]),
                                      df_line, ["salary"], [], ["hire_date"])
    assert sel.chart_type == "LINE", f"Expected LINE, got {sel.chart_type}"
    print(f"  PASS  LINE — {sel.reason} (conf={sel.confidence})")

    # PIE — low cardinality categorical (3 groups)
    df_pie = pd.DataFrame({"dept": ["A", "B", "C"], "salary": [100, 200, 300]})
    sel = ChartSelectionEngine.select(_req(cols=["dept", "salary"]),
                                      df_pie, ["salary"], ["dept"], [])
    assert sel.chart_type == "PIE", f"Expected PIE, got {sel.chart_type}"
    print(f"  PASS  PIE — {sel.reason} (conf={sel.confidence})")

    # HORIZONTAL BAR — long labels (7 groups with very long names → cardinality > 6, avg len > 12)
    df_hbar = pd.DataFrame({
        "department": [
            "Engineering Team Alpha", "Marketing Division Beta", "Sales Force Gamma",
            "Product Design Delta", "Operations Epsilon", "Finance Zeta", "Legal Eta"
        ],
        "salary": [100, 200, 300, 400, 500, 600, 700]
    })
    sel = ChartSelectionEngine.select(_req(cols=["department", "salary"]),
                                      df_hbar, ["salary"], ["department"], [])
    assert sel.chart_type == "HORIZONTAL_BAR", f"Expected HORIZONTAL_BAR, got {sel.chart_type}"
    print(f"  PASS  HORIZONTAL_BAR — {sel.reason} (conf={sel.confidence})")

    # BAR — 7 unique categories (> 6 → not PIE)
    df_bar = pd.DataFrame({
        "dept": ["A", "B", "C", "D", "E", "F", "G"],
        "salary": [1, 2, 3, 4, 5, 6, 7]
    })
    sel = ChartSelectionEngine.select(_req(cols=["dept", "salary"]),
                                      df_bar, ["salary"], ["dept"], [])
    assert sel.chart_type == "BAR", f"Expected BAR, got {sel.chart_type}"
    print(f"  PASS  BAR — {sel.reason} (conf={sel.confidence})")

    # SCATTER — 2 numeric columns
    df_scatter = pd.DataFrame({"salary": [1, 2, 3], "bonus": [10, 20, 30]})
    sel = ChartSelectionEngine.select(_req(cols=["salary", "bonus"]),
                                      df_scatter, ["salary", "bonus"], [], [])
    assert sel.chart_type == "SCATTER", f"Expected SCATTER, got {sel.chart_type}"
    print(f"  PASS  SCATTER — {sel.reason} (conf={sel.confidence})")

    # HISTOGRAM — single numeric column
    df_hist = pd.DataFrame({"salary": [1, 2, 3]})
    sel = ChartSelectionEngine.select(_req(cols=["salary"]),
                                      df_hist, ["salary"], [], [])
    assert sel.chart_type == "HISTOGRAM", f"Expected HISTOGRAM, got {sel.chart_type}"
    print(f"  PASS  HISTOGRAM — {sel.reason} (conf={sel.confidence})")

    # MANUAL override
    sel = ChartSelectionEngine.select(_req(chart_type="PIE", cols=["salary"]),
                                      df_hist, ["salary"], [], [])
    assert sel.chart_type == "PIE" and sel.mode == "MANUAL"
    print(f"  PASS  MANUAL override → PIE (conf={sel.confidence})")

    print("\nChart Selection Tests: ALL PASSED ✓")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Builder Tests
# ─────────────────────────────────────────────────────────────────────────────
def test_builder():
    section("Chart Builder Tests")

    df = pd.DataFrame({
        "department": ["A", "B", "C", "D", "E", "F", "G"],
        "salary":     [1000, 2000, 3000, 4000, 5000, 6000, 7000],
        "bonus":      [100,  200,  300,  400,  500,  600,  700],
        "hire_date":  ["2020-01", "2020-02", "2020-03",
                       "2020-04", "2020-05", "2020-06", "2020-07"],
    })

    for ct, x, y in [
        ("BAR",            "department", "salary"),
        ("HORIZONTAL_BAR", "salary",     "department"),
        ("PIE",            "department", "salary"),
        ("LINE",           "hire_date",  "salary"),
        ("SCATTER",        "salary",     "bonus"),
        ("HISTOGRAM",      "salary",     None),
        ("BOX",            "department", "salary"),
    ]:
        result = ChartBuilder.build(ct, df, x, y, title=f"Test {ct}", x_label=x, y_label=y or "")
        assert result["chart_type"] == ct
        assert isinstance(result["chart_data"], list), f"{ct}: chart_data must be a list"
        assert len(result["chart_data"]) > 0, f"{ct}: chart_data is empty"
        assert isinstance(result["layout"], dict), f"{ct}: layout must be a dict"
        print(f"  PASS  {ct} — {len(result['chart_data'])} trace(s), layout keys: {list(result['layout'].keys())[:4]}")

    print("\nChart Builder Tests: ALL PASSED ✓")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Visualization Cache Tests
# ─────────────────────────────────────────────────────────────────────────────
def test_viz_cache():
    section("Visualization Cache Tests")

    from visualization.engine import (
        VisualizationEngine, _make_cache_key, _get_viz_cache, _store_viz_cache, _clear_viz_cache_for_session
    )
    from visualization.models import VisualizationResult, VisualizationChart, VisualizationMetadata

    session_id = "test-viz-cache-session"
    _clear_viz_cache_for_session(session_id)

    # Build a dummy result
    dummy_chart = VisualizationChart(chart_type="BAR", chart_data=[{"type": "bar", "x": [], "y": []}], layout={})
    dummy_meta  = VisualizationMetadata(chart_id="abc", dataset_fingerprint="fp1", cache_status="MISS")
    dummy_result = VisualizationResult(title="T", subtitle="S", summary="Sum", chart=dummy_chart, metadata=dummy_meta)

    key = _make_cache_key("fp1", "BAR", "department", "salary", ("department", "salary"), ())

    # MISS
    assert _get_viz_cache(session_id, key) is None
    print("  PASS  Cache MISS on empty cache")

    # Store & HIT
    _store_viz_cache(session_id, key, dummy_result)
    hit = _get_viz_cache(session_id, key)
    assert hit is not None and hit.chart.chart_type == "BAR"
    print("  PASS  Cache HIT after store")

    # Different key → MISS
    key2 = _make_cache_key("fp1", "PIE", "department", "salary", ("department", "salary"), ())
    assert _get_viz_cache(session_id, key2) is None
    print("  PASS  Different config key → MISS")

    # Clear → MISS
    _clear_viz_cache_for_session(session_id)
    assert _get_viz_cache(session_id, key) is None
    print("  PASS  Cache cleared → MISS")

    print("\nVisualization Cache Tests: ALL PASSED ✓")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Quality Refinements Tests
# ─────────────────────────────────────────────────────────────────────────────
def test_refinements():
    section("Quality Refinements Regression Tests")

    # 1. Axis Selection Priority (id, name, salary) -> (name, salary)
    df_prio = pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"], "salary": [1000, 2000]})
    req = VisualizationRequest(raw_query="plot employees")
    sel = ChartSelectionEngine.select(req, df_prio, ["id", "salary"], ["name"], [])
    assert sel.x_column == "name", f"Expected x_column to be 'name', got '{sel.x_column}'"
    assert sel.y_column == "salary", f"Expected y_column to be 'salary', got '{sel.y_column}'"
    print("  PASS  Axis Selection Priority: 'id' skipped, 'name' and 'salary' selected.")

    # 2. Multi-Measure Ambiguity -> CLARIFICATION
    df_ambig = pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"], "salary": [1000, 2000], "bonus": [100, 200]})
    sel = ChartSelectionEngine.select(req, df_ambig, ["id", "salary", "bonus"], ["name"], [])
    assert sel.chart_type == "CLARIFICATION", f"Expected CLARIFICATION, got '{sel.chart_type}'"
    assert "salary" in sel.ambiguous_columns and "bonus" in sel.ambiguous_columns
    print("  PASS  Multi-Measure Ambiguity: CLARIFICATION returned with ambiguous columns.")

    # 3. Non-Numeric Dataset -> CLARIFICATION
    df_non_num = pd.DataFrame({"name": ["Alice", "Bob"], "department": ["HR", "IT"]})
    sel = ChartSelectionEngine.select(req, df_non_num, [], ["name", "department"], [])
    assert sel.chart_type == "CLARIFICATION"
    print("  PASS  Non-Numeric Dataset: CLARIFICATION returned safely.")

    # 4. Confidence-Based Decision Gating (<0.55 -> Clarification in VisualizationEngine)
    from agent.result_processing.models import (
        ProcessedResult, DatasetMetadata, SemanticMetadata, ExecutionMetadata,
        StatisticsMetadata, PipelineMetadata, ProcessingMetadata, DatasetProfile
    )
    from visualization.engine import VisualizationEngine

    df_low_conf = pd.DataFrame({"col_a": ["val1", "val2"], "col_b": ["val1", "val2"]})
    res_low = ProcessedResult(
        dataset=DatasetMetadata(dataframe=df_low_conf, columns=["col_a", "col_b"]),
        semantics=SemanticMetadata(numeric_columns=[], categorical_columns=["col_a", "col_b"], datetime_columns=[]),
        execution=ExecutionMetadata(database_name="db", sql="SELECT * FROM table", row_count=2, column_count=2),
        statistics=StatisticsMetadata(),
        pipeline=PipelineMetadata(),
        processing=ProcessingMetadata(),
        profile=DatasetProfile()
    )
    result = VisualizationEngine.generate(res_low, "plot random columns", session_id="test-session")
    assert result.status == "NEEDS_CLARIFICATION", f"Expected NEEDS_CLARIFICATION status, got '{result.status}'"
    print("  PASS  Confidence-Based Decision Gating: low confidence results mapped to NEEDS_CLARIFICATION.")

    # 5. Semantic Explanation Templates
    res_prio = ProcessedResult(
        dataset=DatasetMetadata(dataframe=df_prio, columns=["id", "name", "salary"]),
        semantics=SemanticMetadata(numeric_columns=["id", "salary"], categorical_columns=["name"], datetime_columns=[]),
        execution=ExecutionMetadata(database_name="db", sql="SELECT id, name, salary FROM employees", row_count=2, column_count=3),
        statistics=StatisticsMetadata(),
        pipeline=PipelineMetadata(),
        processing=ProcessingMetadata(),
        profile=DatasetProfile()
    )
    result = VisualizationEngine.generate(res_prio, "plot salaries by name", session_id="test-session")
    assert result.status == "SUCCESS"
    assert "breakdown of **employee salaries** by **employees**" in result.summary, f"Expected semantic summary, got: {result.summary}"
    print("  PASS  Semantic Explanation Templates: summary reads naturally.")

    print("\nQuality Refinements Regression Tests: ALL PASSED ✓")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + SEPARATOR)
    print("  VISUALIZATION ENGINE UNIT TESTS")
    print(SEPARATOR)

    test_parser()
    test_selection()
    test_builder()
    test_viz_cache()
    test_refinements()

    print(f"\n{SEPARATOR}")
    print("  ALL VISUALIZATION UNIT TESTS PASSED SUCCESSFULLY!")
    print(SEPARATOR + "\n")
