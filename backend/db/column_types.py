"""Single source of truth for the SQL column-type whitelist.

Previously duplicated (and drifted out of sync) between db/table_manager.py
(6 types) and validation/schema_creator_validator.py (14 types) — the latter
accepted column types the former would reject, so DDL that passed Gate 3
validation could still fail at actual table-creation time.
"""

ALLOWED_COLUMN_TYPES: set[str] = {
    "TEXT",
    "INTEGER",
    "SERIAL",       # Common PostgreSQL auto-increment type
    "BIGINT",
    "BOOLEAN",
    "DATE",
    "FLOAT",
    "NUMERIC",
    "DECIMAL",
    "TIMESTAMP",
    "VARCHAR",      # Permitted even without length — Gemini uses VARCHAR for strings
    "CHAR",
    "JSON",
    "JSONB",
    "UUID",
}


def normalize_column_type(col_type: str) -> str:
    """Strip an optional length/precision specifier, e.g. "VARCHAR(255)" -> "VARCHAR"."""
    return col_type.strip().split("(")[0].strip().upper()


def is_allowed_column_type(col_type: str) -> bool:
    return normalize_column_type(col_type) in ALLOWED_COLUMN_TYPES
