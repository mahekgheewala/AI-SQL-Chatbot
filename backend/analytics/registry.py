from typing import Dict, Optional
from analytics.models import AnalysisRequest
from analytics.analyzers import (
    BaseAnalyzer,
    NumericSummaryAnalyzer,
    CountAnalyzer,
    TopBottomAnalyzer,
    GroupByAnalyzer,
    DistributionAnalyzer,
    BusinessSummaryAnalyzer
)

class AnalyzerRegistry:
    def __init__(self):
        self._registry: Dict[str, BaseAnalyzer] = {}
        
        # Instantiate analyzers
        num_summary = NumericSummaryAnalyzer()
        count_analyzer = CountAnalyzer()
        top_bottom = TopBottomAnalyzer()
        group_by = GroupByAnalyzer()
        dist_analyzer = DistributionAnalyzer()
        biz_summary = BusinessSummaryAnalyzer()
        
        # Register numeric summary aggregates
        self.register("AVERAGE", num_summary)
        self.register("MEDIAN", num_summary)
        self.register("SUM", num_summary)
        self.register("MINIMUM", num_summary)
        self.register("MAXIMUM", num_summary)
        
        # Register count aggregates
        self.register("COUNT", count_analyzer)
        self.register("UNIQUE_COUNT", count_analyzer)
        self.register("DUPLICATE_COUNT", count_analyzer)
        self.register("NULL_COUNT", count_analyzer)
        
        # Register sorting list aggregates
        self.register("TOP_N", top_bottom)
        self.register("BOTTOM_N", top_bottom)
        
        # Register structural analyzers
        self.register("GROUP_BY", group_by)
        self.register("DISTRIBUTION", dist_analyzer)
        self.register("BUSINESS_SUMMARY", biz_summary)
        
    def register(self, analysis_type: str, analyzer: BaseAnalyzer) -> None:
        self._registry[analysis_type.upper()] = analyzer
        
    def get_analyzer(self, analysis_type: str) -> Optional[BaseAnalyzer]:
        return self._registry.get(analysis_type.upper())
