from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class ExecutionMetadata(BaseModel):
    sql: Optional[str] = None
    database_name: Optional[str] = None
    execution_time_ms: float = 0.0
    row_count: int = 0
    column_count: int = 0

class DatasetMetadata(BaseModel):
    # In-memory DataFrame, excluded from standard JSON/Pydantic serialization
    dataframe: Optional[Any] = Field(default=None, exclude=True)
    columns: List[str] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

class SemanticMetadata(BaseModel):
    numeric_columns: List[str] = Field(default_factory=list)
    categorical_columns: List[str] = Field(default_factory=list)
    datetime_columns: List[str] = Field(default_factory=list)
    boolean_columns: List[str] = Field(default_factory=list)
    special_semantics: Dict[str, str] = Field(
        default_factory=dict,
        description="Maps column names to semantic tags like 'CURRENCY' or 'PERCENTAGE'"
    )

class StatisticsMetadata(BaseModel):
    # Stats status: "IMMEDIATE" | "DEFERRED" | "COMPUTED" | "FAILED"
    status: str = "IMMEDIATE"
    
    # E.g. {"salary": {"mean": 50000.0, "min": 30000.0, "max": 80000.0, ...}}
    numeric_stats: Dict[str, Dict[str, float]] = Field(default_factory=dict)
    
    # E.g. {"department": {"unique_count": 4, "top_value": "Engineering", ...}}
    categorical_stats: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

class PipelineMetadata(BaseModel):
    classification: Optional[str] = None
    selected_pipeline: Optional[str] = None
    execution_path: Optional[str] = None

class ProcessingMetadata(BaseModel):
    # Processing status: "SUCCESS" | "PARTIAL" | "FAILED"
    status: str = "SUCCESS"
    processing_time_ms: float = 0.0
    warnings: List[str] = Field(default_factory=list)
    memory_usage_bytes: int = 0
    step_durations: Dict[str, float] = Field(default_factory=dict)

class DatasetProfile(BaseModel):
    is_empty: bool = False
    is_single_row: bool = False
    is_single_column: bool = False
    has_nulls: bool = False
    has_duplicates: bool = False
    estimated_memory_usage_bytes: int = 0
    
    # Expanded metadata fields
    null_value_count: int = 0
    duplicate_row_count: int = 0
    missing_percentage: float = 0.0
    numeric_column_count: int = 0
    categorical_column_count: int = 0
    datetime_column_count: int = 0
    boolean_column_count: int = 0
    estimated_dataset_size: int = 0
    dataset_empty: bool = False
    single_row: bool = False
    multi_row: bool = False
    column_wise_data_types: Dict[str, str] = Field(default_factory=dict)
    processing_duration: float = 0.0

class ProcessedResult(BaseModel):
    execution: ExecutionMetadata
    dataset: DatasetMetadata
    semantics: SemanticMetadata
    statistics: StatisticsMetadata
    pipeline: PipelineMetadata
    processing: ProcessingMetadata
    profile: DatasetProfile
    raw_rows: List[List[Any]] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

    def compute_full_statistics(self) -> None:
        """
        Force calculations of deferred advanced statistics.
        Merges calculations back into statistics.numeric_stats.
        """
        if self.statistics.status == "DEFERRED" and self.dataset.dataframe is not None:
            from agent.result_processing.statistics import compute_advanced_stats
            
            for col in self.semantics.numeric_columns:
                if col in self.dataset.dataframe.columns:
                    series = self.dataset.dataframe[col]
                    adv_stats = compute_advanced_stats(series)
                    
                    # Merge with existing lightweight stats
                    if col not in self.statistics.numeric_stats:
                        self.statistics.numeric_stats[col] = {}
                    self.statistics.numeric_stats[col].update(adv_stats)
                    
            self.statistics.status = "COMPUTED"
