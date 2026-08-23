"""
Phase 10.5 — Metadata Registry

Single source of truth for schema-level metadata profiles.

All modules (EntityResolver, CompatibilityValidator, IntentEngine, Analytics,
Visualization) must use this service instead of performing their own schema
lookups or type inferences.

Reads directly from the existing metadata_store cache so it never issues extra
database queries.
"""

from typing import Optional


# ---------------------------------------------------------------------------
# Column Capability Profile
# ---------------------------------------------------------------------------

class ColumnProfile:
    """
    Structured capability profile for a single column.

    Consumers should read these flags instead of re-deriving type properties
    from raw data-type strings.
    """

    # Postgres data-type sets
    _NUMERIC_TYPES = {
        "integer", "int", "int2", "int4", "int8",
        "bigint", "smallint",
        "numeric", "decimal",
        "real", "float", "float4", "float8",
        "double precision",
        "serial", "bigserial", "smallserial",
        "money",
    }
    _DATE_TIME_TYPES = {
        "date", "time", "timetz",
        "timestamp", "timestamptz",
        "timestamp without time zone",
        "timestamp with time zone",
        "interval",
    }
    _TEXT_TYPES = {
        "text", "varchar", "character varying",
        "char", "character",
        "name", "citext",
        "uuid",
    }
    _BOOLEAN_TYPES = {"boolean", "bool"}

    def __init__(self, column_name: str, raw_data_type: str):
        self.column_name: str = column_name

        # Normalize the raw type string (strip precision/scale e.g. "varchar(255)")
        base = raw_data_type.lower().strip()
        # remove "(…)" suffix
        paren = base.find("(")
        if paren != -1:
            base = base[:paren].strip()
        self.data_type: str = base

        self.is_numeric: bool = base in self._NUMERIC_TYPES
        self.is_date_time: bool = base in self._DATE_TIME_TYPES
        self.is_boolean: bool = base in self._BOOLEAN_TYPES
        self.is_text: bool = base in self._TEXT_TYPES or (
            not self.is_numeric and not self.is_date_time and not self.is_boolean
        )

        # Derived capability flags consumed by Analytics, Visualization, etc.
        self.is_categorical: bool = self.is_text or self.is_boolean
        self.is_aggregatable: bool = self.is_numeric          # SUM / AVG / MIN / MAX
        self.is_sortable: bool = True                          # every column can ORDER BY
        self.is_plottable_numeric: bool = self.is_numeric      # Y-axis / value axis
        self.is_plottable_categorical: bool = self.is_categorical  # X-axis / category axis

    def __repr__(self) -> str:  # pragma: no cover
        flags = []
        if self.is_numeric:
            flags.append("numeric")
        if self.is_categorical:
            flags.append("categorical")
        if self.is_date_time:
            flags.append("datetime")
        if self.is_aggregatable:
            flags.append("aggregatable")
        return f"<ColumnProfile {self.column_name!r} [{self.data_type}] ({', '.join(flags)})>"


# ---------------------------------------------------------------------------
# Metadata Registry
# ---------------------------------------------------------------------------

class MetadataRegistry:
    """
    Unified metadata service for the active database.

    Reads from metadata_store's in-memory cache (schema + table_schemas) so
    no additional database round-trips are made.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, db_name: Optional[str] = None):
        """
        Parameters
        ----------
        db_name:
            Optional database override.  When None the currently selected
            database from metadata_store is used.
        """
        self._db_name = db_name
        self._schema: dict[str, list[str]] = {}
        self._table_schemas: dict[str, dict[str, str]] = {}
        self._profile_cache: dict[tuple[str, str], ColumnProfile] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load from metadata_store cache on first access."""
        if self._loaded:
            return
        from state.metadata_store import get_metadata, sync_database_context

        meta = get_metadata()

        # If a specific database was requested, make sure its schema is cached
        if self._db_name and meta.get("cached_schema_db") != self._db_name:
            sync_database_context(self._db_name)
            meta = get_metadata()

        self._schema = meta.get("schema") or {}
        self._table_schemas = meta.get("table_schemas") or {}
        self._loaded = True

    # ------------------------------------------------------------------
    # Table-level accessors
    # ------------------------------------------------------------------

    def get_tables(self) -> list[str]:
        """Return all table names in the active database."""
        self._ensure_loaded()
        return list(self._schema.keys())

    def table_exists(self, table_name: str) -> bool:
        self._ensure_loaded()
        return table_name in self._schema

    # ------------------------------------------------------------------
    # Column-level accessors
    # ------------------------------------------------------------------

    def get_columns(self, table_name: str) -> list[str]:
        """Return all column names for *table_name*."""
        self._ensure_loaded()
        return list(self._schema.get(table_name, []))

    def get_column_type(self, table_name: str, column_name: str) -> Optional[str]:
        """Return the raw Postgres data type string, or None if not found."""
        self._ensure_loaded()
        return self._table_schemas.get(table_name, {}).get(column_name)

    def column_exists(self, table_name: str, column_name: str) -> bool:
        self._ensure_loaded()
        return column_name in self._schema.get(table_name, [])

    # ------------------------------------------------------------------
    # Rich capability profiles
    # ------------------------------------------------------------------

    def get_column_profile(self, table_name: str, column_name: str) -> Optional[ColumnProfile]:
        """
        Return a rich ColumnProfile for *column_name* in *table_name*.

        Returns None if the table or column does not exist.
        """
        self._ensure_loaded()
        key = (table_name, column_name)
        if key not in self._profile_cache:
            raw_type = self.get_column_type(table_name, column_name)
            if raw_type is None:
                return None
            self._profile_cache[key] = ColumnProfile(column_name, raw_type)
        return self._profile_cache[key]

    def is_numeric(self, table_name: str, column_name: str) -> bool:
        profile = self.get_column_profile(table_name, column_name)
        return profile.is_numeric if profile else False

    def is_categorical(self, table_name: str, column_name: str) -> bool:
        profile = self.get_column_profile(table_name, column_name)
        return profile.is_categorical if profile else False

    def is_date_time(self, table_name: str, column_name: str) -> bool:
        profile = self.get_column_profile(table_name, column_name)
        return profile.is_date_time if profile else False

    def is_aggregatable(self, table_name: str, column_name: str) -> bool:
        profile = self.get_column_profile(table_name, column_name)
        return profile.is_aggregatable if profile else False

    # ------------------------------------------------------------------
    # Table-level helpers consumed by other services
    # ------------------------------------------------------------------

    def get_numeric_columns(self, table_name: str) -> list[str]:
        """All numeric columns in *table_name*."""
        return [c for c in self.get_columns(table_name) if self.is_numeric(table_name, c)]

    def get_categorical_columns(self, table_name: str) -> list[str]:
        """All categorical (text / boolean) columns in *table_name*."""
        return [c for c in self.get_columns(table_name) if self.is_categorical(table_name, c)]

    def get_datetime_columns(self, table_name: str) -> list[str]:
        """All date/time columns in *table_name*."""
        return [c for c in self.get_columns(table_name) if self.is_date_time(table_name, c)]

    def get_schema_summary(self, table_name: str) -> dict:
        """
        Return a summary dict for *table_name* with rich profiles for each
        column.  Consumers (e.g. the SQL Builder, Visualization Engine) can
        use this instead of repeated individual lookups.
        """
        self._ensure_loaded()
        columns = self.get_columns(table_name)
        return {
            "table": table_name,
            "columns": {
                col: vars(self.get_column_profile(table_name, col))
                for col in columns
                if self.get_column_profile(table_name, col)
            },
        }

    def invalidate(self) -> None:
        """Force reload on next access (e.g. after a DDL operation)."""
        self._loaded = False
        self._profile_cache.clear()


# ---------------------------------------------------------------------------
# Module-level convenience factory
# ---------------------------------------------------------------------------

def get_registry(db_name: Optional[str] = None) -> MetadataRegistry:
    """
    Return a fresh (lazily-loaded) MetadataRegistry for *db_name*.

    Callers should create one registry per request rather than holding a
    long-lived instance so that DDL changes are always reflected.
    """
    return MetadataRegistry(db_name=db_name)
