"""
Visualization Engine — Models (Phase 10.4)

Defines the complete data contract for visualization requests and results.
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional


# ─── Request Model ────────────────────────────────────────────────────────────

class VisualizationRequest(BaseModel):
    """
    Parsed from the user's natural language query.
    Describes what the user wants to see and how.
    """
    # Chart selection
    chart_type: Optional[str] = None  # None = AUTO, else e.g. "BAR", "PIE", "LINE", "SCATTER", "HISTOGRAM", "BOX", "HORIZONTAL_BAR"

    # Axis/column targeting
    x_column: Optional[str] = None
    y_column: Optional[str] = None
    target_columns: List[str] = Field(default_factory=list)   # Direct column names extracted
    group_by: List[str] = Field(default_factory=list)         # Grouping dimension columns

    # Presentation
    title: Optional[str] = None
    confidence: float = 0.0
    raw_query: str = ""


# ─── Response Sub-Models ─────────────────────────────────────────────────────

class VisualizationChart(BaseModel):
    """
    The Plotly-ready chart payload.
    Frontend consumes `chart_data` + `layout` directly via Plotly.js.
    """
    chart_type: str                                         # e.g. "BAR", "PIE", "LINE"
    chart_data: List[Dict[str, Any]] = Field(default_factory=list)   # List of Plotly trace dicts
    layout: Dict[str, Any] = Field(default_factory=dict)   # Plotly layout config


class VisualizationMetadata(BaseModel):
    """
    Rich metadata enabling dashboard widgets, chart history, and cache auditing.
    """
    chart_id: str = ""                    # Unique stable hash for this chart config
    dataset_fingerprint: str = ""         # SHA-256 of source SQL / dataset
    render_time_ms: float = 0.0           # Time to build this chart
    generated_at: float = 0.0            # Epoch timestamp of generation
    cache_status: str = "MISS"           # "HIT" | "MISS"

    # Auto-selection audit
    selection_mode: str = "AUTO"         # "AUTO" | "MANUAL"
    selection_confidence: float = 0.0
    selection_reason: str = ""

    # Axis labels
    x_label: str = ""
    y_label: str = ""

    # Source information
    source_table: str = ""
    source_database: str = ""
    row_count: int = 0
    column_count: int = 0


class VisualizationResult(BaseModel):
    """
    The complete frontend contract.
    Frontend renders title + subtitle above the chart, and uses metadata for
    download / save / dashboard widget flows.
    """
    status: str = "SUCCESS"  # "SUCCESS" | "NEEDS_CLARIFICATION" | "ERROR"
    ambiguous_columns: List[str] = Field(default_factory=list)

    # Human-readable presentation layer
    title: str = ""
    subtitle: str = ""
    summary: str = ""

    # Plotly chart payload
    chart: Optional[VisualizationChart] = None

    # Audit & cache metadata
    metadata: Optional[VisualizationMetadata] = None
