import time
import pandas as pd
from typing import List, Dict, Any, Optional

from agent.result_processing.models import (
    ProcessedResult,
    ExecutionMetadata,
    DatasetMetadata,
    SemanticMetadata,
    StatisticsMetadata,
    PipelineMetadata,
    ProcessingMetadata,
    DatasetProfile
)
from agent.result_processing.profiler import (
    profile_semantics, 
    build_dataset_profile, 
    translate_schema_oids,
    normalize_types
)
from agent.result_processing.statistics import (
    compute_descriptive_stats,
    compute_advanced_stats
)

CELL_DENSITY_THRESHOLD = 10000

class ResultProcessor:
    @staticmethod
    def process(
        columns: List[str],
        rows: List[List[Any]],
        sql: Optional[str] = None,
        dbname: Optional[str] = None,
        classification: Optional[str] = None,
        selected_pipeline: Optional[str] = None,
        execution_path: Optional[str] = None,
        column_types: Optional[Dict[str, int]] = None
    ) -> ProcessedResult:
        """
        Main coordinator for the Result Processing Layer.
        Performs step-by-step latency tracking, OID type mapping,
        data normalization, profiling, statistics, and handles exceptions.
        """
        t_start = time.perf_counter()
        step_durations: Dict[str, float] = {}
        warnings: List[str] = []
        status = "SUCCESS"
        statistics_status = "IMMEDIATE"
        
        # --- Stage 1: DataFrame Instantiation ---
        t_df_start = time.perf_counter()
        try:
            if rows:
                df = pd.DataFrame(rows, columns=columns)
            else:
                df = pd.DataFrame(columns=columns)
            df_success = True
        except Exception as e:
            df = pd.DataFrame(columns=columns)
            df_success = False
            status = "FAILED"
            warnings.append(f"DataFrame instantiation failed: {e}")
        step_durations["DataFrame Created"] = (time.perf_counter() - t_df_start) * 1000

        # Translate PostgreSQL type OIDs to standard categories
        schema_types = {}
        if column_types:
            try:
                schema_types = translate_schema_oids(column_types)
            except Exception as e:
                warnings.append(f"Schema OID translation failed: {e}")

        # --- Stage 2: Type Normalization ---
        t_norm_start = time.perf_counter()
        if df_success and not df.empty:
            try:
                prev_warnings_len = len(warnings)
                df = normalize_types(df, schema_types, warnings)
                if len(warnings) > prev_warnings_len:
                    status = "PARTIAL"
                norm_status = "COMPUTED"
            except Exception as e:
                norm_status = "FAILED"
                status = "PARTIAL"
                warnings.append(f"Type normalization failed: {e}")
        else:
            norm_status = "SKIPPED"
        step_durations[f"Type Normalization ({norm_status})"] = (time.perf_counter() - t_norm_start) * 1000

        # --- Stage 3: Semantic Profiling ---
        t_sem_start = time.perf_counter()
        try:
            semantics = profile_semantics(df, schema_types)
            sem_success = True
        except Exception as e:
            semantics = SemanticMetadata()
            sem_success = False
            status = "PARTIAL"
            warnings.append(f"Semantic profiling failed: {e}")
        step_durations["Semantic Profiling"] = (time.perf_counter() - t_sem_start) * 1000

        # --- Stage 4: Statistics Generation ---
        t_stats_start = time.perf_counter()
        numeric_stats = {}
        categorical_stats = {}
        
        if df_success and sem_success and not df.empty:
            cells_count = len(df) * len(columns)
            if cells_count < CELL_DENSITY_THRESHOLD:
                try:
                    numeric_stats, categorical_stats = compute_descriptive_stats(
                        df, 
                        semantics.numeric_columns, 
                        semantics.categorical_columns
                    )
                    
                    # Compute advanced stats immediately for small datasets
                    for col in semantics.numeric_columns:
                        if col in df.columns:
                            adv = compute_advanced_stats(df[col])
                            numeric_stats[col].update(adv)
                    statistics_status = "IMMEDIATE"
                except Exception as e:
                    statistics_status = "FAILED"
                    status = "PARTIAL"
                    warnings.append(f"Descriptive statistics calculation failed: {e}")
            else:
                try:
                    # Large Dataset: compute ONLY lightweight stats, defer advanced
                    numeric_stats, categorical_stats = compute_descriptive_stats(
                        df, 
                        semantics.numeric_columns, 
                        semantics.categorical_columns
                    )
                    statistics_status = "DEFERRED"
                    warnings.append(
                        f"Dataset contains {cells_count} cells which exceeds threshold. "
                        "Advanced statistics have been deferred."
                    )
                except Exception as e:
                    statistics_status = "FAILED"
                    status = "PARTIAL"
                    warnings.append(f"Lightweight statistics calculation failed: {e}")
        else:
            statistics_status = "SKIPPED"
            
        step_durations[f"Statistics Generated ({statistics_status})"] = (time.perf_counter() - t_stats_start) * 1000

        # --- Stage 5: Profile Generation ---
        t_prof_start = time.perf_counter()
        try:
            profile = build_dataset_profile(df, semantics)
            prof_status = "COMPUTED"
        except Exception as e:
            profile = DatasetProfile()
            prof_status = "FAILED"
            status = "PARTIAL"
            warnings.append(f"Dataset profiling failed: {e}")
        step_durations[f"Profile Generated ({prof_status})"] = (time.perf_counter() - t_prof_start) * 1000

        # Build execution metadata
        execution = ExecutionMetadata(
            sql=sql,
            database_name=dbname,
            execution_time_ms=0.0,
            row_count=len(df),
            column_count=len(columns)
        )

        # Build dataset metadata
        dataset = DatasetMetadata(
            dataframe=df,
            columns=columns
        )

        # Build pipeline metadata
        pipeline = PipelineMetadata(
            classification=classification,
            selected_pipeline=selected_pipeline,
            execution_path=execution_path
        )

        # Build statistics metadata
        statistics = StatisticsMetadata(
            status=statistics_status,
            numeric_stats=numeric_stats,
            categorical_stats=categorical_stats
        )

        # Assemble processing metadata
        t_total_ms = (time.perf_counter() - t_start) * 1000
        step_durations["ProcessedResult Created"] = 0.05
        
        processing = ProcessingMetadata(
            status=status,
            processing_time_ms=t_total_ms,
            warnings=warnings,
            memory_usage_bytes=profile.estimated_memory_usage_bytes,
            step_durations=step_durations
        )
        
        # Inject total processing time into profile duration too
        profile.processing_duration = t_total_ms

        # Return the ProcessedResult
        return ProcessedResult(
            execution=execution,
            dataset=dataset,
            semantics=semantics,
            statistics=statistics,
            pipeline=pipeline,
            processing=processing,
            profile=profile,
            raw_rows=rows
        )
