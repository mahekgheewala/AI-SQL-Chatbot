"""Entity grounding for the semantic-frame pipeline.

Resolves free-text tokens from a user message against the *live, connected*
schema (databases/tables/columns) via utils.entity_resolver.EntityResolver's
four-stage match (exact -> normalized -> alias -> fuzzy), plus a small set of
English-grammar word classifiers (is_identifier/is_non_entity/is_pronoun/
is_type_word) that agent/semantic_frame.py uses while scanning tokens for
role markers ("called X", "X table", ...).

Nothing here is schema-specific vocabulary — the only things kept static are
closed sets of English function words and SQL type names/aliases, the same
class of "language constants" semantic_frame.py itself keeps static (see its
module docstring). Real entity names always come from the metadata dict
built by semantic_frame.build_grounding_metadata().
"""

import re
from typing import List, Optional, Tuple

from db.column_types import ALLOWED_COLUMN_TYPES
from utils.entity_resolver import EntityResolver

_resolver = EntityResolver()


# ─── Word classifiers ────────────────────────────────────────────────────────

# Function words that can never be an entity name — hitting one while
# scanning for a name (e.g. after "called"/"named") signals the marker
# clause ended without one. Closed set of English grammar, not domain data.
_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "by", "with",
    "and", "or", "but", "is", "are", "was", "were", "be", "been", "being",
    "as", "from", "into", "onto", "so", "if", "then", "than", "please",
}

# Syntactically valid identifiers that are nonetheless this assistant's own
# structural vocabulary (table/database/column talk) rather than a name a
# user would actually create or reference.
_NON_ENTITY_WORDS = {
    "database", "databases", "db", "dbs", "table", "tables", "column",
    "columns", "field", "fields", "row", "rows", "record", "records",
    "entry", "entries", "value", "values", "data", "schema", "schemas",
    "called", "named", "name", "new", "fresh", "sample", "samples",
    "some", "any", "all", "each", "every",
}

_PRONOUNS = {
    "it", "its", "that", "this", "these", "those", "them", "they",
}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_identifier(word: str) -> bool:
    """A syntactically plausible name token — i.e. not a grammar glue word."""
    if not word or not _IDENTIFIER_RE.match(word):
        return False
    return word.lower() not in _STOPWORDS


def is_non_entity(word: str) -> bool:
    """A real word, but this assistant's own structural vocabulary, not a name."""
    return bool(word) and word.lower() in _NON_ENTITY_WORDS


def is_pronoun(word: str) -> bool:
    """A reference word ("it", "that", ...) resolved via context_resolution."""
    return bool(word) and word.lower() in _PRONOUNS


# ─── SQL type words ───────────────────────────────────────────────────────────

_TYPE_WORDS = {
    "text", "string", "str", "varchar", "char", "character", "varying",
    "integer", "int", "int4", "int8", "smallint", "bigint",
    "serial", "bigserial", "smallserial",
    "boolean", "bool",
    "date", "time", "timestamp", "timestamptz", "datetime",
    "float", "float4", "float8", "real", "double", "precision",
    "numeric", "decimal",
    "json", "jsonb", "uuid",
}

_TYPE_ALIASES = {
    "STR": "TEXT", "STRING": "TEXT",
    "INT": "INTEGER", "INT4": "INTEGER",
    "INT8": "BIGINT",
    "BOOL": "BOOLEAN",
    "DATETIME": "TIMESTAMP",
    "FLOAT4": "REAL", "FLOAT8": "DOUBLE PRECISION",
    "CHARACTER": "CHAR",
    "CHARACTER VARYING": "VARCHAR",
    "DOUBLE": "DOUBLE PRECISION",
}


def is_type_word(word: str) -> bool:
    return bool(word) and word.lower() in _TYPE_WORDS


def normalize_type(type_token: str) -> Optional[str]:
    """Canonicalize a parsed type token/phrase, e.g. "int" -> "INTEGER",
    "varchar(50)" -> "VARCHAR(50)". Returns None when unrecognized —
    callers (capability_check._type_allowed) fall back to their own check."""
    if not type_token:
        return None
    raw = str(type_token).strip()
    base = raw.split("(")[0].strip().upper()
    length_suffix = raw[raw.index("("):] if "(" in raw else ""

    canonical = _TYPE_ALIASES.get(base, base)
    if canonical in ALLOWED_COLUMN_TYPES or canonical == "DOUBLE PRECISION":
        return f"{canonical}{length_suffix}" if length_suffix else canonical
    return None


# ─── Schema grounding ─────────────────────────────────────────────────────────
# `metadata` is the dict shape built by semantic_frame.build_grounding_metadata:
#   databases, active_database, tables, all_tables_by_db,
#   columns_by_table, column_types_by_table

def ground_database(name: str, metadata: dict, allow_fresh: bool = False) -> Optional[str]:
    """Resolve `name` against the live database list. When allow_fresh (only
    true for create_database), an unmatched-but-plausible name passes through
    as-is so a genuinely new database can be created."""
    if not name:
        return None
    candidates = list((metadata or {}).get("databases") or [])
    result = _resolver.resolve(name, candidates, entity_type="database")
    if result.is_success:
        return result.value
    if allow_fresh and is_identifier(name) and not is_non_entity(name):
        return name
    return None


def ground_table(token: str, metadata: dict, allow_fresh: bool = False) -> Optional[str]:
    """Resolve `token` against the active database's known tables only —
    never other databases' tables, or a fresh CREATE TABLE name can get
    silently coerced onto an unrelated database's similarly-named table via
    alias matching. Callers needing a different database's tables scope
    `metadata` themselves (see semantic_frame.interpret_message)."""
    if not token:
        return None
    meta = metadata or {}
    candidates = list(meta.get("tables") or [])
    if not candidates:
        active_db = meta.get("active_database")
        if active_db:
            candidates = list((meta.get("all_tables_by_db") or {}).get(active_db) or [])
    result = _resolver.resolve(token, candidates, entity_type="table")
    if result.is_success:
        return result.value
    if allow_fresh and is_identifier(token) and not is_non_entity(token) and not is_pronoun(token):
        return token
    return None


def ground_column(token: str, metadata: dict, table: Optional[str] = None,
                   allow_fresh: bool = False) -> Optional[str]:
    """Resolve `token` against `table`'s known columns (or all known columns
    when no table context is available yet)."""
    if not token:
        return None
    columns_by_table = (metadata or {}).get("columns_by_table") or {}
    if table and table in columns_by_table:
        candidates = list(columns_by_table[table])
    else:
        candidates = sorted({c for cols in columns_by_table.values() for c in cols})
    result = _resolver.resolve(token, candidates, entity_type="column")
    if result.is_success:
        return result.value
    if allow_fresh and is_identifier(token) and not is_non_entity(token):
        return token
    return None


def find_table_for_column(token: str, metadata: dict) -> Optional[str]:
    """Which table (if exactly one) has a column `token` resolves to — used
    to infer a table from a bare column mention ("average salary by dept")."""
    columns_by_table = (metadata or {}).get("columns_by_table") or {}
    matches = [
        table for table, cols in columns_by_table.items()
        if _resolver.resolve(token, list(cols), entity_type="column").is_success
    ]
    return matches[0] if len(matches) == 1 else None


_NUMERIC_TYPE_HINTS = ("INT", "SERIAL", "NUMERIC", "DECIMAL", "FLOAT", "REAL", "DOUBLE")
_DATETIME_TYPE_HINTS = ("DATE", "TIME", "TIMESTAMP")


def numeric_and_datetime_columns(table: Optional[str], metadata: dict) -> List[str]:
    """Columns on `table` whose declared type is numeric or date/time-like —
    the candidate set offered for an ordering-column clarification
    (superlatives, "add N sample rows", ...). Falls back to the table's full
    column list when per-column types aren't cached yet, so a clarification
    still has something to offer rather than nothing."""
    if not table:
        return []
    types_by_table = (metadata or {}).get("column_types_by_table") or {}
    col_types = types_by_table.get(table)
    if col_types:
        out = [
            col for col, col_type in col_types.items()
            if any(h in str(col_type or "").upper() for h in _NUMERIC_TYPE_HINTS + _DATETIME_TYPE_HINTS)
        ]
        if out:
            return out
    columns_by_table = (metadata or {}).get("columns_by_table") or {}
    return list(columns_by_table.get(table) or [])


def resolve_attribute_word(word: str, table: Optional[str], metadata: dict) -> Tuple[Optional[str], bool]:
    """Resolve a bare attribute word ("salary", "quantity", "highest paid")
    against the real schema. Returns (column_name, needs_clarification) —
    SUCCESS gives (name, False); both NOT_FOUND and MULTIPLE_MATCHES give
    (None, True), since a superlative/comparative word was present and the
    caller must ask rather than silently drop it and guess."""
    if not word:
        return None, False
    columns_by_table = (metadata or {}).get("columns_by_table") or {}

    if table and table in columns_by_table:
        result = _resolver.resolve(word, list(columns_by_table[table]), entity_type="column")
        if result.is_success:
            return result.value, False
        return None, True

    matches = {}
    for tbl, cols in columns_by_table.items():
        result = _resolver.resolve(word, list(cols), entity_type="column")
        if result.is_success:
            matches[tbl] = result.value
    if len(matches) == 1:
        return next(iter(matches.values())), False
    return None, True
