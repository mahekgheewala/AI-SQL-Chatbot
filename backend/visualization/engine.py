"""
Visualization Engine — Orchestrator (Phase 10.4)

Orchestrates the complete visualization pipeline:
  1. Parse user query → VisualizationRequest
  2. Check Visualization Cache (by dataset fingerprint + chart config)
  3. Apply Chart Selection Engine (deterministic + confidence-scored)
  4. Build Plotly chart via ChartBuilder
  5. Store in Visualization Cache
  6. Return VisualizationResult

Dual-cache architecture:
  Source Dataset Cache  (Phase 10.3) — reused directly
       ↓
  Visualization Cache   (Phase 10.4) — keyed by (fingerprint, chart config)
       ↓
  Frontend
"""

from __future__ import annotations
import hashlib
import time
import uuid
from typing import Optional, Dict, Tuple, Any, List

import pandas as pd
from agent.result_processing.models import ProcessedResult
from visualization.models import (
    VisualizationRequest,
    VisualizationResult,
    VisualizationChart,
    VisualizationMetadata,
)
from visualization.parser import VisualizationParser
from visualization.selection import ChartSelectionEngine
from visualization.builder import ChartBuilder

import re

_VIZ_OVERRIDE_PATTERNS = [
    re.compile(r"^\s*(make|change|show|convert|switch|plot|chart|graph)\s+(?:it\s+)?(?:to\s+)?(?:a\s+)?(horizontal|vertical|pie|bar|line|scatter|box|histogram)(?:\s+(?:chart|graph|plot))?\s*$", re.IGNORECASE),
    re.compile(r"^\s*sort\s+(?:by\s+)?([a-zA-Z0-9_]+)?(?:\s+(descending|ascending|desc|asc))?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(descending|ascending|desc|asc)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(horizontal|vertical)\s*$", re.IGNORECASE),
]


# ─── Visualization Cache ─────────────────────────────────────────────────────

# Structure: session_id → {cache_key → VisualizationResult}
_VIZ_CACHE: Dict[str, Dict[str, VisualizationResult]] = {}

_VIZ_CACHE_TTL: float = 600.0  # 10 minutes


def _make_cache_key(
    dataset_fingerprint: str,
    chart_type: Optional[str],
    x_column: Optional[str],
    y_column: Optional[str],
    target_columns: Tuple[str, ...],
    group_by: Tuple[str, ...],
) -> str:
    """Stable SHA-256 hash over all chart-defining parameters."""
    raw = "|".join([
        dataset_fingerprint or "",
        chart_type or "AUTO",
        x_column or "",
        y_column or "",
        ",".join(sorted(target_columns)),
        ",".join(sorted(group_by)),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _get_viz_cache(session_id: str, key: str) -> Optional[VisualizationResult]:
    session_cache = _VIZ_CACHE.get(session_id, {})
    return session_cache.get(key)


def _store_viz_cache(session_id: str, key: str, result: VisualizationResult) -> None:
    if session_id not in _VIZ_CACHE:
        _VIZ_CACHE[session_id] = {}
    _VIZ_CACHE[session_id][key] = result


def _clear_viz_cache_for_session(session_id: str) -> None:
    _VIZ_CACHE.pop(session_id, None)


# ─── Semantic Axis Translation Helpers ────────────────────────────────────────

def _get_semantic_name(col: str) -> str:
    if not col:
        return ""
    col_lower = col.lower()
    if "salary" in col_lower or "wage" in col_lower or "earnings" in col_lower:
        return "employee salaries"
    if "revenue" in col_lower:
        return "revenue"
    if "sale" in col_lower:
        return "sales"
    if "price" in col_lower or "cost" in col_lower:
        return "prices"
    if "amount" in col_lower:
        return "amounts"
    if "score" in col_lower:
        return "scores"
    if "department" in col_lower:
        return "departments"
    if "name" in col_lower:
        return "employees"
    if "product" in col_lower:
        return "products"
    if "category" in col_lower:
        return "categories"
    if "date" in col_lower or "time" in col_lower or "year" in col_lower:
        return "time"
    return col_lower.replace("_", " ")


# ─── Engine ──────────────────────────────────────────────────────────────────

class VisualizationEngine:
    """
    Stateless engine class.
    All public methods are class methods to mirror the AnalyticsEngine API.
    """

    @classmethod
    def generate(
        cls,
        processed_result: ProcessedResult,
        user_message: str,
        session_id: Optional[str] = None,
        source_cache_hit: bool = False,
    ) -> VisualizationResult:
        """
        Main entry point. Generates (or retrieves from cache/applies overrides) a VisualizationResult.
        """
        t_start = time.perf_counter()

        df            = processed_result.dataset.dataframe
        num_cols      = processed_result.semantics.numeric_columns
        cat_cols      = processed_result.semantics.categorical_columns
        dt_cols       = processed_result.semantics.datetime_columns
        all_cols      = processed_result.dataset.columns
        dataset_fp    = processed_result.execution.sql or ""
        dataset_fp    = hashlib.sha256(dataset_fp.encode()).hexdigest()[:24] if dataset_fp else "unknown"
        source_table  = (processed_result.execution.sql or "").split("FROM")[-1].strip().rstrip(";").split()[0] if processed_result.execution.sql else ""
        source_db     = processed_result.execution.database_name or ""

        # ── 0. Handle Visualization Overrides ─────────────────────────────────
        from state.session_store import get_session, update_session
        session = get_session(session_id) if session_id else {}
        last_viz = session.get("last_visualization")

        is_override = False
        if last_viz:
            is_override = any(pat.match(user_message) for pat in _VIZ_OVERRIDE_PATTERNS)

        if is_override:
            chart_type = last_viz.get("chart_type")
            orientation = last_viz.get("orientation", "vertical")
            sorting = last_viz.get("sorting")
            sort_by = last_viz.get("sort_by")
            cat_col = last_viz.get("cat_col")
            num_col = last_viz.get("num_col")
            original_query = last_viz.get("original_query")

            msg_lower = user_message.lower()
            
            # Parse overrides from message
            for pat in _VIZ_OVERRIDE_PATTERNS:
                m = pat.match(user_message)
                if m:
                    if len(m.groups()) >= 2 and m.group(2):
                        val = m.group(2).lower()
                        if val in ["horizontal", "vertical"]:
                            orientation = val
                            chart_type = "HORIZONTAL_BAR" if val == "horizontal" else "BAR"
                        else:
                            chart_type = val.upper()
                            if chart_type == "HORIZONTAL_BAR":
                                orientation = "horizontal"
                            else:
                                orientation = "vertical"
                    elif "sort" in msg_lower:
                        g1 = m.group(1)
                        g2 = m.group(2)
                        if g2:
                            sorting = "descending" if g2.lower() in ["descending", "desc"] else "ascending"
                        else:
                            sorting = "descending"
                        if g1:
                            sort_by = g1.lower()
                    elif val_desc := (m.group(1) if len(m.groups()) >= 1 else None):
                        if val_desc.lower() in ["descending", "desc"]:
                            sorting = "descending"
                        elif val_desc.lower() in ["ascending", "asc"]:
                            sorting = "ascending"
                    elif val_orient := (m.group(1) if len(m.groups()) >= 1 else None):
                        if val_orient.lower() in ["horizontal", "vertical"]:
                            orientation = val_orient.lower()
                            chart_type = "HORIZONTAL_BAR" if orientation == "horizontal" else "BAR"

            # Apply overrides to a copy of the dataframe to prevent cache pollution
            df_copy = df.copy()
            
            # Apply sorting
            if sorting:
                active_sort_col = sort_by or num_col
                if active_sort_col and active_sort_col in df_copy.columns:
                    ascending = (sorting == "ascending")
                    df_copy = df_copy.sort_values(by=active_sort_col, ascending=ascending)

            # Map axes based on selected chart type and orientation
            if chart_type == "HORIZONTAL_BAR":
                x_column = num_col
                y_column = cat_col
            elif chart_type == "HISTOGRAM":
                x_column = num_col
                y_column = None
            elif chart_type == "SCATTER":
                x_column = last_viz.get("x_column")
                y_column = last_viz.get("y_column")
            else:
                x_column = cat_col
                y_column = num_col

            title = cls._build_title(chart_type, x_column, y_column)
            x_label = (x_column or "").replace("_", " ").title()
            y_label = (y_column or "").replace("_", " ").title()

            chart_raw = ChartBuilder.build(
                chart_type=chart_type,
                df=df_copy,
                x_column=x_column,
                y_column=y_column,
                title=title,
                x_label=x_label,
                y_label=y_label,
            )
            render_ms = (time.perf_counter() - t_start) * 1000

            subtitle = cls._build_subtitle(chart_type, x_column, y_column, source_table)
            summary  = cls._build_summary(
                chart_type, x_column, y_column,
                processed_result.execution.row_count, source_table, source_db
            )

            result = VisualizationResult(
                status="SUCCESS",
                title=title,
                subtitle=subtitle,
                summary=summary,
                chart=VisualizationChart(
                    chart_type=chart_raw["chart_type"],
                    chart_data=chart_raw["chart_data"],
                    layout=chart_raw["layout"],
                ),
                metadata=VisualizationMetadata(
                    chart_id=last_viz.get("cache_key") or str(uuid.uuid4()),
                    dataset_fingerprint=dataset_fp,
                    render_time_ms=round(render_ms, 2),
                    generated_at=time.time(),
                    cache_status="MISS",
                    selection_mode="MANUAL",
                    selection_confidence=1.0,
                    selection_reason="Visualization override applied successfully.",
                    x_label=x_label,
                    y_label=y_label,
                    source_table=source_table,
                    source_database=source_db,
                    row_count=processed_result.execution.row_count,
                    column_count=processed_result.execution.column_count,
                ),
            )

            # Store updated settings in session
            last_viz_payload = {
                "chart_type": chart_type,
                "orientation": orientation,
                "sorting": sorting,
                "sort_by": sort_by,
                "x_column": x_column,
                "y_column": y_column,
                "cat_col": cat_col,
                "num_col": num_col,
                "original_query": original_query,
                "table": source_table,
                "cache_key": last_viz.get("cache_key"),
            }
            if session_id:
                update_session(session_id, last_visualization=last_viz_payload)

            return result

        # 1. Parse user message → VisualizationRequest
        req = VisualizationParser.parse(
            user_message=user_message,
            available_columns=all_cols,
            numeric_columns=num_cols,
            categorical_columns=cat_cols,
            datetime_columns=dt_cols,
        )

        # 2. Check Visualization Cache
        cache_key = _make_cache_key(
            dataset_fp,
            req.chart_type,
            req.x_column,
            req.y_column,
            tuple(req.target_columns),
            tuple(req.group_by),
        )

        cached = _get_viz_cache(session_id, cache_key) if session_id else None
        if cached and cached.status == "SUCCESS":
            cached.metadata.cache_status = "HIT"
            cls._print_decision_log(
                category_candidate=cached.metadata.x_label,
                measure_candidate=cached.metadata.y_label,
                confidence=cached.metadata.selection_confidence,
                chart_type=cached.chart.chart_type,
                reason=cached.metadata.selection_reason,
                clarification_needed=False
            )
            return cached

        # 3. Apply Chart Selection Engine
        sel = ChartSelectionEngine.select(
            request=req,
            df=df,
            numeric_columns=num_cols,
            categorical_columns=cat_cols,
            datetime_columns=dt_cols,
        )

        # Gating based on Ambiguity & Confidence Tiers
        is_clarification = (sel.chart_type == "CLARIFICATION" or sel.confidence < 0.55)
        
        cls._print_decision_log(
            category_candidate=sel.x_column or "None",
            measure_candidate=sel.y_column or "None",
            confidence=sel.confidence,
            chart_type=sel.chart_type,
            reason=sel.reason,
            clarification_needed=is_clarification
        )

        if is_clarification:
            ambig = sel.ambiguous_columns
            if sel.reason.startswith("No numeric columns"):
                summary = "No numeric columns found in the dataset to visualize."
            elif not ambig:
                avail_num = [c for c in num_cols if not c.lower().startswith('id_') and not c.lower().endswith('_id') and c.lower() != 'id']
                avail_cat = [c for c in cat_cols if not c.lower().startswith('id_') and not c.lower().endswith('_id') and c.lower() != 'id']
                
                parts = [f"I found the following columns in {source_table or 'the dataset'}:"]
                if avail_cat:
                    parts.append("\nCategory:")
                    for c in avail_cat: parts.append(f"• {c}")
                if avail_num:
                    parts.append("\nNumeric:")
                    for c in avail_num: parts.append(f"• {c}")
                
                if avail_cat and avail_num:
                    parts.append("\nSuggested:\n" + f"• {avail_cat[0]} vs {avail_num[0]}")
                
                summary = "\n".join(parts) if (avail_cat or avail_num) else "I couldn't confidently select columns to visualize."
            else:
                is_num = all(c in num_cols for c in ambig)
                if is_num:
                    summary = f"I found '{req.y_column or 'measures'}' in multiple columns:\n\n" + "\n".join(f"• {c}" for c in ambig) + "\n\nWhich column would you like to visualize?"
                else:
                    summary = f"I found '{req.x_column or 'categories'}' in multiple columns:\n\n" + "\n".join(f"• {c}" for c in ambig) + "\n\nWhich column should be used on the X-axis?"
            
            return VisualizationResult(
                status="NEEDS_CLARIFICATION",
                summary=summary,
                ambiguous_columns=ambig
            )

        # ── 4. Chart Compatibility Validation & Aggregation ──
        if df is None or df.empty:
            return VisualizationResult(
                status="NEEDS_CLARIFICATION",
                summary="The dataset is empty or does not exist. I cannot build a chart without data."
            )

        c_type = sel.chart_type.upper()
        x_col = sel.x_column
        y_col = sel.y_column

        # Row count check
        if len(df) < 1:
            return VisualizationResult(
                status="NEEDS_CLARIFICATION",
                summary="The dataset is empty. I cannot build a chart without rows."
            )

        # Required columns exist
        if x_col and x_col not in df.columns:
            return VisualizationResult(
                status="NEEDS_CLARIFICATION",
                summary=f"The required column '{x_col}' was not found in the dataset."
            )
        if y_col and y_col not in df.columns:
            return VisualizationResult(
                status="NEEDS_CLARIFICATION",
                summary=f"The required column '{y_col}' was not found in the dataset."
            )

        # PIE chart validation & aggregation policy
        if c_type == "PIE":
            if not x_col or x_col not in df.columns:
                return VisualizationResult(
                    status="NEEDS_CLARIFICATION",
                    summary="A pie chart requires a category column. None was found."
                )
            # 1. Category only -> COUNT policy
            if not y_col or y_col not in df.columns:
                grouped = df[x_col].value_counts().reset_index()
                grouped.columns = [x_col, "count"]
                df = grouped
                sel.y_column = "count"
                y_col = "count"
            else:
                # 2. Category + Numeric
                try:
                    df[y_col] = pd.to_numeric(df[y_col], errors="coerce")
                except Exception:
                    pass
                if not pd.api.types.is_numeric_dtype(df[y_col]):
                    return VisualizationResult(
                        status="NEEDS_CLARIFICATION",
                        summary=f"A pie chart requires a category and numeric values. Column '{y_col}' is not numeric. Available numeric columns:\n" + 
                                "\n".join(f"• {c}" for c in num_cols) + "\n\nWhich one would you like to use?",
                        ambiguous_columns=num_cols
                    )
                # Group and aggregate (SUM policy by default, AVG/mean if requested)
                agg_func = "sum"
                if any(w in user_message.lower() for w in ["avg", "average", "mean"]):
                    agg_func = "mean"
                if df[x_col].duplicated().any():
                    df = df.groupby(x_col)[y_col].agg(agg_func).reset_index()

        # BAR / HORIZONTAL_BAR aggregation policy
        elif c_type in ("BAR", "HORIZONTAL_BAR"):
            cat_c = y_col if c_type == "HORIZONTAL_BAR" else x_col
            num_c = x_col if c_type == "HORIZONTAL_BAR" else y_col
            if cat_c and num_c and cat_c in df.columns and num_c in df.columns:
                try:
                    df[num_c] = pd.to_numeric(df[num_c], errors="coerce")
                except Exception:
                    pass
                if pd.api.types.is_numeric_dtype(df[num_c]):
                    agg_func = "sum"
                    if any(w in user_message.lower() for w in ["avg", "average", "mean"]):
                        agg_func = "mean"
                    if df[cat_c].duplicated().any():
                        df = df.groupby(cat_c)[num_c].agg(agg_func).reset_index()

        # HISTOGRAM validation
        elif c_type == "HISTOGRAM":
            col = x_col or y_col
            if not col or col not in df.columns:
                return VisualizationResult(
                    status="NEEDS_CLARIFICATION",
                    summary="A histogram requires a column to plot. None was found."
                )
            try:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            except Exception:
                pass
            if not pd.api.types.is_numeric_dtype(df[col]):
                return VisualizationResult(
                    status="NEEDS_CLARIFICATION",
                    summary=f"A histogram requires a numeric column. Column '{col}' is not numeric. Available numeric columns:\n" + 
                            "\n".join(f"• {c}" for c in num_cols) + "\n\nWhich one would you like to use?",
                    ambiguous_columns=num_cols
                )

        # SCATTER validation
        elif c_type == "SCATTER":
            if not x_col or not y_col or x_col not in df.columns or y_col not in df.columns:
                return VisualizationResult(
                    status="NEEDS_CLARIFICATION",
                    summary="A scatter plot requires both X and Y axis columns. Please specify them."
                )
            for col in (x_col, y_col):
                try:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                except Exception:
                    pass
                if not pd.api.types.is_numeric_dtype(df[col]):
                    return VisualizationResult(
                        status="NEEDS_CLARIFICATION",
                        summary=f"A scatter plot requires 2 numeric columns. Column '{col}' is not numeric. Available numeric columns:\n" + 
                                "\n".join(f"• {c}" for c in num_cols) + "\n\nWhich one would you like to use?",
                        ambiguous_columns=num_cols
                    )

        # LINE validation & ordered axis sorting
        elif c_type == "LINE":
            if not x_col or not y_col or x_col not in df.columns or y_col not in df.columns:
                return VisualizationResult(
                    status="NEEDS_CLARIFICATION",
                    summary="A line chart requires both X and Y axis columns. Please specify them."
                )
            # Sort by X axis to ensure ordered line
            df = df.sort_values(by=x_col)

        # 4. Build Plotly chart
        title     = cls._build_title(sel.chart_type, sel.x_column, sel.y_column, source_table)
        x_label   = (sel.x_column or "").replace("_", " ").title()
        y_label   = (sel.y_column or "").replace("_", " ").title()
        chart_raw = ChartBuilder.build(
            chart_type=sel.chart_type,
            df=df,
            x_column=sel.x_column,
            y_column=sel.y_column,
            title=title,
            x_label=x_label,
            y_label=y_label,
        )
        render_ms = (time.perf_counter() - t_start) * 1000

        # 5. Assemble result
        subtitle = cls._build_subtitle(sel.chart_type, sel.x_column, sel.y_column, source_table)
        summary  = cls._build_summary(
            sel.chart_type, sel.x_column, sel.y_column,
            processed_result.execution.row_count, source_table, source_db
        )

        result = VisualizationResult(
            status="SUCCESS",
            title=title,
            subtitle=subtitle,
            summary=summary,
            chart=VisualizationChart(
                chart_type=chart_raw["chart_type"],
                chart_data=chart_raw["chart_data"],
                layout=chart_raw["layout"],
            ),
            metadata=VisualizationMetadata(
                chart_id=str(uuid.uuid4()),
                dataset_fingerprint=dataset_fp,
                render_time_ms=round(render_ms, 2),
                generated_at=time.time(),
                cache_status="MISS",
                selection_mode=sel.mode,
                selection_confidence=round(sel.confidence, 2),
                selection_reason=sel.reason,
                x_label=x_label,
                y_label=y_label,
                source_table=source_table,
                source_database=source_db,
                row_count=processed_result.execution.row_count,
                column_count=processed_result.execution.column_count,
            ),
        )

        # 6. Store in Visualization Cache and update Session structured state
        if session_id:
            _store_viz_cache(session_id, cache_key, result)
            
            if sel.chart_type == "HORIZONTAL_BAR":
                cat_col = sel.y_column
                num_col = sel.x_column
                orientation = "horizontal"
            elif sel.chart_type == "HISTOGRAM":
                cat_col = None
                num_col = sel.x_column
                orientation = "vertical"
            elif sel.chart_type == "SCATTER":
                cat_col = sel.x_column
                num_col = sel.y_column
                orientation = "vertical"
            else:
                cat_col = sel.x_column
                num_col = sel.y_column
                orientation = "vertical"

            last_viz_payload = {
                "chart_type": sel.chart_type,
                "orientation": orientation,
                "sorting": None,
                "sort_by": None,
                "x_column": sel.x_column,
                "y_column": sel.y_column,
                "cat_col": cat_col,
                "num_col": num_col,
                "original_query": user_message,
                "table": source_table,
                "cache_key": cache_key,
            }
            update_session(session_id, last_visualization=last_viz_payload)

        return result

    @classmethod
    def clear_cache(cls, session_id: str) -> None:
        """Clears all cached visualizations for the given session."""
        _clear_viz_cache_for_session(session_id)

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _build_title(chart_type: str, x_col: Optional[str], y_col: Optional[str], table_name: Optional[str] = None) -> str:
        # Singularize/format table name
        tbl = ""
        if table_name:
            t_clean = table_name.replace("_", " ").lower()
            if t_clean.endswith("s"):
                t_clean = t_clean[:-1]
            tbl = t_clean.title()
        
        y = (y_col or "").replace("_", " ").title()
        x = (x_col or "").replace("_", " ").title()
        
        c_type = chart_type.upper()
        if c_type in ("BOX", "HISTOGRAM"):
            prefix = f"{tbl} " if tbl else ""
            return f"{prefix}{y} Distribution"
            
        if c_type == "PIE":
            if y.lower() in ("count", "quantity"):
                return f"{x} Breakdown"
            return f"{y} Breakdown by {x}"
            
        if c_type in ("BAR", "HORIZONTAL_BAR"):
            if y.lower() in ("count", "quantity"):
                return f"{x} Breakdown"
            return f"{y} by {x}"
            
        if c_type == "LINE":
            return f"{y} Over {x}"
            
        if c_type == "SCATTER":
            return f"{x} vs {y}"
            
        return f"{y} Chart"

    @staticmethod
    def _build_subtitle(chart_type: str, x_col: Optional[str], y_col: Optional[str], table: str) -> str:
        y = (y_col or x_col or "").replace("_", " ").lower()
        x = (x_col or "").replace("_", " ").lower()
        src = table or "the dataset"
        subtitles = {
            "BOX":            f"Statistical distribution of {y} from {src}.",
            "LINE":           f"Trend of {y} over {x} from {src}.",
            "PIE":            f"Breakdown of {y} by {x} from {src}.",
            "HORIZONTAL_BAR": f"Comparison of {y} across {x} from {src}.",
            "BAR":            f"Comparison of {y} by {x} from {src}.",
            "SCATTER":        f"Correlation between {x} and {y} from {src}.",
            "HISTOGRAM":      f"Frequency distribution of {y} from {src}.",
        }
        return subtitles.get(chart_type.upper(), f"Visualization from {src}.")

    @staticmethod
    def _build_summary(
        chart_type: str, x_col: Optional[str], y_col: Optional[str],
        row_count: int, table: str, db: str
    ) -> str:
        if chart_type == "HORIZONTAL_BAR":
            measure_col = x_col
            cat_col = y_col
        else:
            measure_col = y_col
            cat_col = x_col

        measure_sem = _get_semantic_name(measure_col)
        cat_sem = _get_semantic_name(cat_col)
        src_table = table or "the dataset"

        if chart_type == "BOX":
            return f"This box plot shows the statistical distribution of **{measure_sem}** across **{src_table}**."
        elif chart_type == "LINE":
            if cat_sem == "time" or cat_sem == "date":
                return f"This line chart shows how **{measure_sem}** changes over time."
            else:
                return f"This line chart shows the trend of **{measure_sem}** over **{cat_sem}**."
        elif chart_type == "PIE":
            return f"This pie chart shows the breakdown of **{measure_sem}** by **{cat_sem}**."
        elif chart_type in ("BAR", "HORIZONTAL_BAR"):
            return f"This bar chart compares **{measure_sem}** across **{cat_sem}**."
        elif chart_type == "SCATTER":
            x_sem = _get_semantic_name(x_col)
            y_sem = _get_semantic_name(y_col)
            return f"This scatter plot shows the correlation between **{x_sem}** and **{y_sem}**."
        elif chart_type == "HISTOGRAM":
            col_sem = _get_semantic_name(x_col or y_col)
            return f"This histogram shows the frequency distribution of **{col_sem}**."
        
        return f"This chart visualizes the dataset."

    @staticmethod
    def _print_decision_log(
        category_candidate: str,
        measure_candidate: str,
        confidence: float,
        chart_type: str,
        reason: str,
        clarification_needed: bool
    ) -> None:
        # Best-effort diagnostic logging only — must never be able to take
        # down an otherwise-successfully-built chart. Windows consoles
        # default to a codepage (cp1252/"charmap") that can't encode many
        # Unicode characters (e.g. "≤"), and `reason`/candidate strings here
        # can contain arbitrary generated text; a print() raising
        # UnicodeEncodeError previously propagated straight out of chart
        # generation and got the whole result discarded by the caller.
        try:
            sep = "===================================="
            print(f"\n{sep}")
            print("VISUALIZATION DECISION")
            print(sep)
            print(f"Category Candidate:\n{category_candidate}\n")
            print(f"Measure Candidate:\n{measure_candidate}\n")
            print(f"Confidence:\n{confidence:.2f}\n")
            print(f"Decision:\n{chart_type}\n")
            print(f"Reason:\n{reason}\n")
            print(f"Clarification Needed:\n{'Yes' if clarification_needed else 'No'}")
            print(f"{sep}\n")
        except Exception:
            pass
