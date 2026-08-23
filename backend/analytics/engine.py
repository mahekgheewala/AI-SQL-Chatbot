import time
import logging
from typing import Dict, Optional, List, Any
from analytics.models import AnalysisRequest, AnalyticsResult
from analytics.parser import AnalyticsParser
from analytics.registry import AnalyzerRegistry
from agent.result_processing.models import ProcessedResult
from utils.logging_config import logger_query, user_id_var

# Central TTL Configuration value (in seconds)
ANALYTICS_CACHE_TTL = 900

# Global registry
_REGISTRY = AnalyzerRegistry()

class SourceCacheEntry:
    def __init__(self, result: ProcessedResult, database: str, source_tables: List[str], sql_fingerprint: str, row_count: int, execution_mode: str):
        self.result = result
        self.database = database
        self.source_tables = source_tables
        self.sql_fingerprint = sql_fingerprint
        self.row_count = row_count
        self.execution_mode = execution_mode
        self.timestamp = time.time()

# Independent cache layers mapped by session_id (namespaced by user_id for isolation)
_SOURCE_CACHE: Dict[str, SourceCacheEntry] = {}
_RESULT_CACHE: Dict[str, Dict[tuple, AnalyticsResult]] = {}

def _cache_key(session_id: str, user_id: Optional[int] = None) -> Optional[str]:
    """Build a composite cache key from user_id + session_id (multi-user isolation).

    Falls back to the session_id alone when no user context is available.
    """
    if not session_id:
        return None
    uid = user_id
    if uid is None:
        try:
            uid = user_id_var.get()
        except Exception:
            uid = None
    return f"{uid}:{session_id}" if uid else session_id

class AnalyticsEngine:
    @staticmethod
    def get_source_cache_entry(session_id: str, user_id: Optional[int] = None) -> Optional[SourceCacheEntry]:
        """Retrieves the full SourceCacheEntry structure for a session, checking TTL."""
        key = _cache_key(session_id, user_id)
        if not key or key not in _SOURCE_CACHE:
            return None
            
        entry = _SOURCE_CACHE[key]
        if time.time() - entry.timestamp > ANALYTICS_CACHE_TTL:
            # Clear expired cache
            AnalyticsEngine.invalidate_source_cache(session_id, reason="CACHE_EXPIRED", user_id=user_id)
            return None
            
        return entry

    @staticmethod
    def get_source_cache(session_id: str, user_id: Optional[int] = None) -> Optional[ProcessedResult]:
        """Retrieves the cached unaggregated Source Dataset for a session, checking TTL."""
        entry = AnalyticsEngine.get_source_cache_entry(session_id, user_id)
        if not entry:
            return None
        return entry.result

    @staticmethod
    def get_cached_result(session_id: str, user_id: Optional[int] = None) -> Optional[ProcessedResult]:
        """Compatibility alias for get_source_cache."""
        return AnalyticsEngine.get_source_cache(session_id, user_id)

    @staticmethod
    def cache_source(session_id: str, result: ProcessedResult, source_tables: List[str], db_name: str, sql_fingerprint: str, execution_mode: str, user_id: Optional[int] = None) -> None:
        """Caches the unaggregated ProcessedResult source dataset and evicts the session's analytical calculations cache."""
        key = _cache_key(session_id, user_id)
        if key and result:
            entry = SourceCacheEntry(
                result=result,
                database=db_name,
                source_tables=source_tables,
                sql_fingerprint=sql_fingerprint,
                row_count=result.execution.row_count,
                execution_mode=execution_mode
            )
            _SOURCE_CACHE[key] = entry
            AnalyticsEngine.clear_result_cache(session_id, user_id)
            
            print("\n====================================")
            print("ANALYTICS CACHE")
            print("====================================")
            print("Source Dataset Stored")
            print("====================================\n")

    @staticmethod
    def invalidate_source_cache(session_id: str, reason: str, new_db: Optional[str] = None, user_id: Optional[int] = None) -> None:
        """Evicts the source dataset cache and prints/logs the explicit invalidation reason."""
        key = _cache_key(session_id, user_id)
        if not key or key not in _SOURCE_CACHE:
            return
            
        entry = _SOURCE_CACHE[key]
        prev_db = entry.database
        if reason == "DATABASE_SWITCH" and new_db and prev_db == new_db:
            return
        prev_dataset = ", ".join(entry.source_tables) if entry.source_tables else "None"
        
        print("\n====================================")
        print("ANALYTICS CACHE")
        print("====================================")
        print("Source Cache")
        print("INVALIDATED")
        print(f"Reason:                     {reason}")
        print(f"Previous Database:          {prev_db}")
        print(f"Previous Dataset:           {prev_dataset}")
        if reason == "DATABASE_SWITCH" and new_db:
            print(f"New Database:               {new_db}")
        print("====================================\n")
        
        # Log structured invalidation event
        try:
            logger_query.info(
                "Analytics cache invalidated",
                extra={
                    "category": "query",
                    "operation_type": "ANALYTICS_CACHE_INVALIDATE",
                    "invalidation_reason": reason,
                    "previous_dataset": entry.source_tables,
                    "previous_database": prev_db,
                    "new_database": new_db or prev_db,
                    "session_id": session_id
                }
            )
        except Exception:
            pass
            
        del _SOURCE_CACHE[key]
        AnalyticsEngine.clear_result_cache(session_id, user_id)

    @staticmethod
    def clear_source_cache(session_id: str, user_id: Optional[int] = None) -> None:
        """Compatibility alias forwarding to invalidate_source_cache."""
        AnalyticsEngine.invalidate_source_cache(session_id, reason="SESSION_RESET", user_id=user_id)

    @staticmethod
    def get_result_cache(session_id: str, fingerprint: str, analysis_type: str, target_columns: List[str], group_by: List[str], user_id: Optional[int] = None) -> Optional[AnalyticsResult]:
        """Retrieves a cached analytical result derived from the exact same source dataset fingerprint and parameters."""
        key = _cache_key(session_id, user_id)
        if not key or key not in _RESULT_CACHE:
            return None
        cache_key = (fingerprint, analysis_type, tuple(target_columns), tuple(group_by))
        return _RESULT_CACHE[key].get(cache_key)

    @staticmethod
    def cache_result(session_id: str, fingerprint: str, request: AnalysisRequest, result: AnalyticsResult, user_id: Optional[int] = None) -> None:
        """Caches an analytical result derived from the given source dataset fingerprint."""
        key = _cache_key(session_id, user_id)
        if key and fingerprint and request and result:
            if key not in _RESULT_CACHE:
                _RESULT_CACHE[key] = {}
            cache_key = (fingerprint, request.analysis_type, tuple(request.target_columns), tuple(request.group_by))
            _RESULT_CACHE[key][cache_key] = result

    @staticmethod
    def clear_result_cache(session_id: str, user_id: Optional[int] = None) -> None:
        """Clears the session's analytical calculations cache."""
        key = _cache_key(session_id, user_id)
        if key and key in _RESULT_CACHE:
            del _RESULT_CACHE[key]

    @staticmethod
    def clear_cache(session_id: str, user_id: Optional[int] = None) -> None:
        """Clears both source dataset and analytics result cache layers for a session."""
        AnalyticsEngine.invalidate_source_cache(session_id, reason="SESSION_RESET", user_id=user_id)

    @staticmethod
    def analyze(processed_result: ProcessedResult, user_message: str, session_id: Optional[str] = None) -> AnalyticsResult:
        """
        Coordinates the analytics parsing, result cache lookup, analyzer execution, and logging traces.
        Works strictly on unaggregated source datasets.
        """
        start_time = time.perf_counter()
        warnings = []
        
        # 1. Parse the user query against the schema metadata
        try:
            request = AnalyticsParser.parse(user_message, processed_result)
        except Exception as e:
            request = AnalysisRequest(
                analysis_type="UNKNOWN_ANALYSIS",
                confidence="LOW"
            )
            warnings.append(f"Analytics query parsing failed: {e}")
            
        analysis_type = request.analysis_type
        target_columns = request.target_columns
        group_by = request.group_by
        
        # Check empty dataset
        df = processed_result.dataset.dataframe
        if df is None or df.empty:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return AnalyticsResult(
                analysis_type=analysis_type,
                success=False,
                confidence=request.confidence,
                title="Empty Dataset",
                summary="The selected dataset contains no rows, so no analytical calculation can be performed.",
                metrics={},
                tables=[],
                warnings=["The selected dataset contains no rows, so no analytical calculation can be performed."],
                execution_time_ms=duration_ms,
                metadata={}
            )

        # Check if the table has no numeric columns and the user query is asking for numeric analysis
        import re
        is_numeric_query = any(re.search(pat, user_message.lower()) for pat, _ in [
            (r"\b(average|mean)\b", "AVERAGE"),
            (r"\b(median)\b", "MEDIAN"),
            (r"\b(sum|total)\b", "SUM"),
            (r"\b(max|maximum|highest)\b", "MAXIMUM"),
            (r"\b(min|minimum|lowest)\b", "MINIMUM")
        ])
        if is_numeric_query and (not processed_result.semantics.numeric_columns):
            duration_ms = (time.perf_counter() - start_time) * 1000
            return AnalyticsResult(
                analysis_type=analysis_type,
                success=False,
                confidence=request.confidence,
                title="No Numeric Columns",
                summary="The selected table does not contain any numeric columns that can be used for analytical calculations.",
                metrics={},
                tables=[],
                warnings=["The selected table does not contain any numeric columns that can be used for analytical calculations."],
                execution_time_ms=duration_ms,
                metadata={}
            )

        # Check all-NULL column for target columns
        if target_columns:
            for col in target_columns:
                if col in df.columns:
                    if df[col].isnull().all():
                        duration_ms = (time.perf_counter() - start_time) * 1000
                        return AnalyticsResult(
                            analysis_type=analysis_type,
                            success=False,
                            confidence=request.confidence,
                            title="No Usable Numeric Values",
                            summary="The selected column contains no usable numeric values, so I can't calculate this analysis.",
                            metrics={},
                            tables=[],
                            warnings=["The selected column contains no usable numeric values, so I can't calculate this analysis."],
                            execution_time_ms=duration_ms,
                            metadata={}
                        )

        # Determine SQL fingerprint of the source query
        from analytics.helpers import get_sql_fingerprint
        sql = processed_result.execution.sql or ""
        fingerprint = get_sql_fingerprint(sql)
        
        # 2. Check cache hits
        source_cache_hit = False
        analytics_cache_hit = False
        
        source_entry = _SOURCE_CACHE.get(_cache_key(session_id)) if session_id else None
        if source_entry and source_entry.result == processed_result:
            source_cache_hit = True
            
        cached_res = None
        if session_id and fingerprint:
            cached_res = AnalyticsEngine.get_result_cache(session_id, fingerprint, analysis_type, target_columns, group_by)
            
        if cached_res:
            analytics_cache_hit = True
            duration_ms = (time.perf_counter() - start_time) * 1000
            
            print("\n====================================")
            print("ANALYTICS CACHE")
            print("====================================")
            print("Analytics Result Cache")
            print("HIT")
            print("====================================\n")
            
            # Log structured JSON event
            AnalyticsEngine._log_json_event(
                session_id=session_id,
                analysis_type=analysis_type,
                target_columns=target_columns,
                confidence=request.confidence,
                duration_ms=duration_ms,
                rows_processed=processed_result.execution.row_count,
                analyzer_name=cached_res.metadata.get("analyzer_name", "None"),
                success=True,
                warnings=cached_res.warnings,
                source_cache_hit=source_cache_hit,
                analytics_cache_hit=analytics_cache_hit,
                source_tables=source_entry.source_tables if source_entry else [],
                exec_mode=source_entry.execution_mode if source_entry else "STANDARD_SQL",
                invalidation_reason=""
            )
            return cached_res
            
        # 3. Cache Miss: Execute calculation
        print("\n====================================")
        print("ANALYTICS CACHE")
        print("====================================")
        print("Analytics Result Cache")
        print("MISS")
        print("v")
        print("Analytics Result Calculated")
        print("v")
        print("Analytics Result Cached")
        print("====================================\n")

        analyzer_name = "None"
        success = False
        result_title = "Analysis Error"
        result_summary = "An unexpected error occurred during analytics computation."
        metrics = {}
        tables = []
        metadata = {}
        
        if analysis_type == "UNKNOWN_ANALYSIS":
            warnings.append("Unsupported analytics request.")
            result_summary = "Unsupported analytics request."
            analyzer_name = "None"
        else:
            analyzer = _REGISTRY.get_analyzer(analysis_type)
            if not analyzer:
                warnings.append(f"No analyzer registered for type '{analysis_type}'.")
                result_summary = f"No analyzer registered for type '{analysis_type}'."
            else:
                analyzer_name = analyzer.__class__.__name__
                try:
                    res = analyzer.analyze(request, processed_result)
                    success = res.success
                    result_title = res.title
                    result_summary = res.summary
                    metrics = res.metrics
                    tables = res.tables
                    if res.warnings:
                        warnings.extend(res.warnings)
                    metadata = res.metadata
                except Exception as e:
                    success = False
                    warnings.append(f"Analyzer execution failed: {e}")
                    result_summary = f"Analyzer execution failed: {e}"
                    
        duration_ms = (time.perf_counter() - start_time) * 1000
        
        # Build AnalyticsResult object
        analytics_result = AnalyticsResult(
            analysis_type=analysis_type,
            success=success,
            confidence=request.confidence,
            title=result_title,
            summary=result_summary,
            metrics=metrics,
            tables=tables,
            warnings=warnings,
            execution_time_ms=duration_ms,
            metadata={**metadata, "analyzer_name": analyzer_name}
        )
        
        # Cache Result
        if session_id and fingerprint and success:
            AnalyticsEngine.cache_result(session_id, fingerprint, request, analytics_result)
            
        # Log structured JSON event
        AnalyticsEngine._log_json_event(
            session_id=session_id,
            analysis_type=analysis_type,
            target_columns=target_columns,
            confidence=request.confidence,
            duration_ms=duration_ms,
            rows_processed=processed_result.execution.row_count,
            analyzer_name=analyzer_name,
            success=success,
            warnings=warnings,
            source_cache_hit=source_cache_hit,
            analytics_cache_hit=analytics_cache_hit,
            source_tables=source_entry.source_tables if source_entry else [],
            exec_mode=source_entry.execution_mode if source_entry else "STANDARD_SQL",
            invalidation_reason=""
        )
        
        return analytics_result

    @staticmethod
    def _log_json_event(session_id, analysis_type, target_columns, confidence, duration_ms, rows_processed, analyzer_name, success, warnings, source_cache_hit, analytics_cache_hit, source_tables, exec_mode, invalidation_reason):
        try:
            logger_query.info(
                "Analytics engine execution completed",
                extra={
                    "category": "query",
                    "operation_type": "ANALYTICS_ENGINE",
                    "analysis_type": analysis_type,
                    "target_columns": target_columns,
                    "confidence": confidence,
                    "execution_time_ms": duration_ms,
                    "rows_processed": rows_processed,
                    "analyzer_name": analyzer_name,
                    "status": "SUCCESS" if success else "FAILED",
                    "warnings": warnings,
                    "source_cache_hit": source_cache_hit,
                    "analytics_cache_hit": analytics_cache_hit,
                    "source_dataset_name": source_tables[0] if source_tables else None,
                    "source_tables": source_tables,
                    "source_row_count": rows_processed,
                    "execution_mode": exec_mode,
                    "cache_invalidation_reason": invalidation_reason,
                    "cache_build_time_ms": duration_ms if not source_cache_hit else 0.0,
                    "session_id": session_id
                }
            )
        except Exception:
            pass
