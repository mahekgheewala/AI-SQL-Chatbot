import re
import decimal
import datetime
import pandas as pd
from typing import Dict, Any, Tuple, Optional, List
from agent.result_processing.models import SemanticMetadata, DatasetProfile

# Standard PostgreSQL OID category mappings
_PG_OID_MAP = {
    16: "BOOLEAN",
    20: "NUMERIC",    # INT8
    21: "NUMERIC",    # INT2
    23: "NUMERIC",    # INT4
    700: "NUMERIC",   # FLOAT4
    701: "NUMERIC",   # FLOAT8
    1700: "NUMERIC",  # NUMERIC/DECIMAL
    1082: "DATETIME", # DATE
    1083: "DATETIME", # TIME
    1114: "DATETIME", # TIMESTAMP
    1184: "DATETIME", # TIMESTAMPTZ
    1266: "DATETIME"  # TIMETZ
}

_CURRENCY_KEYWORDS = {
    "price", "salary", "cost", "amount", "revenue", "expense", "fee", "tax", 
    "total", "sum", "wage", "payment", "budget", "price_usd", "amount_usd", "sales"
}
_PERCENTAGE_KEYWORDS = {
    "percent", "percentage", "pct", "rate", "ratio", "margin", "%"
}

def _match_column_keywords(col_name: str, keywords: set) -> bool:
    tokens = set(re.split(r'[^a-zA-Z0-9%]+', col_name.lower()))
    return not tokens.isdisjoint(keywords)

def translate_schema_oids(column_types: Dict[str, int]) -> Dict[str, str]:
    """
    Translates raw PostgreSQL description integer OIDs into unified standard types:
    'NUMERIC', 'DATETIME', 'BOOLEAN', 'CATEGORICAL'.
    """
    translated = {}
    for col, oid in column_types.items():
        translated[col] = _PG_OID_MAP.get(oid, "CATEGORICAL")
    return translated

def normalize_types(df: pd.DataFrame, schema_types: Dict[str, str], warnings: Optional[List[str]] = None) -> pd.DataFrame:
    """
    Safely normalizes object column types using SQL schema declarations or type scanning.
    Casts Decimal -> float64, Date/Timestamp -> datetime64, and Boolean -> bool.
    """
    df = df.copy()
    for col in df.columns:
        expected_type = schema_types.get(col)
        non_null = df[col].dropna()
        if non_null.empty:
            continue
            
        sample = non_null.head(100)
        
        # Scanning checks
        has_str = any(isinstance(val, str) for val in sample)
        has_numeric = any(isinstance(val, (int, float, decimal.Decimal)) for val in sample)
        has_date_type = any(isinstance(val, (datetime.date, datetime.datetime)) for val in sample)
        
        is_mixed = (has_str and (has_numeric or has_date_type)) or (has_numeric and has_date_type)
        
        if is_mixed:
            if warnings is not None:
                types_set = {type(val).__name__ for val in sample}
                warnings.append(f"Column '{col}' contains conflicting mixed types: {types_set}. Skipping normalization.")
            continue

        has_decimal = all(isinstance(val, decimal.Decimal) for val in sample)
        has_date = all(isinstance(val, (datetime.date, datetime.datetime)) for val in sample)

        # Check and normalize types
        if expected_type == "NUMERIC" or (expected_type is None and has_decimal):
            try:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            except Exception:
                pass
        elif expected_type == "DATETIME" or (expected_type is None and has_date):
            try:
                df[col] = pd.to_datetime(df[col], errors='coerce')
            except Exception:
                pass
        elif expected_type == "BOOLEAN":
            try:
                # Map standard string conversions to boolean safely
                df[col] = df[col].astype(bool)
            except Exception:
                pass
    return df

def profile_semantics(df: pd.DataFrame, schema_types: Optional[Dict[str, str]] = None) -> SemanticMetadata:
    """
    Identifies column semantic categories (numeric, categorical, datetime, boolean).
    Prioritizes PostgreSQL schema_types when DataFrame is empty.
    """
    numeric_cols = []
    categorical_cols = []
    datetime_cols = []
    boolean_cols = []
    special_semantics: Dict[str, str] = {}
    
    # 1. Fallback for Empty Datasets
    if df.empty and schema_types:
        for col, t in schema_types.items():
            if t == "NUMERIC":
                numeric_cols.append(col)
            elif t == "DATETIME":
                datetime_cols.append(col)
            elif t == "BOOLEAN":
                boolean_cols.append(col)
            else:
                categorical_cols.append(col)
        return SemanticMetadata(
            numeric_columns=numeric_cols,
            categorical_columns=categorical_cols,
            datetime_columns=datetime_cols,
            boolean_columns=boolean_cols,
            special_semantics=special_semantics
        )

    # 2. Ingest schema types
    s_types = schema_types or {}

    for col in df.columns:
        series = df[col]
        dtype = series.dtype
        expected_type = s_types.get(col)

        # Boolean detection
        if pd.api.types.is_bool_dtype(dtype) or expected_type == "BOOLEAN":
            boolean_cols.append(col)
            continue
            
        # Datetime detection
        if pd.api.types.is_datetime64_any_dtype(dtype) or expected_type == "DATETIME":
            datetime_cols.append(col)
            continue
        
        # Numeric detection
        if pd.api.types.is_numeric_dtype(dtype) or expected_type == "NUMERIC":
            unique_count = series.nunique(dropna=True)
            if unique_count <= 5 and unique_count < len(series) * 0.1:
                categorical_cols.append(col)
            else:
                numeric_cols.append(col)
        else:
            categorical_cols.append(col)

        # Special Semantics (Currency / Percentages)
        if _match_column_keywords(col, _CURRENCY_KEYWORDS):
            special_semantics[col] = "CURRENCY"
        elif _match_column_keywords(col, _PERCENTAGE_KEYWORDS):
            special_semantics[col] = "PERCENTAGE"
        else:
            if pd.api.types.is_string_dtype(dtype) or pd.api.types.is_object_dtype(dtype):
                sample_values = series.dropna().head(10).astype(str)
                if not sample_values.empty:
                    if sample_values.str.strip().str.startswith(("$", "€", "£")).mean() > 0.5:
                        special_semantics[col] = "CURRENCY"
                    elif sample_values.str.strip().str.endswith("%").mean() > 0.5:
                        special_semantics[col] = "PERCENTAGE"

    return SemanticMetadata(
        numeric_columns=numeric_cols,
        categorical_columns=categorical_cols,
        datetime_columns=datetime_cols,
        boolean_columns=boolean_cols,
        special_semantics=special_semantics
    )

def build_dataset_profile(df: pd.DataFrame, semantics: SemanticMetadata) -> DatasetProfile:
    """
    Computes global structural metadata properties for the DataFrame.
    """
    is_empty = df.empty
    row_count = len(df)
    col_count = len(df.columns)
    
    is_single_row = row_count == 1
    is_single_column = col_count == 1
    
    has_nulls = False
    has_duplicates = False
    estimated_memory_usage_bytes = 0
    null_value_count = 0
    duplicate_row_count = 0
    missing_percentage = 0.0
    
    if not is_empty:
        null_value_count = int(df.isnull().sum().sum())
        missing_percentage = float((null_value_count / (row_count * col_count)) * 100) if row_count * col_count > 0 else 0.0
        has_nulls = null_value_count > 0
        try:
            duplicate_row_count = int(df.duplicated().sum())
            has_duplicates = duplicate_row_count > 0
        except Exception:
            pass
        estimated_memory_usage_bytes = int(df.memory_usage(deep=True).sum())

    column_wise_data_types = {col: str(df[col].dtype) for col in df.columns}

    return DatasetProfile(
        is_empty=is_empty,
        is_single_row=is_single_row,
        is_single_column=is_single_column,
        has_nulls=has_nulls,
        has_duplicates=has_duplicates,
        estimated_memory_usage_bytes=estimated_memory_usage_bytes,
        null_value_count=null_value_count,
        duplicate_row_count=duplicate_row_count,
        missing_percentage=missing_percentage,
        numeric_column_count=len(semantics.numeric_columns),
        categorical_column_count=len(semantics.categorical_columns),
        datetime_column_count=len(semantics.datetime_columns),
        boolean_column_count=len(semantics.boolean_columns),
        estimated_dataset_size=estimated_memory_usage_bytes,
        dataset_empty=is_empty,
        single_row=is_single_row,
        multi_row=row_count > 1,
        column_wise_data_types=column_wise_data_types,
        processing_duration=0.0
    )
