"""
Visualization Engine — Phase 10.4

Provides automated chart generation from processed SQL datasets.

Components:
  - VisualizationParser   : Extracts chart intent and column targets from user query
  - ChartSelectionEngine  : Deterministic chart type selection with confidence scoring
  - ChartBuilder          : Plotly graph construction and static image export
  - VisualizationEngine   : Orchestration, dual-cache, and backend logging
"""

from visualization.engine import VisualizationEngine
from visualization.models import VisualizationRequest, VisualizationResult

__all__ = [
    "VisualizationEngine",
    "VisualizationRequest",
    "VisualizationResult",
]
