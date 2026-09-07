"""Capability completeness & conflict check for SemanticFrame.

Compares a grounded frame against the capability registry role contracts
(including grounding failures recorded on the frame). Returns a structured
verdict; the frame builder turns a missing/conflicting result into a
clarification (never a silent fallback).
"""

import re
from dataclasses import dataclass
from typing import List, Optional

from models.schemas import SemanticFrame
from agent.capabilities import Capability, get_capability
from agent import grounding as G
from db.column_types import ALLOWED_COLUMN_TYPES


@dataclass
class CheckResult:
    status: str                 # "SUCCESS" | "NEEDS_CLARIFICATION" | "UNDERSTANDING_FAILED"
    missing_required: List[str] = None
    message: Optional[str] = None

    def __post_init__(self):
        if self.missing_required is None:
            self.missing_required = []


def _role_satisfied(frame: SemanticFrame, role: str) -> bool:
    if role == "database":
        return bool(frame.database)
    if role == "table":
        return bool(frame.table)
    if role == "new_name":
        return bool(frame.new_name)
    if role == "columns":
        return bool(frame.columns)
    if role == "column":
        if frame.alter and frame.alter.column:
            return True
        return bool(frame.columns)
    if role == "chart":
        return frame.chart is not None
    if role == "count":
        return frame.sample_data is not None and frame.sample_data.count is not None
    if role == "raw_sql":
        return bool(frame.raw_sql)
    return True


def check(frame: SemanticFrame, metadata: dict) -> CheckResult:
    capability = get_capability(frame.capability_id or "")
    if capability is None:
        return CheckResult(
            status="UNDERSTANDING_FAILED",
            message="No supported capability matches this request.",
        )

    registry_missing = [r for r in capability.required_roles if not _role_satisfied(frame, r)]
    grounding_missing = [r for r in (frame.missing_required or []) if r not in registry_missing]
    missing = registry_missing + grounding_missing

    if missing:
        return CheckResult(
            status="NEEDS_CLARIFICATION",
            missing_required=missing,
            message=_clarification_message(capability, frame, missing, metadata),
        )

    for role in ("table", "column", "database"):
        if _invalid_type_present(frame, role):
            return CheckResult(
                status="NEEDS_CLARIFICATION",
                message="The column type is not recognized. Please use one of the supported SQL types.",
            )

    return CheckResult(status="SUCCESS")


def _invalid_type_present(frame: SemanticFrame, role: str) -> bool:
    for col in frame.columns:
        if col.type and not _type_allowed(col.type):
            return True
    if frame.alter and frame.alter.new_type and not _type_allowed(frame.alter.new_type):
        return True
    return False


def _type_allowed(type_token: str) -> bool:
    # db.column_types.ALLOWED_COLUMN_TYPES is the documented single source
    # of truth for which column types this app actually accepts — its own
    # docstring records that this exact list was ONCE ALREADY duplicated
    # and drifted between two other files before being consolidated there.
    # This function used to keep a third, hand-copied set instead of
    # importing it — which had already drifted again: "SMALLINT" and
    # "REAL" passed this (early, frame-level) check but were then rejected
    # by validation/schema_creator_validator.py's Gate 3 (the real
    # authority), so a column typed that way looked accepted right up
    # until it confusingly failed several steps later in the pipeline.
    primary = G.normalize_type(type_token) or str(type_token).upper()
    if primary in ALLOWED_COLUMN_TYPES:
        return True
    if re_match_varchar_length(primary):
        return True
    return False


def re_match_varchar_length(type_upper: str) -> bool:
    return bool(re.match(r"^VARCHAR\(\d+\)$", type_upper))


# ── Column constraint validation ────────────────────────────────────────────
# Whitelist-pattern based, mirroring _type_allowed()'s approach above — a
# constraint clause is free text that gets interpolated directly into a
# CREATE TABLE statement, so (like `type`) it's validated against known-safe
# shapes rather than trusted as-is, regardless of whether it came from a
# deterministic parse or an LLM proposal.
_SIMPLE_CONSTRAINTS = {"PRIMARY KEY", "UNIQUE", "NOT NULL", "NULL"}

_REFERENCES_PATTERN = re.compile(
    r"^REFERENCES\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\)"
    r"(?:\s+ON\s+(?:DELETE|UPDATE)\s+(?:CASCADE|RESTRICT|SET\s+NULL|SET\s+DEFAULT|NO\s+ACTION))*$",
    re.IGNORECASE,
)

_DEFAULT_PATTERN = re.compile(
    r"^DEFAULT\s+(?:'[^']*'|-?\d+(?:\.\d+)?|TRUE|FALSE|NULL|CURRENT_TIMESTAMP|CURRENT_DATE|CURRENT_TIME)$",
    re.IGNORECASE,
)


def _constraint_allowed(token: str) -> bool:
    """True if `token` is a recognized, safe constraint clause. Deliberately
    does not attempt to support CHECK (...) — validating an arbitrary
    boolean expression safely is a meaningfully bigger problem than the
    fixed-shape clauses here, and out of scope for this fix; PRIMARY KEY /
    UNIQUE / NOT NULL / NULL / REFERENCES / DEFAULT cover what was actually
    found missing in the audit."""
    t = token.strip()
    if t.upper() in _SIMPLE_CONSTRAINTS:
        return True
    if _REFERENCES_PATTERN.match(t):
        return True
    if _DEFAULT_PATTERN.match(t):
        return True
    return False


def _clarification_message(capability: Capability, frame: SemanticFrame,
                           missing: List[str], metadata: dict = None) -> str:
    role = missing[0]
    if role == "database":
        if capability.id in ("create_database",):
            return "Which name would you like to use for the new database?"
        if capability.id == "switch_database":
            target = frame.database
            available = [str(d) for d in (metadata or {}).get("databases") or [] if d]
            if target:
                suffix = f" Available databases: {', '.join(available)}." if available else ""
                return (
                    f"The database '{target}' was not found in your accessible databases."
                    f"{suffix}"
                )
            return "Which database would you like to switch to?"
        return "Which database should this operation target?"
    if role == "table":
        if capability.id == "create_table":
            if frame.columns:
                cols = ", ".join(c.name for c in frame.columns)
                return (
                    f"You specified columns ({cols}), but did not provide a table name. "
                    "What would you like to name this new table?"
                )
            return "What would you like to name this new table, and which columns should it have?"
        if capability.id == "retrieve":
            target = frame.table
            if target:
                return (
                    f"The table '{target}' was not found in the current database. "
                    "Please check the table name or select another database."
                )
            return "Which table would you like to query?"
        if capability.id == "add_sample_data":
            return "Which table would you like to add sample data to?"
        if capability.id == "describe_table":
            return "Which table would you like to describe?"
        return "Which table should this operation target?"
    if role == "new_name":
        if frame.table:
            return f"What would you like to rename '{frame.table}' to?"
        return "What should the new name be?"
    if role == "columns":
        return "Please provide at least one column for the new table."
    if role == "column":
        if frame.alter and frame.alter.operation == "ADD_COLUMN":
            return "Which column would you like to add, and to which table?"
        if frame.alter and frame.alter.operation == "DROP_COLUMN":
            return "Which column would you like to drop, and from which table?"
        return "Which column should this operation target?"
    if role == "chart":
        return "What kind of chart would you like to see?"
    if role == "measure":
        # The chart request named a measure word that isn't a real column
        # on this table ("plot bogus by department") — the completeness
        # check upstream used to only verify a ChartSpec object existed at
        # all, not that its measure actually resolved, so this silently
        # built a chart with no data to plot instead of asking.
        candidates = list((metadata or {}).get("columns_by_table", {}).get(frame.table, [])) if frame.table else []
        if candidates:
            return (
                "I couldn't tell which column you want to plot. Did you mean one of: "
                f"{', '.join(candidates)}?"
            )
        return "Which column would you like to plot?"
    if role == "count":
        return "How many sample rows would you like to add?"
    if role == "ordering_column":
        candidates = G.numeric_and_datetime_columns(frame.table, metadata or {}) if frame.table else []
        if candidates:
            return (
                "I couldn't tell which column you mean. Did you mean one of: "
                f"{', '.join(candidates)}?"
            )
        return "Which numeric or date column should I use for that?"
    return "Please provide the missing details for this request."
