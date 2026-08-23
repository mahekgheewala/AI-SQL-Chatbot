"""
Visualization Engine — Parser (Phase 10.4)

Extracts structured VisualizationRequest from a raw user query.

Resolution priority:
  1. Explicit chart type keywords in the query
  2. Column names extracted from the query (exact match → synonym match)
  3. x / y axis derivation from column pair
"""

from __future__ import annotations
import re
from typing import List, Optional, Tuple

from visualization.models import VisualizationRequest


_CHART_PATTERNS: List[Tuple[str, str]] = [
    (r"\bbox\s*(?:plot|chart|graph)?\b|\boutlier\b",                  "BOX"),
    (r"\bhorizontal\s+bar\b",                                         "HORIZONTAL_BAR"),
    (r"\bbar\s*(?:chart|graph|plot)?\b",                              "BAR"),
    (r"\bpie\s*(?:chart|graph|plot)?\b|\bdonut\b",                   "PIE"),
    (r"\bline\s*(?:chart|graph|plot)?\b|\btrend\b|\bover\s+time\b",  "LINE"),
    (r"\bscatter\s*(?:chart|graph|plot)?\b|\bcorrelation\b",          "SCATTER"),
    (r"\bhistogram\b|\bfrequency\b|\bdistribution\b",                  "HISTOGRAM"),
]

# Semantic synonyms for column name resolution
_COLUMN_SYNONYMS: dict[str, List[str]] = {
    "salary":      ["salary", "compensation", "income", "earnings", "wage", "pay", "salaries"],
    "name":        ["name", "employee", "staff", "worker", "person"],
    "department":  ["department", "dept", "division", "team", "group"],
    "hire_date":   ["hire_date", "hired", "join_date", "date_hired", "start_date"],
    "date":        ["date", "time", "month", "year", "period"],
    "id":          ["id", "employee_id", "number"],
    "age":         ["age", "years"],
    "gender":      ["gender", "sex"],
    "country":     ["country", "region", "location", "city"],
    "sales":       ["sales", "revenue", "amount", "total"],
    "price":       ["price", "cost", "rate", "fee"],
    "quantity":    ["quantity", "qty", "count", "units"],
}


def _detect_chart_type(query: str) -> Optional[str]:
    """Returns the first matching chart type keyword or None (AUTO)."""
    query_lower = query.lower()
    for pattern, chart_type in _CHART_PATTERNS:
        if re.search(pattern, query_lower):
            return chart_type
    return None  # AUTO


def _resolve_columns(query: str, available_columns: List[str]) -> List[str]:
    """
    Finds which available columns are referenced in the query.
    Uses exact match first, then synonym overlap.
    """
    query_lower = query.lower()
    query_tokens = set(re.split(r"[^a-zA-Z0-9_]+", query_lower))
    matched: List[str] = []

    # 1. Exact match
    for col in available_columns:
        col_lower = col.lower()
        if col_lower in query_lower or col_lower in query_tokens:
            matched.append(col)

    # 2. Synonym match
    for col in available_columns:
        if col in matched:
            continue
        col_lower = col.lower()
        for prime, syns in _COLUMN_SYNONYMS.items():
            if prime in col_lower or any(p in col_lower for p in prime.split("_")):
                if any(s in query_tokens or s in query_lower for s in syns):
                    matched.append(col)
                    break

    return list(dict.fromkeys(matched))


def _derive_axes(
    target_columns: List[str],
    numeric_columns: List[str],
    categorical_columns: List[str],
    datetime_columns: List[str],
) -> Tuple[Optional[str], Optional[str]]:
    """
    Heuristically assigns x and y from the resolved target columns.
    Priority: categorical/datetime → x; numeric → y.
    """
    dimension_cols = [c for c in target_columns if c in categorical_columns or c in datetime_columns]
    metric_cols    = [c for c in target_columns if c in numeric_columns]

    x_col = dimension_cols[0] if dimension_cols else (target_columns[0] if target_columns else None)
    y_col = metric_cols[0]    if metric_cols    else (target_columns[1] if len(target_columns) > 1 else None)

    return x_col, y_col


class VisualizationParser:
    """
    Stateless parser that converts a natural language query into a
    structured VisualizationRequest.
    """

    @staticmethod
    def parse(
        user_message: str,
        available_columns: List[str],
        numeric_columns: List[str],
        categorical_columns: List[str],
        datetime_columns: List[str],
    ) -> VisualizationRequest:
        """
        Parses the user query and returns a VisualizationRequest.

        Args:
            user_message:         Raw user query string.
            available_columns:    All columns in the resolved source dataset.
            numeric_columns:      Columns classified as numeric.
            categorical_columns:  Columns classified as categorical.
            datetime_columns:     Columns classified as datetime.

        Returns:
            VisualizationRequest with best-effort chart type, axes, and columns.
        """
        chart_type = _detect_chart_type(user_message)
        target_columns = _resolve_columns(user_message, available_columns)

        # If no columns resolved, default to all available (let selection engine decide)
        if not target_columns:
            target_columns = list(available_columns)

        x_col, y_col = _derive_axes(
            target_columns, numeric_columns, categorical_columns, datetime_columns
        )

        # Build a title from query context
        title = _build_title(user_message, chart_type, x_col, y_col)

        return VisualizationRequest(
            chart_type=chart_type,
            x_column=x_col,
            y_column=y_col,
            target_columns=target_columns,
            title=title,
            raw_query=user_message,
        )


def _build_title(query: str, chart_type: Optional[str], x_col: Optional[str], y_col: Optional[str]) -> str:
    """Generates a human-readable chart title from the query context."""
    if y_col and x_col:
        y_label = y_col.replace("_", " ").title()
        x_label = x_col.replace("_", " ").title()
        if chart_type == "BOX":
            return f"Distribution of {y_label}"
        if chart_type == "PIE":
            return f"{y_label} by {x_label}"
        if chart_type in ("LINE",):
            return f"{y_label} Over {x_label}"
        return f"{y_label} by {x_label}"
    if y_col:
        return y_col.replace("_", " ").title()
    # Strip chart keywords and title-case the remainder
    stripped = re.sub(
        r"\b(plot|chart|graph|bar|pie|line|scatter|histogram|box|show|me|the|of|by|a|an|draw)\b",
        "", query, flags=re.IGNORECASE
    ).strip()
    return stripped.title() if stripped else "Visualization"
