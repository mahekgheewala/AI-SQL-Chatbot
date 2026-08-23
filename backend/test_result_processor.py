import sys
import os
import decimal
import datetime
import pandas as pd
import numpy as np

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agent.result_processing import ResultProcessor, ProcessedResult

def test_result_processor():
    print("==================================================")
    print("RUNNING RESULT PROCESSING LAYER UNIT TESTS (REV 2)")
    print("==================================================")

    # ─────────────────────────────────────────────────────────────────────────
    # Scenario 1: Small Dataset IMMEDIATE Statistics & Type Profiling (with OIDs)
    # ─────────────────────────────────────────────────────────────────────────
    print("Scenario 1: Small Dataset Type Profiling and Stats...")
    columns = ["id", "salary", "department", "hire_date", "pct_bonus"]
    rows = [
        [1, 50000.0, "HR", "2020-01-15", 0.05],
        [2, 60000.0, "Engineering", "2021-03-22", 0.10],
        [3, 75000.0, "Engineering", "2019-11-01", 0.15],
        [4, 45000.0, "Sales", "2022-07-10", 0.08]
    ]

    # Map database OIDs: 23=INT4, 1700=NUMERIC, 1043=VARCHAR, 1082=DATE, 701=FLOAT8
    column_types = {
        "id": 23,
        "salary": 1700,
        "department": 1043,
        "hire_date": 1082,
        "pct_bonus": 701
    }

    processed = ResultProcessor.process(
        columns=columns,
        rows=rows,
        sql="SELECT * FROM employees;",
        dbname="hr_db",
        classification="Standard Query",
        selected_pipeline="STANDARD",
        execution_path="STANDARD -> STANDARD_SQL -> QUERY",
        column_types=column_types
    )

    # Check models initialization
    assert processed.execution.row_count == 4
    assert processed.execution.column_count == 5
    assert processed.execution.database_name == "hr_db"
    assert processed.execution.sql == "SELECT * FROM employees;"
    assert processed.dataset.columns == columns
    assert processed.dataset.dataframe is not None
    assert len(processed.dataset.dataframe) == 4

    # Check semantic typing (normalizing strings to datetime because of OID)
    assert "id" in processed.semantics.numeric_columns
    assert "salary" in processed.semantics.numeric_columns
    assert "pct_bonus" in processed.semantics.numeric_columns
    assert "department" in processed.semantics.categorical_columns
    assert "hire_date" in processed.semantics.datetime_columns

    # Check special semantics matching
    assert processed.semantics.special_semantics["salary"] == "CURRENCY"
    assert processed.semantics.special_semantics["pct_bonus"] == "PERCENTAGE"

    # Check 9-key statistics calculations
    assert processed.statistics.status == "IMMEDIATE"
    sal_stats = processed.statistics.numeric_stats["salary"]
    
    # 9-key count check
    assert len(sal_stats) == 9
    assert sal_stats["count"] == 4.0
    assert sal_stats["minimum"] == 45000.0
    assert sal_stats["maximum"] == 75000.0
    assert sal_stats["mean"] == 57500.0
    assert sal_stats["median"] == 55000.0
    assert sal_stats["sum"] == 230000.0
    assert "variance" in sal_stats
    assert "standard_deviation" in sal_stats
    assert sal_stats["unique_count"] == 4.0

    dept_stats = processed.statistics.categorical_stats["department"]
    assert dept_stats["unique_count"] == 3
    assert dept_stats["top_value"] == "Engineering"
    assert dept_stats["top_freq"] == 2

    # Check expanded profile metadata
    assert processed.profile.is_empty is False
    assert processed.profile.is_single_row is False
    assert processed.profile.is_single_column is False
    assert processed.profile.has_nulls is False
    assert processed.profile.has_duplicates is False
    assert processed.profile.null_value_count == 0
    assert processed.profile.duplicate_row_count == 0
    assert processed.profile.missing_percentage == 0.0
    assert processed.profile.numeric_column_count == 3
    assert processed.profile.categorical_column_count == 1
    assert processed.profile.datetime_column_count == 1
    assert processed.profile.boolean_column_count == 0
    assert processed.profile.dataset_empty is False
    assert processed.profile.single_row is False
    assert processed.profile.multi_row is True
    assert "salary" in processed.profile.column_wise_data_types
    assert processed.profile.processing_duration > 0.0
    print("  Scenario 1 SUCCESS!")

    # ─────────────────────────────────────────────────────────────────────────
    # Scenario 2: Large Dataset DEFERRED Lazy Statistics
    # ─────────────────────────────────────────────────────────────────────────
    print("\nScenario 2: Large Dataset Deferred Lazy Statistics...")
    # Create 11 columns, 1000 rows -> 11,000 cells (exceeds 10,000 threshold)
    large_cols = [f"col_{i}" for i in range(11)]
    large_rows = [[float(r * c) for c in range(11)] for r in range(1000)]

    processed_large = ResultProcessor.process(
        columns=large_cols,
        rows=large_rows,
        sql="SELECT * FROM big_table;",
        dbname="heavy_db"
    )

    # Check that statistics calculation was deferred
    assert processed_large.statistics.status == "DEFERRED"
    # Lightweight stats are computed immediately for all numeric and categorical columns
    # col_0 has constant value 0.0 (unique count = 1), so it is categorized as categorical.
    # Therefore, 10 columns are numeric and 1 is categorical.
    assert len(processed_large.statistics.numeric_stats) == 10
    assert len(processed_large.statistics.categorical_stats) == 1
    first_col_stats = processed_large.statistics.numeric_stats["col_1"]
    assert len(first_col_stats) == 5  # count, minimum, maximum, unique_count, sum
    assert "mean" not in first_col_stats
    assert any("exceeds" in w for w in processed_large.processing.warnings)
    print("  Large dataset lightweight statistics computed; advanced statistics deferred successfully.")

    # Explicitly trigger deferred computation
    processed_large.compute_full_statistics()

    # Check that advanced statistics are now computed and merged (9 keys total)
    assert processed_large.statistics.status == "COMPUTED"
    assert len(first_col_stats) == 9
    assert "mean" in first_col_stats
    assert abs(first_col_stats["mean"] - 499.5) < 0.01
    print("  Deferred stats computation triggered and succeeded.")
    print("  Scenario 2 SUCCESS!")

    # ─────────────────────────────────────────────────────────────────────────
    # Scenario 3: Empty Dataset Handling (with OID mapping fallbacks)
    # ─────────────────────────────────────────────────────────────────────────
    print("\nScenario 3: Empty Dataset Handling...")
    
    empty_column_types = {
        "id": 23,          # NUMERIC
        "name": 1043,       # CATEGORICAL
        "price": 1700,      # NUMERIC
        "hire_date": 1082,  # DATETIME
        "is_active": 16     # BOOLEAN
    }
    
    processed_empty = ResultProcessor.process(
        columns=["id", "name", "price", "hire_date", "is_active"],
        rows=[],
        column_types=empty_column_types
    )

    assert processed_empty.execution.row_count == 0
    assert processed_empty.profile.is_empty is True
    assert processed_empty.statistics.status == "SKIPPED"
    assert len(processed_empty.statistics.numeric_stats) == 0
    
    # Assert category fallback is mapped correctly from OIDs on empty set
    assert "id" in processed_empty.semantics.numeric_columns
    assert "price" in processed_empty.semantics.numeric_columns
    assert "name" in processed_empty.semantics.categorical_columns
    assert "hire_date" in processed_empty.semantics.datetime_columns
    assert "is_active" in processed_empty.semantics.boolean_columns
    print("  Scenario 3 SUCCESS!")

    # ─────────────────────────────────────────────────────────────────────────
    # Scenario 4: Dataset Profile Constraints (Nulls, Duplicates, Edge Shapes)
    # ─────────────────────────────────────────────────────────────────────────
    print("\nScenario 4: Dataset Profile (Nulls, Duplicates, Edge Shapes)...")
    
    # 1. Single row, single column
    single = ResultProcessor.process(columns=["val"], rows=[[42]])
    assert single.profile.is_single_row is True
    assert single.profile.is_single_column is True

    # 2. Null values present
    null_rows = [
        [1, None],
        [2, "active"]
    ]
    null_processed = ResultProcessor.process(columns=["id", "status"], rows=null_rows)
    assert null_processed.profile.has_nulls is True
    assert null_processed.profile.null_value_count == 1
    assert null_processed.profile.missing_percentage == 25.0  # 1 null out of 4 cells

    # 3. Duplicate rows present
    dup_rows = [
        ["HR", "London"],
        ["HR", "London"],
        ["Sales", "Paris"]
    ]
    dup_processed = ResultProcessor.process(columns=["dept", "city"], rows=dup_rows)
    assert dup_processed.profile.has_duplicates is True
    assert dup_processed.profile.duplicate_row_count == 1
    print("  Scenario 4 SUCCESS!")

    # ─────────────────────────────────────────────────────────────────────────
    # Scenario 5: Type Normalization & Raw SQL Precision Retention
    # ─────────────────────────────────────────────────────────────────────────
    print("\nScenario 5: Type Normalization & Raw Row Precision...")
    
    # 1. Test decimal.Decimal -> float64 type conversion in DataFrame, but preserved in raw_rows
    # 2. Test datetime.date -> datetime64 in DataFrame, but preserved in raw_rows
    # 3. Test mixed type warnings and status state machine partial fallback
    raw_decimal = decimal.Decimal("123.45")
    raw_date = datetime.date(2025, 6, 26)
    
    norm_columns = ["decimal_val", "date_val", "mixed_val"]
    norm_rows = [
        [raw_decimal, raw_date, decimal.Decimal("1.0")],
        [decimal.Decimal("200.00"), datetime.date(2026, 1, 1), "invalid_string"]
    ]
    
    processed_norm = ResultProcessor.process(
        columns=norm_columns,
        rows=norm_rows
    )
    
    # Assert type normalization scanning checks correctly coerced the dataframes
    df = processed_norm.dataset.dataframe
    assert df is not None
    assert pd.api.types.is_numeric_dtype(df["decimal_val"].dtype)
    assert pd.api.types.is_datetime64_any_dtype(df["date_val"].dtype)
    
    # Assert mixed column did not convert and raises a partial state warning
    assert pd.api.types.is_object_dtype(df["mixed_val"].dtype)
    assert processed_norm.processing.status == "PARTIAL"
    assert any("mixed" in w or "normalization" in w for w in processed_norm.processing.warnings)
    
    # Assert raw_rows keeps precision intact
    assert isinstance(processed_norm.raw_rows[0][0], decimal.Decimal)
    assert processed_norm.raw_rows[0][0] == raw_decimal
    assert isinstance(processed_norm.raw_rows[0][1], datetime.date)
    assert processed_norm.raw_rows[0][1] == raw_date
    print("  Scenario 5 SUCCESS!")

    print("\n==================================================")
    print("ALL RESULT PROCESSING LAYER TESTS PASSED SUCCESS!")
    print("==================================================")

if __name__ == "__main__":
    test_result_processor()
