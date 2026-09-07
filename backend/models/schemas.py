from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Any, Dict


# ─── Phase 1 — Structured Query Intent ──────────────────────────────────────────

from typing import Literal

def _current_session_id() -> Optional[str]:
    try:
        from utils.logging_config import session_id_var
        return session_id_var.get()
    except Exception:
        return None

class QueryOrdering(BaseModel):
    column: str
    direction: Literal["ASC", "DESC"]

    @model_validator(mode='before')
    @classmethod
    def _coerce_dir(cls, data: Any) -> Any:
        if isinstance(data, dict):
            dir_val = data.get("direction") or data.get("dir")
            if dir_val:
                data["direction"] = str(dir_val).upper()
        return data


class QueryAggregation(BaseModel):
    function: str
    column: str


class QueryEntities(BaseModel):
    database: Optional[str] = None
    tables: List[str] = Field(default_factory=list)
    columns: List[str] = Field(default_factory=list)
    tables_validated: bool = False
    columns_validated: bool = False


class QueryFilter(BaseModel):
    column: str
    operator: str
    value: Any

    @model_validator(mode='before')
    @classmethod
    def _coerce_op_val(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "op" in data and "operator" not in data:
                data["operator"] = data.pop("op")
            if "val" in data and "value" not in data:
                data["value"] = data.pop("val")
        return data


class SemanticRepresentation(BaseModel):
    action: Optional[str] = None
    target_entities: List[str] = Field(default_factory=list)
    target_attributes: List[str] = Field(default_factory=list)
    numeric_constraints: Dict[str, int] = Field(default_factory=dict)
    ordering: List[QueryOrdering] = Field(default_factory=list)
    aggregations: List[QueryAggregation] = Field(default_factory=list)
    filters: List[QueryFilter] = Field(default_factory=list)
    schema_request: bool = False
    missing_required_fields: List[str] = Field(default_factory=list)


class QueryConstraints(BaseModel):
    limit: Optional[int] = None
    offset: Optional[int] = None
    filters: List[QueryFilter] = Field(default_factory=list)


class QueryIntent(BaseModel):
    operation: Optional[str] = None  # 'LIST_TABLES', 'LIST_DATABASES', 'DATABASE_SWITCH', 'DESCRIBE_TABLE', 'RAW_SQL', 'SIMPLE_DDL', 'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'GENERAL_CONVERSATION'
    entities: QueryEntities = Field(default_factory=QueryEntities)
    constraints: QueryConstraints = Field(default_factory=QueryConstraints)
    ordering: List[Any] = Field(default_factory=list)
    aggregations: List[Any] = Field(default_factory=list)
    grouping: List[str] = Field(default_factory=list)
    comparisons: List[Dict[str, Any]] = Field(default_factory=list)


# ─── SemanticFrame — Stage 1 typed role layer ──────────────────────────────────
# Grounded frame produced by agent/semantic_frame.py and wired into execution by
# agent_coordinator (Phase 10.6). Existing QueryIntent/SemanticRepresentation
# paths are untouched for backward compat.

class ColumnSpec(BaseModel):
    """A column definition used by CREATE_TABLE / ALTER TABLE capabilities."""
    name: str
    type: Optional[str] = None
    drop: bool = False          # True => DROP COLUMN
    new_name: Optional[str] = None   # rename target (RENAME COLUMN)
    # Structural fix for the audit finding that constraints (PRIMARY KEY,
    # UNIQUE, NOT NULL, DEFAULT, REFERENCES) had nowhere to be represented
    # in this model at all — a list of individually-validated constraint
    # clauses (see agent/capability_check.py's _constraint_allowed()),
    # e.g. ["PRIMARY KEY"], ["NOT NULL", "UNIQUE"], ["REFERENCES customers(id)"].
    constraints: List[str] = []


class AlterSpec(BaseModel):
    """Structured ALTER TABLE operation (one at a time)."""
    operation: Literal["ADD_COLUMN", "DROP_COLUMN", "RENAME_COLUMN", "ALTER_COLUMN_TYPE"]
    column: Optional[str] = None
    new_column: Optional[str] = None
    new_type: Optional[str] = None


class ChartSpec(BaseModel):
    """Typed roles for the VISUALIZE capability."""
    chart_type: Optional[str] = None
    measure: Optional[str] = None
    dimension: Optional[str] = None
    aggregation: Optional[str] = None


class SampleDataSpec(BaseModel):
    """Typed roles for the ADD_SAMPLE_DATA capability."""
    count: Optional[int] = None
    database: Optional[str] = None
    table: Optional[str] = None


class SelectionSpec(BaseModel):
    """Result of context/reference resolution (pronoun, ordinal, relative)."""
    kind: Literal["PRONOUN", "ORDINAL", "EXACT", "RELATIVE"]
    ordinal: Optional[int] = None
    relative: Optional[str] = None
    resolved_entity: Optional[str] = None


class SemanticFrame(BaseModel):
    """A single, grounded interpretation of one user message.

    Action is detected first; every other field is a typed role slot. Role
    values are grounded against live per-user metadata (or fresh names that a
    create/drop/rename capability explicitly allows) — never invented.
    """
    action: Optional[str] = None
    object_type: Optional[str] = None       # DATABASE|TABLE|COLUMN|DATA|CHART|SCHEMA|RAW_SQL|None
    capability_id: Optional[str] = None
    database: Optional[str] = None
    table: Optional[str] = None
    new_name: Optional[str] = None   # rename target (RENAME_TABLE)
    columns: List[ColumnSpec] = Field(default_factory=list)
    alter: Optional[AlterSpec] = None
    chart: Optional[ChartSpec] = None
    sample_data: Optional[SampleDataSpec] = None
    selection: Optional[SelectionSpec] = None
    raw_sql: Optional[str] = None
    filters: List[QueryFilter] = Field(default_factory=list)
    ordering: List[QueryOrdering] = Field(default_factory=list)
    limit: Optional[int] = None
    group_by: List[str] = Field(default_factory=list)
    aggregations: List[QueryAggregation] = Field(default_factory=list)
    references: List[str] = Field(default_factory=list)
    missing_required: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    source: str = "SEMANTIC_FRAME"
    is_clarification_response: bool = False
    pending_frame_id: Optional[str] = None
    legacy_intent: Optional[str] = None


from pydantic import BaseModel, Field, model_validator

class UnderstandingResult(BaseModel):
    status: str = "SUCCESS"  # 'SUCCESS', 'AMBIGUOUS', 'UNDERSTANDING_FAILED'
    intent: str = "UNKNOWN"   # Preserved for downstream compatibility
    raw_input: str
    normalized_input: str
    corrected_input: Optional[str] = None
    query_intent: QueryIntent = Field(default_factory=QueryIntent)
    needs_clarification: bool = False
    clarification_type: Optional[str] = None
    clarification_message: Optional[str] = None
    confidence: float = 1.0
    source: str = "STRUCTURED_PARSER"  # 'STRUCTURED_PARSER' or 'DETERMINISTIC'
    semantic_frame: Optional[SemanticFrame] = None

    @model_validator(mode='before')
    @classmethod
    def _populate_query_intent(cls, data: Any) -> Any:
        if isinstance(data, dict):
            q_intent = data.get("query_intent")
            if not q_intent or not isinstance(q_intent, QueryIntent):
                q_intent = QueryIntent()
                if "query_intent" in data and isinstance(data["query_intent"], dict):
                    q_intent = QueryIntent(**data["query_intent"])
                data["query_intent"] = q_intent

            db_kw = data.pop("database", None)
            tbl_kw = data.pop("table", None)
            cols_kw = data.pop("columns", None)
            limit_kw = data.pop("limit", None)

            if db_kw and not q_intent.entities.database:
                q_intent.entities.database = db_kw
            if tbl_kw and not q_intent.entities.tables:
                q_intent.entities.tables = [tbl_kw]
            if cols_kw and not q_intent.entities.columns:
                q_intent.entities.columns = cols_kw
            if limit_kw and q_intent.constraints.limit is None:
                q_intent.constraints.limit = limit_kw

        return data

    @property
    def database(self) -> Optional[str]:
        return self.query_intent.entities.database

    @property
    def table(self) -> Optional[str]:
        return self.query_intent.entities.tables[0] if self.query_intent.entities.tables else None

    @property
    def columns(self) -> List[str]:
        return self.query_intent.entities.columns

    @property
    def limit(self) -> Optional[int]:
        return self.query_intent.constraints.limit


class RoutingDecision(BaseModel):
    route: str  # 'DIRECT', 'RAW_SQL', 'AI_PLANNER', 'CONVERSATION', 'CLARIFICATION'
    intent: str
    target_db: Optional[str] = None
    target_table: Optional[str] = None
    target_columns: List[str] = Field(default_factory=list)
    tool_name: Optional[str] = None
    needs_clarification: bool = False
    clarification_type: Optional[str] = None
    reason: str = ""


class GatewayDecision(BaseModel):
    """Single authoritative routing output from the Universal Semantic Gateway.

    Every downstream component reads this; none independently re-routes.
    Combines UnderstandingResult + RoutingDecision + clarification state
    into one object that flows from gateway → chat.py → coordinator → handler.
    """
    understanding: UnderstandingResult
    route: str  # 'DIRECT', 'RAW_SQL', 'AI_PLANNER', 'CONVERSATION', 'CLARIFICATION'
    target_db: Optional[str] = None
    target_table: Optional[str] = None
    target_columns: List[str] = Field(default_factory=list)
    tool_name: Optional[str] = None
    needs_clarification: bool = False
    clarification_type: Optional[str] = None
    clarification_message: Optional[str] = None
    clarification_data: Optional[Dict[str, Any]] = None
    requires_db_connection: bool = True
    semantic_frame: Optional[SemanticFrame] = None
    reason: str = ""


class MessageParam(BaseModel):
    role: str  # 'user' or 'assistant'
    text: str


class ChatRequest(BaseModel):
    message: str
    table: str | None = None
    history: List[MessageParam] | None = None
    # ── Phase 6: Session Context Memory ──────────────────────────────────────
    session_id: str | None = None


class ExecutionResult(BaseModel):
    success: bool
    operation: str
    columns: List[str] = []
    rows: List[list] = []
    row_count: int = 0
    message: str | None = None
    error: str | None = None

class ChatResponse(BaseModel):
    reply: str
    intent: str
    database: str | None = None
    sql: str | None = None
    question: str | None = None
    # ── Phase 9.5: Server-resolved chat session id ───────────────────────────
    session_id: str | None = Field(default_factory=_current_session_id)
    # ── Phase 3.5: Refresh infrastructure ────────────────────────────────────
    refresh_databases: bool = False
    refresh_tables: bool = False
    refresh_schema: bool = False
    refresh_data: bool = False
    # ── Phase 4: Validation fields ────────────────────────────────────────────
    # risk_level is None for NEEDS_CLARIFICATION (validation skipped entirely).
    valid: bool = True
    risk_level: str | None = "SAFE"   # "SAFE" | "HIGH_RISK" | "CRITICAL_RISK" | "BLOCKED" | None
    requires_confirmation: bool = False
    blocked_reason: str | None = None
    # ── Phase 5: Execution Result ─────────────────────────────────────────────
    execution: ExecutionResult | None = None
    # ── Phase 9.5: Friendly Error Experience ──────────────────────────────────
    error_code: str | None = None           # e.g. "DB_CONNECTION_FAILED"
    error_detail: dict | None = None        # {category, title, message, suggestion, retry}
    # Chart-friendly query results (columns/rows suitable for client-side
    # charting); no server-side chart-spec engine is wired in, so this is
    # always None today — kept as a generic placeholder for the frontend
    # contract rather than a dead package's specific type.
    visualization: dict | None = None

class ExecuteConfirmedRequest(BaseModel):
    sql: str
    intent: str
    database: str
    # ── Phase 6: Session Context Memory ──────────────────────────────────────
    session_id: str | None = None



# ─── Phase 2 Base — Schema ───────────────────────────────────────────────────

class SelectDBRequest(BaseModel):
    database: str


class SchemaResponse(BaseModel):
    selected: str
    schema: dict[str, list[str]]


# ─── Phase 2 Additional — Database & Table Management ────────────────────────

class CreateDatabaseRequest(BaseModel):
    """Body for POST /api/create-database."""
    database_name: str


class ColumnDefinition(BaseModel):
    """A single column specification for dynamic table creation."""
    name: str
    type: str   # Must be one of ALLOWED_COLUMN_TYPES in table_manager.py


class CreateTableRequest(BaseModel):
    """Body for POST /api/create-table."""
    database: str
    table_name: str
    columns: List[ColumnDefinition]


# ─── Phase 9.5 — Admin Dashboard Response Models ─────────────────────────────

class LogEntry(BaseModel):
    """A single structured log entry from JSONL log files."""
    timestamp: Optional[str] = None
    level: Optional[str] = None
    logger: Optional[str] = None
    message: Optional[str] = None
    request_id: Optional[str] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    database_name: Optional[str] = None
    selected_table: Optional[str] = None
    operation_type: Optional[str] = None
    execution_time_ms: Optional[float] = None
    intent: Optional[str] = None

    class Config:
        extra = "allow"  # Preserve any additional fields from the log


class LogsResponse(BaseModel):
    """Paginated log explorer response."""
    entries: List[Any] = []
    total: int = 0
    page: int = 1
    page_size: int = 50
    pages: int = 1


class GeminiCallCounts(BaseModel):
    total: int = 0
    router: int = 0
    planner: int = 0
    sql_generator: int = 0
    summarizer: int = 0
    report_formatter: int = 0


class TokenUsage(BaseModel):
    prompt: int = 0
    response: int = 0
    total: int = 0


class AnalyticsResponse(BaseModel):
    """Analytics dashboard response."""
    total_requests: int = 0
    sql_queries: int = 0
    gemini_calls: GeminiCallCounts = GeminiCallCounts()
    avg_response_ms: float = 0.0
    avg_sql_ms: float = 0.0
    avg_ai_ms: float = 0.0
    token_usage: TokenUsage = TokenUsage()
    estimated_cost_usd: float = 0.0
    top_intents: List[Any] = []
    top_databases: List[Any] = []
    error_count: int = 0
    error_rate_pct: float = 0.0


class LogFileInfo(BaseModel):
    size_bytes: int = 0
    size_human: str = "0 B"
    last_modified: Optional[str] = None


class HealthResponse(BaseModel):
    """System health snapshot response."""
    active_sessions: int = 0
    cache_generation: int = 0
    routing_summaries_count: int = 0
    log_file_sizes: dict = {}
    server_uptime_s: float = 0.0
    server_start_utc: str = ""
    gemini_metrics: dict = {}

