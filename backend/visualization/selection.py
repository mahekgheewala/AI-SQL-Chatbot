"""
Visualization Engine — Chart Selection (Phase 10.4)

Deterministic chart type selection with confidence scoring.

Rules (in priority order):
  1. BOX         — 1 numeric column + distribution keywords
  2. LINE        — 1 datetime + 1 numeric column
  3. PIE         — 1 categorical (low cardinality ≤ 6) + 1 numeric
  4. HORIZONTAL_BAR — 1 categorical (long labels > 12 chars avg) + 1 numeric
  5. BAR         — 1 categorical + 1 numeric (general case)
  6. SCATTER     — 2 numeric columns
  7. HISTOGRAM   — 1 numeric column only

When the user explicitly specifies a chart type, that type is returned
immediately with confidence=1.0 and mode="MANUAL".
"""

from __future__ import annotations
import re
from typing import List, Optional, Tuple, Dict, Any
import pandas as pd

from visualization.models import VisualizationRequest


# ─── Types ────────────────────────────────────────────────────────────────────

class SelectionResult:
    """Returned by ChartSelectionEngine.select()."""
    def __init__(
        self,
        chart_type: str,
        x_column: Optional[str],
        y_column: Optional[str],
        confidence: float,
        reason: str,
        mode: str,  # "AUTO" | "MANUAL"
        ambiguous_columns: Optional[List[str]] = None,
    ):
        self.chart_type  = chart_type
        self.x_column    = x_column
        self.y_column    = y_column
        self.confidence  = confidence
        self.reason      = reason
        self.mode        = mode
        self.ambiguous_columns = ambiguous_columns or []


# ─── Box Plot trigger keywords ────────────────────────────────────────────────

_BOX_KEYWORDS = re.compile(
    r"\b(box\s*(?:plot|chart)?|distribution|outlier|spread|quartile|median)\b",
    re.IGNORECASE,
)


# ─── Semantic Axis Priority Definitions ───────────────────────────────────────

_CATEGORY_PRIORITY = ["name", "title", "department", "product", "category"]
_MEASURE_PRIORITY = ["salary", "revenue", "sales", "price", "amount", "bonus", "score"]


def _score_category_col(col: str, query: str) -> int:
    col_lower = col.lower()
    if col_lower in query.lower():
        return 100
    for idx, p in enumerate(_CATEGORY_PRIORITY):
        if p in col_lower:
            return 90 - idx
    if col_lower == "id" or col_lower.endswith("_id") or col_lower.startswith("id_"):
        return 0
    return 10


def _score_numeric_col(col: str, query: str) -> int:
    col_lower = col.lower()
    if col_lower in query.lower():
        return 100
    for idx, p in enumerate(_MEASURE_PRIORITY):
        if p in col_lower:
            return 90 - idx
    if col_lower == "id" or col_lower.endswith("_id") or col_lower.startswith("id_"):
        return 0
    return 10


def _rank_category_cols(cols: List[str], query: str) -> List[str]:
    return sorted(cols, key=lambda c: _score_category_col(c, query), reverse=True)


def _rank_numeric_cols(cols: List[str], query: str) -> List[str]:
    return sorted(cols, key=lambda c: _score_numeric_col(c, query), reverse=True)


class ChartSelectionEngine:
    """
    Stateless engine that applies deterministic rules to select the best
    chart type for a given dataset profile.
    """

    @staticmethod
    def select(
        request: VisualizationRequest,
        df: pd.DataFrame,
        numeric_columns: List[str],
        categorical_columns: List[str],
        datetime_columns: List[str],
    ) -> SelectionResult:
        """
        Returns a SelectionResult with the chosen chart type, axes,
        confidence score (0–1), and human-readable reason string.
        """
        raw_query = request.raw_query

        # ── 0. Manual override ────────────────────────────────────────────────
        if request.chart_type is not None:
            return SelectionResult(
                chart_type=request.chart_type,
                x_column=request.x_column,
                y_column=request.y_column,
                confidence=1.0,
                reason=f"User explicitly requested {request.chart_type} chart.",
                mode="MANUAL",
            )

        # Scope to the columns in the request
        target_cols = list(request.target_columns) if request.target_columns else list(df.columns)
        
        # If no categorical/datetime columns are in target_cols, but they exist in the dataframe, append them
        cat_cols  = [c for c in target_cols if c in categorical_columns]
        dt_cols   = [c for c in target_cols if c in datetime_columns]
        if not cat_cols and not dt_cols:
            extra_cats = [c for c in df.columns if c in categorical_columns and c not in target_cols]
            extra_dts  = [c for c in df.columns if c in datetime_columns and c not in target_cols]
            if extra_cats:
                target_cols.append(extra_cats[0])
            elif extra_dts:
                target_cols.append(extra_dts[0])

        num_cols  = [c for c in target_cols if c in numeric_columns]
        cat_cols  = [c for c in target_cols if c in categorical_columns]
        dt_cols   = [c for c in target_cols if c in datetime_columns]

        # Apply semantic priority ranking
        num_cols = _rank_numeric_cols(num_cols, raw_query)
        cat_cols = _rank_category_cols(cat_cols, raw_query)

        # ── Ambiguity-First Detection ─────────────────────────────────────────
        # Ensure we do not check ambiguity if the user specified columns manually.
        if raw_query and not request.x_column and not request.y_column:
            # 1. No numeric columns at all
            if not numeric_columns:
                return SelectionResult(
                    chart_type="CLARIFICATION",
                    x_column=None,
                    y_column=None,
                    confidence=0.0,
                    reason="No numeric columns found in the dataset to visualize.",
                    mode="AUTO",
                    ambiguous_columns=[],
                )

            # 2. Check numeric ambiguity (excluding ID fields)
            valid_num_measures = [
                c for c in num_cols 
                if not (c.lower() == "id" or c.lower().endswith("_id") or c.lower().startswith("id_"))
            ]
            explicit_num_mentioned = [c for c in valid_num_measures if c.lower() in raw_query.lower()]
            
            if len(valid_num_measures) > 1 and not explicit_num_mentioned:
                return SelectionResult(
                    chart_type="CLARIFICATION",
                    x_column=None,
                    y_column=None,
                    confidence=0.0,
                    reason=f"Ambiguous numeric measures: {', '.join(valid_num_measures)}",
                    mode="AUTO",
                    ambiguous_columns=valid_num_measures,
                )

            # 3. Check categorical ambiguity (excluding ID fields)
            valid_cat_cols = [
                c for c in cat_cols 
                if not (c.lower() == "id" or c.lower().endswith("_id") or c.lower().startswith("id_"))
            ]
            explicit_cat_mentioned = [c for c in valid_cat_cols if c.lower() in raw_query.lower()]
            
            if len(valid_cat_cols) > 1 and not explicit_cat_mentioned:
                scores = {c: _score_category_col(c, raw_query) for c in valid_cat_cols}
                max_score = max(scores.values())
                top_cats = [c for c, score in scores.items() if score == max_score]
                if len(top_cats) > 1:
                    return SelectionResult(
                        chart_type="CLARIFICATION",
                        x_column=None,
                        y_column=None,
                        confidence=0.0,
                        reason=f"Ambiguous category columns: {', '.join(top_cats)}",
                        mode="AUTO",
                        ambiguous_columns=top_cats,
                    )

        # ── 1. BOX PLOT — distribution / outlier query ───────────────────────
        if num_cols and _BOX_KEYWORDS.search(raw_query):
            # Only trigger box plot automatically if explicit box keywords match (not just general "distribution")
            if any(w in raw_query.lower() for w in ["box", "outlier", "spread", "quartile", "median"]):
                return SelectionResult(
                    chart_type="BOX",
                    x_column=cat_cols[0] if cat_cols else None,
                    y_column=num_cols[0],
                    confidence=0.95,
                    reason="Distribution / box plot keyword detected with numeric column.",
                    mode="AUTO",
                )

        # ── 2. LINE CHART — time series ───────────────────────────────────────
        if dt_cols and num_cols:
            return SelectionResult(
                chart_type="LINE",
                x_column=dt_cols[0],
                y_column=num_cols[0],
                confidence=0.95,
                reason="1 datetime column + 1 numeric column — time series.",
                mode="AUTO",
            )

        # ── 3. PIE CHART — low-cardinality categorical + numeric ──────────────
        if cat_cols and num_cols:
            cat_col = cat_cols[0]
            cardinality = df[cat_col].nunique() if cat_col in df.columns else 99
            if 2 <= cardinality <= 6:
                return SelectionResult(
                    chart_type="PIE",
                    x_column=cat_col,
                    y_column=num_cols[0],
                    confidence=0.85,
                    reason=f"Low-cardinality category ({cardinality} unique values ≤ 6) + 1 numeric measure.",
                    mode="AUTO",
                )

        # ── 4. HORIZONTAL BAR — long category labels ──────────────────────────
        if cat_cols and num_cols:
            cat_col = cat_cols[0]
            if cat_col in df.columns:
                avg_label_len = df[cat_col].astype(str).str.len().mean()
                if avg_label_len > 12:
                    return SelectionResult(
                        chart_type="HORIZONTAL_BAR",
                        x_column=num_cols[0],
                        y_column=cat_col,
                        confidence=0.92,
                        reason=f"Identifier + Numeric measure (average labels {avg_label_len:.0f} chars > 12) — horizontal layout.",
                        mode="AUTO",
                    )

        # ── 5. BAR CHART — general categorical + numeric ──────────────────────
        if cat_cols and num_cols:
            return SelectionResult(
                chart_type="BAR",
                x_column=cat_cols[0],
                y_column=num_cols[0],
                confidence=0.90,
                reason="Identifier column + Numeric measure detected.",
                mode="AUTO",
            )

        # ── 6. SCATTER PLOT — two numeric columns ─────────────────────────────
        if len(num_cols) >= 2:
            return SelectionResult(
                chart_type="SCATTER",
                x_column=num_cols[0],
                y_column=num_cols[1],
                confidence=0.90,
                reason="2 numeric columns — scatter plot shows correlation.",
                mode="AUTO",
            )

        # ── 7. HISTOGRAM — single numeric column ──────────────────────────────
        has_hist_intent = any(w in raw_query.lower() for w in ["distribution", "frequency", "histogram"])
        if num_cols and (not cat_cols or has_hist_intent):
            col_target = num_cols[0]
            reason_str = "Single numeric column and no identifier/category exists."
            if has_hist_intent:
                reason_str = "User explicitly asked for distribution/frequency/histogram."
            return SelectionResult(
                chart_type="HISTOGRAM",
                x_column=col_target,
                y_column=None,
                confidence=0.85,
                reason=reason_str,
                mode="AUTO",
            )

        # ── Fallback: bar with first two columns ──────────────────────────────
        cols = list(df.columns)
        return SelectionResult(
            chart_type="BAR",
            x_column=cols[0] if cols else None,
            y_column=cols[1] if len(cols) > 1 else cols[0] if cols else None,
            confidence=0.50,
            reason="No strong signal — defaulting to bar chart with first two columns.",
            mode="AUTO",
        )
