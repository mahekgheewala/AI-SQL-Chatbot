from pydantic import BaseModel
from typing import List


# ─── Phase 1 — Chat ──────────────────────────────────────────────────────────

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
