import sys
import os
import pandas as pd
import numpy as np
import decimal
import datetime

# Add backend directory to system path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agent.result_processing import ResultProcessor
from analytics import AnalyticsEngine, AnalysisRequest, AnalyticsResult
from analytics.parser import AnalyticsParser
from analytics.registry import AnalyzerRegistry

def test_analytics_engine():
    print("==================================================")
    print("RUNNING ANALYTICS ENGINE UNIT TESTS")
    print("==================================================")

    # ─────────────────────────────────────────────────────────────────────────
    # Setup: Create a mock ProcessedResult using ResultProcessor
    # ─────────────────────────────────────────────────────────────────────────
    columns = ["id", "salary", "department", "hire_date", "is_active"]
    rows = [
        [1, 50000.0, "HR", "2020-01-15", True],
        [2, 60000.0, "Engineering", "2021-03-22", True],
        [3, 75000.0, "Engineering", "2019-11-01", False],
        [4, 45000.0, "Sales", "2022-07-10", True],
        [5, 80000.0, "HR", "2018-05-05", False]
    ]
    column_types = {
        "id": 23, "salary": 1700, "department": 1043, "hire_date": 1082, "is_active": 16
    }
    
    result = ResultProcessor.process(
        columns=columns,
        rows=rows,
        sql="SELECT * FROM employees;",
        dbname="test_db",
        column_types=column_types
    )

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Test Analytics Parser
    # ─────────────────────────────────────────────────────────────────────────
    print("Testing Analytics Parser...")
    
    # Test average salary
    req_avg = AnalyticsParser.parse("What is the average salary?", result)
    assert req_avg.analysis_type == "AVERAGE"
    assert req_avg.target_columns == ["salary"]
    assert req_avg.confidence == "HIGH"
    
    # Test synonym matching ("highest salary" -> MAXIMUM with explicit column)
    req_max = AnalyticsParser.parse("highest salary", result)
    assert req_max.analysis_type == "MAXIMUM"
    assert req_max.target_columns == ["salary"]
    assert req_max.confidence == "HIGH"
    
    # "Who earns the most?" — "earns" is a true synonym of "salary", not a
    # spelling/pluralization variant, so schema-driven resolution (exact ->
    # normalized -> alias -> fuzzy against real column names) correctly
    # declines to guess rather than relying on a hardcoded synonym table.
    # It falls back to a general summary instead of a wrong/lucky match.
    req_syn = AnalyticsParser.parse("Who earns the most?", result)
    assert req_syn.analysis_type == "BUSINESS_SUMMARY"
    
    # Test Top N ("top 3 salaries" - synonym mapping of salaries -> salary)
    req_top = AnalyticsParser.parse("top 3 salaries", result)
    assert req_top.analysis_type == "TOP_N"
    assert req_top.target_columns == ["salary"]
    assert req_top.limit == 3
    assert req_top.confidence == "MEDIUM"
    
    # Test Group By ("average salary by department")
    req_group = AnalyticsParser.parse("average salary by department", result)
    assert req_group.analysis_type == "GROUP_BY"
    assert req_group.target_columns == ["salary"]
    assert req_group.group_by == ["department"]
    assert req_group.confidence == "HIGH"
    
    # Test unknown request
    req_unk = AnalyticsParser.parse("Predict salary for next year", result)
    assert req_unk.analysis_type == "UNKNOWN_ANALYSIS"
    assert req_unk.confidence == "LOW"
    
    print("  Parser tests passed.")

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Test Registry & Engine Execution
    # ─────────────────────────────────────────────────────────────────────────
    print("\nTesting Registry & Engine Execution...")
    
    # Test Average query
    res_avg = AnalyticsEngine.analyze(result, "What is the average salary?")
    assert res_avg.success is True
    assert res_avg.analysis_type == "AVERAGE"
    assert res_avg.metrics["average"] == 62000.0  # (50+60+75+45+80)/5 = 62
    assert "average value of **salary**" in res_avg.summary
    
    # Test Top 3 query
    res_top = AnalyticsEngine.analyze(result, "top 3 salaries")
    assert res_top.success is True
    assert res_top.analysis_type == "TOP_N"
    assert len(res_top.tables[0]["rows"]) == 3
    # Check sorting order: 80,000 first, 75,000 second, 60,000 third
    assert res_top.tables[0]["rows"][0]["salary"] == 80000.0
    assert res_top.tables[0]["rows"][1]["salary"] == 75000.0
    assert res_top.tables[0]["rows"][2]["salary"] == 60000.0
    
    # Test Group By query
    res_group = AnalyticsEngine.analyze(result, "average salary by department")
    assert res_group.success is True
    assert res_group.analysis_type == "GROUP_BY"
    # Should yield rows for HR, Engineering, Sales
    rows_gb = res_group.tables[0]["rows"]
    assert len(rows_gb) == 3
    # Average HR: (50+80)/2 = 65000.0
    hr_row = next(r for r in rows_gb if r["department"] == "HR")
    assert hr_row["average"] == 65000.0
    
    # Test Unknown query fallback warning (No BusinessSummary fallback)
    res_unk = AnalyticsEngine.analyze(result, "predict salary next year")
    assert res_unk.success is False
    assert res_unk.analysis_type == "UNKNOWN_ANALYSIS"
    assert "Unsupported analytics request" in res_unk.summary
    assert "Unsupported analytics request." in res_unk.warnings
    
    print("  Execution tests passed.")

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Test Failures & Safe Computations
    # ─────────────────────────────────────────────────────────────────────────
    print("\nTesting Edge Cases & Safe Computations...")
    
    # Empty Dataset
    empty_result = ResultProcessor.process(columns=["id", "salary"], rows=[])
    res_empty = AnalyticsEngine.analyze(empty_result, "average salary")
    assert res_empty.success is False
    assert "no rows" in res_empty.summary
    
    # Missing Column
    res_missing = AnalyticsEngine.analyze(result, "average bonus_points")
    assert res_missing.success is False
    assert "couldn't find a numeric column named" in res_missing.summary
    
    # No Numeric Columns
    text_result = ResultProcessor.process(columns=["name", "dept"], rows=[["Alice", "HR"], ["Bob", "Engineering"]])
    res_no_num = AnalyticsEngine.analyze(text_result, "average name")
    assert res_no_num.success is False
    assert "does not contain any numeric columns" in res_no_num.summary
    
    res_no_num_table = AnalyticsEngine.analyze(text_result, "average")
    assert res_no_num_table.success is False
    assert "does not contain any numeric columns" in res_no_num_table.summary
    
    # Single Row standard deviation calculation (avoids division by zero)
    single_row_result = ResultProcessor.process(columns=["id", "salary"], rows=[[1, 50000.0]], column_types={"id": 23, "salary": 1700})
    # Since stddev is not in default immediate stats, we call compute_full_statistics
    # and verify it calculates cleanly
    single_row_result.compute_full_statistics()
    stddev_val = single_row_result.statistics.numeric_stats["salary"].get("standard_deviation")
    assert stddev_val == 0.0 or stddev_val is None or np.isnan(stddev_val)
    
    print("  Edge cases tests passed.")

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Test Result Caching
    # ─────────────────────────────────────────────────────────────────────────
    print("\nTesting Result Caching...")
    
    session_id = "test-session-999"
    
    # Verify initially empty
    assert AnalyticsEngine.get_source_cache(session_id) is None
    
    # Cache source
    from analytics.helpers import extract_tables_from_select, get_sql_fingerprint
    sql_str = result.execution.sql or ""
    source_tables = extract_tables_from_select(sql_str)
    fingerprint = get_sql_fingerprint(sql_str)
    
    AnalyticsEngine.cache_source(
        session_id=session_id,
        result=result,
        source_tables=source_tables,
        db_name="test_db",
        sql_fingerprint=fingerprint,
        execution_mode="STANDARD_SQL"
    )
    
    cached = AnalyticsEngine.get_source_cache(session_id)
    assert cached is not None
    assert cached.execution.sql == "SELECT * FROM employees;"
    
    # Clear cache
    AnalyticsEngine.clear_cache(session_id)
    assert AnalyticsEngine.get_source_cache(session_id) is None
    
    print("  Caching tests passed.")

    print("\n==================================================")
    print("ALL ANALYTICS ENGINE UNIT TESTS PASSED SUCCESS!")
    print("==================================================")

if __name__ == "__main__":
    test_analytics_engine()
