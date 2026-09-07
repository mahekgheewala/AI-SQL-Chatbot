"""Declarative capability registry — Stage 1 (additive, consumed by Stage 2).

Each capability describes one general user capability. There are NO per-query
rules here: a capability is matched by (action, object_type) detected from a
SemanticFrame, and its role contracts drive grounding, completeness checks and
routing. `legacy_intent` keeps response payload intent strings byte-stable.

Design rules enforced in later stages:
  * `required_roles` are roles a grounded frame MUST satisfy; otherwise the
    frame is a clarification (never silent fabrication).
  * `needs_existing_entity=True` means the target must ground against live
    per-user metadata (databases/tables/columns). If it cannot, it is a
    clarification, not a fallback.
  * `allows_fresh_name=True` is only granted to create/rename/drop capabilities
    so they may introduce names that do not yet exist in metadata.
"""

from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class Capability:
    id: str
    action: str                          # canonical action from the semantic lexicon
    object_type: Optional[str]           # DATABASE|TABLE|COLUMN|DATA|CHART|SCHEMA|RAW_SQL|None
    route: str                           # "HANDLER" | "SQL_BUILDER" | "CONVERSATION"
    legacy_intent: str                   # response payload intent string
    required_roles: Tuple[str, ...] = ()
    optional_roles: Tuple[str, ...] = ()
    conflicting_roles: Tuple[str, ...] = ()
    needs_existing_entity: bool = True
    allows_fresh_name: bool = False


CAPABILITIES: Tuple[Capability, ...] = (
    Capability(
        id="general_conversation",
        action="converse",
        object_type=None,
        route="CONVERSATION",
        legacy_intent="GENERAL_CONVERSATION",
    ),
    Capability(
        id="knowledge",
        action="ask",
        object_type="KNOWLEDGE",
        route="CONVERSATION",
        legacy_intent="KNOWLEDGE",
    ),
    Capability(
        id="create_database",
        action="create",
        object_type="DATABASE",
        route="SQL_BUILDER",
        legacy_intent="CREATE_DATABASE",
        required_roles=("database",),
        needs_existing_entity=False,
        allows_fresh_name=True,
    ),
    Capability(
        id="create_table",
        action="create",
        object_type="TABLE",
        route="SQL_BUILDER",
        legacy_intent="CREATE_TABLE",
        required_roles=("table", "columns"),
        optional_roles=("database",),
        needs_existing_entity=False,
        allows_fresh_name=True,
    ),
    Capability(
        id="add_column",
        action="alter",
        object_type="COLUMN",
        route="SQL_BUILDER",
        legacy_intent="ADD_COLUMN",
        required_roles=("table", "column"),
        optional_roles=("database", "column_type"),
    ),
    Capability(
        id="drop_column",
        action="alter",
        object_type="COLUMN",
        route="SQL_BUILDER",
        # Was "ADD_COLUMN" (copy-pasted from the capability above) — the
        # actual SQL-text-based risk classification in execute_sql()
        # re-derives the real operation from the generated SQL and isn't
        # affected by this, but the label itself is still wrong wherever
        # it's surfaced on its own (e.g. the response payload's `intent`
        # field showing "ADD_COLUMN" for a drop request).
        legacy_intent="DROP_COLUMN",
        required_roles=("table", "column"),
        optional_roles=("database",),
    ),
    Capability(
        id="alter_column",
        action="alter",
        object_type="COLUMN",
        route="SQL_BUILDER",
        # Was "ADD_COLUMN" — same copy-paste as drop_column above.
        # "MODIFY_COLUMN" matches the label validation/safety_checker.py
        # and validation/schema_creator_validator.py already use for a
        # column TYPE change, so this now agrees with the rest of the app
        # instead of introducing a fourth spelling for the same concept.
        legacy_intent="MODIFY_COLUMN",
        required_roles=("table", "column"),
        optional_roles=("database", "column_type"),
    ),
    Capability(
        id="switch_database",
        action="switch",
        object_type="DATABASE",
        route="HANDLER",
        legacy_intent="DATABASE_SWITCH",
        required_roles=("database",),
        needs_existing_entity=True,
    ),
    Capability(
        id="list_tables",
        action="list",
        object_type="TABLE",
        route="HANDLER",
        legacy_intent="LIST_TABLES",
    ),
    Capability(
        id="list_databases",
        action="list",
        object_type="DATABASE",
        route="HANDLER",
        legacy_intent="LIST_DATABASES",
    ),
    Capability(
        id="describe_table",
        action="describe",
        object_type="TABLE",
        route="HANDLER",
        legacy_intent="DESCRIBE_TABLE",
        required_roles=("table",),
        optional_roles=("database",),
    ),
    Capability(
        id="add_sample_data",
        action="add",
        object_type="DATA",
        route="SQL_BUILDER",
        legacy_intent="ADD_SAMPLE_DATA",
        required_roles=("table",),
        optional_roles=("count", "database"),
    ),
    Capability(
        id="visualize",
        action="visualize",
        object_type="CHART",
        route="HANDLER",
        legacy_intent="VISUALIZE",
        required_roles=("chart",),
        optional_roles=("database", "table", "measure", "dimension"),
    ),
    Capability(
        id="retrieve",
        action="select",
        object_type="DATA",
        route="SQL_BUILDER",
        legacy_intent="QUERY",
        required_roles=("table",),
        optional_roles=(
            "database", "columns", "filters", "ordering", "limit", "group_by", "aggregations",
        ),
    ),
    Capability(
        id="drop_table",
        action="drop",
        object_type="TABLE",
        route="SQL_BUILDER",
        legacy_intent="DROP_TABLE",
        required_roles=("table",),
        optional_roles=("database",),
    ),
    Capability(
        id="drop_database",
        action="drop",
        object_type="DATABASE",
        route="SQL_BUILDER",
        legacy_intent="DROP_DATABASE",
        required_roles=("database",),
    ),
    Capability(
        id="rename_table",
        action="rename",
        object_type="TABLE",
        route="SQL_BUILDER",
        legacy_intent="RENAME_TABLE",
        required_roles=("table", "new_name"),
        optional_roles=("database",),
        allows_fresh_name=True,
    ),
    Capability(
        id="raw_sql",
        action="execute",
        object_type="RAW_SQL",
        route="SQL_BUILDER",
        legacy_intent="QUERY",
        required_roles=("raw_sql",),
        needs_existing_entity=False,
    ),
    Capability(
        id="understanding_failed",
        action="none",
        object_type=None,
        route="CONVERSATION",
        legacy_intent="UNDERSTANDING_FAILED",
    ),
    Capability(
        id="needs_clarification",
        action="none",
        object_type=None,
        route="CONVERSATION",
        legacy_intent="NEEDS_CLARIFICATION",
    ),
)


CAPABILITY_BY_ID: dict = {c.id: c for c in CAPABILITIES}


def get_capability(capability_id: str) -> Optional[Capability]:
    return CAPABILITY_BY_ID.get(capability_id)


def capability_for(action: str, object_type: Optional[str]) -> Optional[Capability]:
    """Map a detected (action, object_type) to exactly one capability.

    Returns None when no capability matches — the interpretation must fall
    back to understanding_failed rather than inventing a route.
    """
    for cap in CAPABILITIES:
        if cap.action == action and cap.object_type == object_type:
            return cap
    return None


# ─── Canonical action lexicon ────────────────────────────────────────────────
# The finite set of actions the semantic layer may emit. Stage 2's action
# detector maps natural-language verbs onto this set; it is intentionally small
# and general (no per-sentence rules).

ACTIONS: Tuple[str, ...] = (
    "converse", "ask", "create", "alter", "switch", "list", "describe", "add",
    "visualize", "select", "execute", "drop", "rename", "none",
)

OBJECT_TYPES: Tuple[str, ...] = (
    "DATABASE", "TABLE", "COLUMN", "DATA", "CHART", "SCHEMA", "RAW_SQL",
    "KNOWLEDGE", None,
)
