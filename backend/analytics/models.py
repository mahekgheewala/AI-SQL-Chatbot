from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class AnalysisRequest(BaseModel):
    analysis_type: str                  # e.g. "AVERAGE", "MEDIAN", "MINIMUM", "MAXIMUM", "SUM", "COUNT", "GROUP_BY", "TOP_N", "DISTRIBUTION", "UNKNOWN_ANALYSIS"
    target_columns: List[str] = Field(default_factory=list)
    group_by: List[str] = Field(default_factory=list)
    limit: Optional[int] = None
    filters: List[Dict[str, Any]] = Field(default_factory=list)
    confidence: str = "HIGH"            # "HIGH" | "MEDIUM" | "LOW"
    options: Dict[str, Any] = Field(default_factory=dict)

class AnalyticsResult(BaseModel):
    analysis_type: str
    success: bool
    confidence: str
    title: str
    summary: str
    metrics: Dict[str, Any] = Field(default_factory=dict)
    tables: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    execution_time_ms: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)
