import re
from typing import List, Dict, Any, Tuple, Optional
from analytics.models import AnalysisRequest
from analytics.helpers import find_target_columns
from agent.result_processing.models import ProcessedResult

# Intent regex patterns
_TOP_N_PATTERN = re.compile(r"\b(?:top|highest|best|first)\s+(\d+)\b", re.IGNORECASE)
_BOTTOM_N_PATTERN = re.compile(r"\b(?:bottom|lowest|worst|last)\s+(\d+)\b", re.IGNORECASE)

# Mapping of keywords to analysis types
_KEYWORD_MAP = [
    (r"\b(average|mean)\b", "AVERAGE"),
    (r"\b(median)\b", "MEDIAN"),
    (r"\b(sum|total)\b", "SUM"),
    (r"\b(max|maximum|highest|earns\s+the\s+most|highest\s+paid)\b", "MAXIMUM"),
    (r"\b(min|minimum|lowest|lowest\s+paid)\b", "MINIMUM"),
    (r"\b(unique\s+count|distinct\s+count|number\s+of\s+unique|how\s+many\s+unique)\b", "UNIQUE_COUNT"),
    (r"\b(duplicate\s+count|how\s+many\s+duplicates|number\s+of\s+duplicates)\b", "DUPLICATE_COUNT"),
    (r"\b(null\s+count|missing\s+count|how\s+many\s+null|how\s+many\s+missing|number\s+of\s+null|number\s+of\s+missing)\b", "NULL_COUNT"),
    (r"\b(count|how\s+many|number\s+of)\b", "COUNT"),
    (r"\b(distribution|frequencies|most\s+common|least\s+common)\b", "DISTRIBUTION"),
    (r"\b(summary|statistics|summary\s+statistics|describe)\b", "BUSINESS_SUMMARY")
]

class AnalyticsParser:
    @staticmethod
    def parse(query: str, result: ProcessedResult) -> AnalysisRequest:
        """
        Parses raw user query and ProcessedResult schema to compile AnalysisRequest.
        Uses synonyms, regex match groups, and maps confidence scores.
        """
        normalized_query = query.lower()
        
        # 1. Parse Analysis Type
        analysis_type = "UNKNOWN_ANALYSIS"
        confidence = "LOW"
        
        # Top N / Bottom N checking
        top_match = _TOP_N_PATTERN.search(normalized_query)
        bottom_match = _BOTTOM_N_PATTERN.search(normalized_query)
        
        limit = None
        if top_match:
            analysis_type = "TOP_N"
            limit = int(top_match.group(1))
            confidence = "HIGH"
        elif bottom_match:
            analysis_type = "BOTTOM_N"
            limit = int(bottom_match.group(1))
            confidence = "HIGH"
        else:
            # Check keyword map
            for pattern, a_type in _KEYWORD_MAP:
                if re.search(pattern, normalized_query):
                    analysis_type = a_type
                    confidence = "HIGH"
                    break
        
        # 2. Parse Target Columns
        columns = result.dataset.columns
        target_cols = find_target_columns(query, columns)
        
        # Handle potential specified column that doesn't exist (e.g. "average bonus_points")
        if not target_cols:
            match = re.search(r"\b(?:average|mean|sum|total|max|maximum|highest|most|min|minimum|lowest|least|top|bottom|by|of)\s+(?:of\s+)?([a-zA-Z0-9_]+)", normalized_query)
            if match:
                potential_col = match.group(1)
                if potential_col not in {"the", "a", "an", "what", "is", "of", "value", "in", "for", "record", "records", "employee", "employees", "dataset", "table"}:
                    target_cols = [potential_col]
        
        # 3. Parse Group By Dimensions
        group_by_cols = []
        by_match = re.search(r"\bby\s+(.+)$", normalized_query)
        if by_match:
            by_clause = by_match.group(1)
            # Find which categorical or datetime columns are in the "by" clause
            categorical_and_dt = result.semantics.categorical_columns + result.semantics.datetime_columns
            for col in categorical_and_dt:
                if col.lower() in by_clause:
                    group_by_cols.append(col)
            
            if group_by_cols:
                analysis_type = "GROUP_BY"
                confidence = "HIGH"
        
        # Handle cases where query had synonyms or in-context matching
        # If columns were matched via synonyms, change confidence to MEDIUM
        if target_cols:
            # Check if any of target_cols are not in actual columns
            invalid_cols = [c for c in target_cols if c not in columns]
            if invalid_cols:
                confidence = "LOW"
            else:
                exact_match = any(col.lower() in normalized_query for col in target_cols)
                if not exact_match:
                    confidence = "MEDIUM"
        
        # 4. Resolve Target Column Fallbacks and Ambiguities
        # If user asks for numeric analysis but no target columns matched or specified:
        # Fall back to compilation of general business statistics on the entire dataset
        numeric_analyses = {"AVERAGE", "MEDIAN", "SUM", "MINIMUM", "MAXIMUM"}
        if analysis_type in numeric_analyses and not target_cols:
            analysis_type = "BUSINESS_SUMMARY"
            confidence = "LOW"
            
        # Deduplicate target and group_by columns
        if target_cols and group_by_cols:
            target_cols = [c for c in target_cols if c not in group_by_cols]
            
        return AnalysisRequest(
            analysis_type=analysis_type,
            target_columns=target_cols,
            group_by=group_by_cols,
            limit=limit,
            confidence=confidence,
            options={}
        )
