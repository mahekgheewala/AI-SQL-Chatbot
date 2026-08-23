import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional
from analytics.models import AnalysisRequest, AnalyticsResult
from analytics.helpers import safe_float
from agent.result_processing.models import ProcessedResult

class BaseAnalyzer:
    def analyze(self, request: AnalysisRequest, result: ProcessedResult) -> AnalyticsResult:
        """Computes deterministic analytical reasoning on the ProcessedResult."""
        raise NotImplementedError()

class NumericSummaryAnalyzer(BaseAnalyzer):
    def analyze(self, request: AnalysisRequest, result: ProcessedResult) -> AnalyticsResult:
        df = result.dataset.dataframe
        warnings = []
        
        if not request.target_columns:
            return AnalyticsResult(
                analysis_type=request.analysis_type,
                success=False,
                confidence=request.confidence,
                title="Numeric Analysis Failed",
                summary="No target column was specified for analysis.",
                warnings=["No target column specified."]
            )
            
        col = request.target_columns[0]
        if df is None or df.empty:
            return AnalyticsResult(
                analysis_type=request.analysis_type,
                success=False,
                confidence=request.confidence,
                title="Numeric Analysis Failed",
                summary="The dataset is empty. Cannot compute numeric aggregations.",
                warnings=["Empty dataset."]
            )
            
        actual_col = col
        if col not in df.columns:
            # Let's check if there is a matching column or a fallback
            aggregate_names = {"avg", "average", "mean", "sum", "total", "min", "minimum", "max", "maximum", "count", "cnt"}
            found_agg_col = None
            for c in df.columns:
                if c.lower() in aggregate_names:
                    found_agg_col = c
                    break
            
            if found_agg_col:
                actual_col = found_agg_col
            elif len(result.semantics.numeric_columns) == 1:
                actual_col = result.semantics.numeric_columns[0]
            else:
                return AnalyticsResult(
                    analysis_type=request.analysis_type,
                    success=False,
                    confidence=request.confidence,
                    title="Numeric Analysis Failed",
                    summary=f"I couldn't find a numeric column named \"{col}\" in the selected database. Please specify another column or table.",
                    warnings=[f"Target column '{col}' missing from dataset."]
                )
            
        if actual_col not in result.semantics.numeric_columns:
            return AnalyticsResult(
                analysis_type=request.analysis_type,
                success=False,
                confidence=request.confidence,
                title="Numeric Analysis Failed",
                summary=f"I couldn't find a numeric column named \"{actual_col}\" in the selected database. Please specify another column or table.",
                warnings=[f"Non-numeric column: {actual_col}"]
            )

        # Force full statistics calculation if deferred
        if result.statistics.status == "DEFERRED":
            try:
                result.compute_full_statistics()
            except Exception as e:
                warnings.append(f"Failed to compute deferred full statistics: {e}")
                
        # Re-use cached statistics if available
        stats = result.statistics.numeric_stats.get(actual_col, {})
        
        val = 0.0
        label = request.analysis_type.lower()
        
        if request.analysis_type == "AVERAGE":
            val = stats.get("mean")
            if val is None:
                val = safe_float(df[actual_col].mean())
            summary_label = "average"
        elif request.analysis_type == "MEDIAN":
            val = stats.get("median")
            if val is None:
                val = safe_float(df[actual_col].median())
            summary_label = "median"
        elif request.analysis_type == "SUM":
            val = stats.get("sum")
            if val is None:
                val = safe_float(df[actual_col].sum())
            summary_label = "total"
        elif request.analysis_type == "MINIMUM":
            val = stats.get("minimum")
            if val is None:
                val = safe_float(df[actual_col].min())
            summary_label = "minimum"
        elif request.analysis_type == "MAXIMUM":
            val = stats.get("maximum")
            if val is None:
                val = safe_float(df[actual_col].max())
            summary_label = "maximum"
        else:
            return AnalyticsResult(
                analysis_type=request.analysis_type,
                success=False,
                confidence=request.confidence,
                title="Numeric Analysis Error",
                summary=f"Unsupported numeric aggregation type '{request.analysis_type}'.",
                warnings=[f"Unsupported aggregation: {request.analysis_type}"]
            )
            
        val = safe_float(val)
        
        # Formatting output
        if val.is_integer():
            formatted_val = f"{int(val):,}"
        else:
            formatted_val = f"{val:,.2f}"
            
        title = f"{summary_label.capitalize()} of {col}"
        summary = f"The {summary_label} value of **{col}** is **{formatted_val}**."
        
        return AnalyticsResult(
            analysis_type=request.analysis_type,
            success=True,
            confidence=request.confidence,
            title=title,
            summary=summary,
            metrics={label: val},
            warnings=warnings,
            metadata={"target_column": col}
        )

class CountAnalyzer(BaseAnalyzer):
    def analyze(self, request: AnalysisRequest, result: ProcessedResult) -> AnalyticsResult:
        df = result.dataset.dataframe
        row_count = result.execution.row_count
        
        if df is None:
            return AnalyticsResult(
                analysis_type=request.analysis_type,
                success=False,
                confidence=request.confidence,
                title="Count Analysis Failed",
                summary="Dataset is missing.",
                warnings=["Dataset missing."]
            )
            
        warnings = []
        metrics = {}
        title = "Count Analysis"
        summary = ""
        
        # Determine target column if specified
        col = request.target_columns[0] if request.target_columns else None
        actual_col = col
        if col and col not in df.columns:
            # Let's check if there is an aggregate / count column or a fallback
            aggregate_names = {"count", "cnt", "avg", "average", "sum", "total", "min", "minimum", "max", "maximum"}
            found_agg_col = None
            for c in df.columns:
                if c.lower() in aggregate_names:
                    found_agg_col = c
                    break
            
            if found_agg_col:
                actual_col = found_agg_col
            elif len(df.columns) == 1:
                actual_col = df.columns[0]
            else:
                return AnalyticsResult(
                    analysis_type=request.analysis_type,
                    success=False,
                    confidence=request.confidence,
                    title="Count Analysis Failed",
                    summary=f"Column '{col}' was not found in the dataset.",
                    warnings=[f"Target column '{col}' missing."]
                )
            
        if request.analysis_type == "UNIQUE_COUNT":
            if actual_col:
                val = int(df[actual_col].nunique(dropna=True))
                metrics["unique_count"] = val
                title = f"Unique Values in {col}"
                summary = f"There are **{val}** unique values in column **{col}**."
            else:
                # Average unique values per column
                uniques = {c: int(df[c].nunique(dropna=True)) for c in df.columns}
                metrics = uniques
                title = "Unique Values count per column"
                summary = "Unique counts per column:\n" + "\n".join([f"- **{c}**: {v}" for c, v in uniques.items()])
                
        elif request.analysis_type == "DUPLICATE_COUNT":
            if actual_col:
                val = int(df[actual_col].duplicated().sum())
                metrics["duplicate_count"] = val
                title = f"Duplicate Values in {col}"
                summary = f"There are **{val}** duplicate values in column **{col}**."
            else:
                val = result.profile.duplicate_row_count
                metrics["duplicate_rows"] = val
                title = "Duplicate Rows in Dataset"
                summary = f"There are **{val}** duplicate rows in the dataset."
                
        elif request.analysis_type == "NULL_COUNT":
            if actual_col:
                val = int(df[actual_col].isnull().sum())
                pct = float((val / row_count) * 100) if row_count > 0 else 0.0
                metrics["null_count"] = val
                metrics["null_percentage"] = pct
                title = f"Null values in {col}"
                summary = f"There are **{val}** null values in column **{col}** ({pct:.2f}% null)."
            else:
                val = result.profile.null_value_count
                pct = result.profile.missing_percentage
                metrics["null_cells"] = val
                metrics["null_percentage"] = pct
                title = "Null cells in Dataset"
                summary = f"There are **{val}** total null cells in the dataset ({pct:.2f}% missing)."
                
        else: # Standard record count
            if actual_col:
                val = int(df[actual_col].count()) # non-null count
                metrics["non_null_count"] = val
                title = f"Non-null record count of {col}"
                summary = f"There are **{val}** non-null values in column **{col}**."
            else:
                val = row_count
                metrics["row_count"] = val
                title = "Record Count"
                summary = f"The dataset contains a total of **{val}** records."
                
        return AnalyticsResult(
            analysis_type=request.analysis_type,
            success=True,
            confidence=request.confidence,
            title=title,
            summary=summary,
            metrics=metrics,
            warnings=warnings,
            metadata={"target_column": col} if col else {}
        )

class TopBottomAnalyzer(BaseAnalyzer):
    def analyze(self, request: AnalysisRequest, result: ProcessedResult) -> AnalyticsResult:
        df = result.dataset.dataframe
        if df is None or df.empty:
            return AnalyticsResult(
                analysis_type=request.analysis_type,
                success=False,
                confidence=request.confidence,
                title="Top/Bottom Sorting Failed",
                summary="The dataset is empty or missing.",
                warnings=["Dataset is empty or missing."]
            )
            
        if not request.target_columns:
            return AnalyticsResult(
                analysis_type=request.analysis_type,
                success=False,
                confidence=request.confidence,
                title="Sorting Failed",
                summary="No sorting column was specified.",
                warnings=["No sorting target column."]
            )
            
        col = request.target_columns[0]
        actual_col = col
        if col not in df.columns:
            matched_col = None
            for c in df.columns:
                if col.lower() in c.lower():
                    matched_col = c
                    break
            if matched_col:
                actual_col = matched_col
            elif len(result.semantics.numeric_columns) == 1:
                actual_col = result.semantics.numeric_columns[0]
            else:
                return AnalyticsResult(
                    analysis_type=request.analysis_type,
                    success=False,
                    confidence=request.confidence,
                    title="Sorting Failed",
                    summary=f"I couldn't find a numeric column named \"{col}\" in the selected database. Please specify another column or table.",
                    warnings=[f"Target column '{col}' missing."]
                )
            
        limit = request.limit or 5
        is_top = request.analysis_type == "TOP_N"
        
        # Sort values safely
        sorted_df = df.sort_values(by=actual_col, ascending=not is_top).head(limit)
        
        # Convert output to list of dicts
        table_rows = sorted_df.to_dict(orient="records")
        # Ensure values are JSON-serializable
        for row in table_rows:
            for k, v in row.items():
                if isinstance(v, (pd.Timestamp, np.datetime64)):
                    row[k] = str(v)
                elif isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
                    row[k] = None
        
        direction = "top" if is_top else "bottom"
        title = f"{direction.capitalize()} {limit} records by {col}"
        summary = f"Here are the {direction} **{len(table_rows)}** records sorted by column **{col}**:"
        
        return AnalyticsResult(
            analysis_type=request.analysis_type,
            success=True,
            confidence=request.confidence,
            title=title,
            summary=summary,
            tables=[{"columns": list(df.columns), "rows": table_rows}],
            metadata={"target_column": col, "limit": limit}
        )

class GroupByAnalyzer(BaseAnalyzer):
    def analyze(self, request: AnalysisRequest, result: ProcessedResult) -> AnalyticsResult:
        df = result.dataset.dataframe
        if df is None or df.empty:
            return AnalyticsResult(
                analysis_type=request.analysis_type,
                success=False,
                confidence=request.confidence,
                title="Group By Failed",
                summary="The dataset is empty or missing.",
                warnings=["Dataset is empty."]
            )
            
        if not request.group_by:
            return AnalyticsResult(
                analysis_type=request.analysis_type,
                success=False,
                confidence=request.confidence,
                title="Group By Failed",
                summary="No grouping dimension was specified.",
                warnings=["No grouping dimension specified."]
            )
            
        group_col = request.group_by[0]
        if group_col not in df.columns:
            return AnalyticsResult(
                analysis_type=request.analysis_type,
                success=False,
                confidence=request.confidence,
                title="Group By Failed",
                summary=f"Grouping column '{group_col}' was not found in the dataset.",
                warnings=[f"Grouping column '{group_col}' missing."]
            )
            
        val_col = request.target_columns[0] if request.target_columns else None
        actual_val_col = val_col
        if val_col and val_col not in df.columns:
            if len(result.semantics.numeric_columns) == 1:
                actual_val_col = result.semantics.numeric_columns[0]
            else:
                return AnalyticsResult(
                    analysis_type=request.analysis_type,
                    success=False,
                    confidence=request.confidence,
                    title="Group By Failed",
                    summary=f"Aggregation column '{val_col}' was not found in the dataset.",
                    warnings=[f"Aggregation column '{val_col}' missing."]
                )
            
        # Fall back if no numeric target was provided for grouping aggregates
        if not actual_val_col:
            # If no aggregation target was specified, count records per group
            grouped = df.groupby(group_col).size().reset_index(name="count")
            title = f"Record distribution by {group_col}"
            summary = f"Record distribution grouped by **{group_col}**:"
        else:
            # Group by and aggregate standard descriptive summaries
            # To be highly robust, compute mean, sum, count, min, max
            grouped = df.groupby(group_col)[actual_val_col].agg(['mean', 'sum', 'min', 'max', 'count']).reset_index()
            # Clean aggregate column names
            grouped.columns = [group_col, "average", "total", "minimum", "maximum", "count"]
            title = f"{val_col} analysis grouped by {group_col}"
            summary = f"Descriptive statistics of column **{val_col}** grouped by **{group_col}**:"
            
        table_rows = grouped.to_dict(orient="records")
        # Ensure values are JSON-serializable
        for row in table_rows:
            for k, v in row.items():
                if isinstance(v, (pd.Timestamp, np.datetime64)):
                    row[k] = str(v)
                elif isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
                    row[k] = None
                    
        return AnalyticsResult(
            analysis_type=request.analysis_type,
            success=True,
            confidence=request.confidence,
            title=title,
            summary=summary,
            tables=[{"columns": list(grouped.columns), "rows": table_rows}],
            metadata={"group_by": group_col, "target_column": val_col}
        )

class DistributionAnalyzer(BaseAnalyzer):
    def analyze(self, request: AnalysisRequest, result: ProcessedResult) -> AnalyticsResult:
        df = result.dataset.dataframe
        if df is None or df.empty:
            return AnalyticsResult(
                analysis_type=request.analysis_type,
                success=False,
                confidence=request.confidence,
                title="Distribution Failed",
                summary="The dataset is empty or missing.",
                warnings=["Dataset is empty."]
            )
            
        col = request.target_columns[0] if request.target_columns else None
        if not col:
            # Use the first categorical column
            if result.semantics.categorical_columns:
                col = result.semantics.categorical_columns[0]
            elif result.dataset.columns:
                col = result.dataset.columns[0]
            else:
                return AnalyticsResult(
                    analysis_type=request.analysis_type,
                    success=False,
                    confidence=request.confidence,
                    title="Distribution Failed",
                    summary="No column available for distribution check.",
                    warnings=["No columns available."]
                )
                
        if col not in df.columns:
            return AnalyticsResult(
                analysis_type=request.analysis_type,
                success=False,
                confidence=request.confidence,
                title="Distribution Failed",
                summary=f"Column '{col}' was not found in the dataset.",
                warnings=[f"Target column '{col}' missing."]
            )
            
        counts = df[col].value_counts(dropna=True).reset_index()
        counts.columns = [col, "count"]
        
        total_count = len(df)
        counts["percentage"] = (counts["count"] / total_count * 100) if total_count > 0 else 0.0
        
        table_rows = counts.to_dict(orient="records")
        title = f"Distribution of {col}"
        summary = f"Frequencies and distributions of unique values in column **{col}**:"
        
        return AnalyticsResult(
            analysis_type=request.analysis_type,
            success=True,
            confidence=request.confidence,
            title=title,
            summary=summary,
            tables=[{"columns": list(counts.columns), "rows": table_rows}],
            metadata={"target_column": col}
        )

class BusinessSummaryAnalyzer(BaseAnalyzer):
    def analyze(self, request: AnalysisRequest, result: ProcessedResult) -> AnalyticsResult:
        df = result.dataset.dataframe
        warnings = []
        
        if df is None or df.empty:
            return AnalyticsResult(
                analysis_type=request.analysis_type,
                success=True,
                confidence=request.confidence,
                title="Empty Dataset Summary",
                summary="The dataset is completely empty (0 rows returned).",
                metadata={"row_count": 0}
            )
            
        row_count = len(df)
        col_count = len(df.columns)
        
        summary = f"### Dataset Profile Summary\n"
        summary += f"- **Total Records**: {row_count}\n"
        summary += f"- **Total Columns**: {col_count}\n"
        summary += f"- **Memory Footprint**: {result.profile.estimated_memory_usage_bytes / 1024:.2f} KB\n"
        summary += f"- **Missing Data Percentage**: {result.profile.missing_percentage:.2f}%\n"
        summary += f"- **Duplicate Row Count**: {result.profile.duplicate_row_count}\n\n"
        
        # Compile numeric statistics summaries
        numeric_cols = result.semantics.numeric_columns
        if numeric_cols:
            summary += "#### Numeric Column Statistics Summary:\n"
            # Force statistics computation if deferred
            if result.statistics.status == "DEFERRED":
                try:
                    result.compute_full_statistics()
                except Exception as e:
                    warnings.append(f"Failed to compile deferred statistics: {e}")
                    
            for col in numeric_cols:
                if col in df.columns:
                    stats = result.statistics.numeric_stats.get(col, {})
                    min_val = stats.get("minimum", df[col].min())
                    max_val = stats.get("maximum", df[col].max())
                    mean_val = stats.get("mean", df[col].mean())
                    
                    min_str = f"{min_val:,.2f}" if isinstance(min_val, (int, float)) else str(min_val)
                    max_str = f"{max_val:,.2f}" if isinstance(max_val, (int, float)) else str(max_val)
                    mean_str = f"{mean_val:,.2f}" if isinstance(mean_val, (int, float)) else str(mean_val)
                    
                    summary += f"- **{col}**: Range [{min_str} - {max_str}] | Average: {mean_str}\n"
            summary += "\n"
            
        # Compile categorical column distributions
        cat_cols = result.semantics.categorical_columns
        if cat_cols:
            summary += "#### Categorical Column Profiles:\n"
            for col in cat_cols:
                if col in df.columns:
                    stats = result.statistics.categorical_stats.get(col, {})
                    uniques = stats.get("unique_count", df[col].nunique())
                    top_val = stats.get("top_value")
                    top_freq = stats.get("top_freq")
                    
                    summary += f"- **{col}**: {uniques} unique values"
                    if top_val is not None:
                        summary += f" | Most frequent: '{top_val}' (occurs {top_freq} times)"
                    summary += "\n"
                    
        return AnalyticsResult(
            analysis_type="BUSINESS_SUMMARY",
            success=True,
            confidence=request.confidence,
            title="Dataset Summary Report",
            summary=summary,
            warnings=warnings,
            metadata={"row_count": row_count, "column_count": col_count}
        )
