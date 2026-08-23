import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple

def compute_lightweight_stats(series: pd.Series) -> Dict[str, float]:
    """
    Calculates inexpensive mathematical summaries for a column (min, max, count, unique, sum).
    """
    numeric_series = pd.to_numeric(series, errors='coerce')
    count_val = float(numeric_series.count())
    min_val = float(numeric_series.min()) if count_val > 0 else 0.0
    max_val = float(numeric_series.max()) if count_val > 0 else 0.0
    sum_val = float(numeric_series.sum())
    unique_val = float(numeric_series.nunique())

    def clean_val(val) -> float:
        if pd.isna(val) or np.isnan(val) or np.isinf(val):
            return 0.0
        return float(val)

    return {
        "count": clean_val(count_val),
        "minimum": clean_val(min_val),
        "maximum": clean_val(max_val),
        "unique_count": clean_val(unique_val),
        "sum": clean_val(sum_val)
    }

def compute_advanced_stats(series: pd.Series) -> Dict[str, float]:
    """
    Calculates computationally expensive stats (mean, median, variance, std dev).
    """
    numeric_series = pd.to_numeric(series, errors='coerce')
    count_val = numeric_series.count()
    if count_val == 0:
        return {
            "mean": 0.0,
            "median": 0.0,
            "variance": 0.0,
            "standard_deviation": 0.0
        }
    
    mean_val = float(numeric_series.mean())
    median_val = float(numeric_series.median())
    var_val = float(numeric_series.var()) if count_val > 1 else 0.0
    std_val = float(numeric_series.std()) if count_val > 1 else 0.0

    def clean_val(val) -> float:
        if pd.isna(val) or np.isnan(val) or np.isinf(val):
            return 0.0
        return float(val)

    return {
        "mean": clean_val(mean_val),
        "median": clean_val(median_val),
        "variance": clean_val(var_val),
        "standard_deviation": clean_val(std_val)
    }

def compute_descriptive_stats(
    df: pd.DataFrame, 
    numeric_columns: List[str], 
    categorical_columns: List[str]
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, Any]]]:
    """
    Computes lightweight stats for numeric columns, and categorical stats immediately.
    """
    numeric_stats: Dict[str, Dict[str, float]] = {}
    categorical_stats: Dict[str, Dict[str, Any]] = {}

    if df.empty:
        return numeric_stats, categorical_stats

    # Process lightweight numeric stats
    for col in numeric_columns:
        if col in df.columns:
            numeric_stats[col] = compute_lightweight_stats(df[col])

    # Process categorical columns
    for col in categorical_columns:
        if col in df.columns:
            series = df[col].dropna()
            unique_count = int(df[col].nunique(dropna=True))
            
            top_value = None
            top_freq = 0
            
            if not series.empty:
                counts = series.value_counts()
                if not counts.empty:
                    top_value = str(counts.index[0])
                    top_freq = int(counts.iloc[0])

            categorical_stats[col] = {
                "unique_count": unique_count,
                "top_value": top_value,
                "top_freq": top_freq
            }

    return numeric_stats, categorical_stats
