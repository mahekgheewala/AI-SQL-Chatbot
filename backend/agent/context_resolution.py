"""Context & reference resolution for SemanticFrame.

Resolves pronouns, ordinal/selection references and relative anchors against
structured prior frames, and implements frame unification / topic-supersede
for pending clarifications.

Context shape:
    {
        "pending_frame": SemanticFrame | None,
        "prior_frames": [SemanticFrame, ...],   # most recent last
        "active_database": name | None,
    }
"""

from typing import Optional

from models.schemas import SemanticFrame
from agent import grounding as G


def resolve_references(frame: SemanticFrame, context: dict) -> SemanticFrame:
    """Resolve pronoun/relative roles using prior frames and active context."""
    prior = context.get("prior_frames") or []
    prior_frame = prior[-1] if prior else None

    if frame.table and G.is_pronoun(frame.table):
        resolved = _prior_table(prior_frame)
        if resolved:
            frame.table = resolved
        else:
            return frame

    if frame.database and G.is_pronoun(frame.database):
        active = context.get("active_database")
        if active:
            frame.database = active

    if _is_that_database_reference(frame.database, frame.table):
        active = context.get("active_database")
        if active and not frame.database:
            frame.database = active

    if not frame.table and frame.action in ("select", "visualize") and prior_frame:
        frame.table = prior_frame.table

    return frame


def _is_that_database_reference(database, table) -> bool:
    if database:
        return str(database).lower() in ("that", "this", "current", "same")
    if table:
        return str(table).lower() in ("that", "this", "current", "same")
    return False


def _prior_table(prior_frame: Optional[SemanticFrame]) -> Optional[str]:
    if prior_frame is None:
        return None
    if prior_frame.table:
        return prior_frame.table
    if prior_frame.alter and prior_frame.alter.column and prior_frame.table is None:
        return None
    return None


def unify_with_pending(frame: SemanticFrame, context: dict):
    """Merge the frame into a pending clarification, or supersede it.

    Returns (resolved_frame, superseded_pending: bool).
    """
    pending = context.get("pending_frame")
    if pending is None:
        return frame, False

    same_capability = pending.capability_id == frame.capability_id
    fills_missing = _fills_missing(pending, frame)

    if same_capability or fills_missing:
        merged = _merge(pending, frame)
        if merged is not None:
            return merged, False

    return frame, True


def _fills_missing(pending: SemanticFrame, frame: SemanticFrame) -> bool:
    pending_roles = set(pending.missing_required or [])
    if not pending_roles:
        return False
    if "columns" in pending_roles and frame.columns:
        return True
    if "table" in pending_roles and frame.table:
        return True
    if "database" in pending_roles and frame.database:
        return True
    return False


def _merge(pending: SemanticFrame, frame: SemanticFrame) -> Optional[SemanticFrame]:
    merged = pending.model_copy(deep=True)
    if not merged.table and frame.table:
        merged.table = frame.table
    if not merged.database and frame.database:
        merged.database = frame.database
    if frame.columns and not merged.columns:
        merged.columns = frame.columns
    if frame.raw_sql and not merged.raw_sql:
        merged.raw_sql = frame.raw_sql
    if frame.filters and not merged.filters:
        merged.filters = frame.filters
    if frame.limit is not None and merged.limit is None:
        merged.limit = frame.limit
    if frame.alter and not merged.alter:
        merged.alter = frame.alter
    if frame.chart and not merged.chart:
        merged.chart = frame.chart
    if frame.sample_data and not merged.sample_data:
        merged.sample_data = frame.sample_data
    merged.missing_required = []
    merged.is_clarification_response = True
    return merged
