"""
In-memory metadata store — single source of truth for the current session.

Lives at the module level so it persists across requests within a single
server process. It resets on server restart. Phase 6 (Memory System) will
extend this pattern for persistent conversation history.

Fields:
  databases      — List[str]  — all available PostgreSQL database names
  selected_db    — str | None — currently active database
  schema         — Dict[str, List[str]] — table → columns for selected DB
  tables         — List[str]  — table names for the currently selected DB
  table_schemas  — Dict[str, Dict] — per-table metadata (reserved for Phase 3+)
"""

_store = {
    "databases": [],
    "selected_db": None,
    "schema": {},
    "tables": [],           # Phase 2 Additional: list of tables in selected DB
    "table_schemas": {},    # Phase 2 Additional: reserved for per-table detail
    "routing_summaries": {}, # Phase 7: { db_name: [table1, table2, ...] } for AI routing
    "cached_schema_db": None, # Phase 8.2: tracks which database the cached schema/tables belong to
}


def get_metadata() -> dict:
    return _store


def set_databases(dbs: list[str]) -> None:
    _store["databases"] = dbs


def set_selected_db(name: str | None) -> None:
    sync_database_context(name)


def sync_database_context(db_name: str | None) -> None:
    """
    Authoritatively synchronize the database context:
    1. Set selected_db to db_name
    2. Fetch and cache the schema, tables, and table_schemas for db_name if not already cached.
    """
    _store["selected_db"] = db_name
    if not db_name:
        _store["cached_schema_db"] = None
        _store["schema"] = {}
        _store["tables"] = []
        _store["table_schemas"] = {}
        return
        
    if db_name != _store.get("cached_schema_db"):
        from db.schema_fetcher import fetch_schema, fetch_table_schemas
        from db.table_manager import fetch_tables
        try:
            print(f"[metadata_store] Synchronizing schema cache for: {db_name}...")
            schema = fetch_schema(db_name)
            tables = fetch_tables(db_name)
            table_schemas = fetch_table_schemas(db_name)
            _store["schema"] = schema
            _store["tables"] = tables
            _store["table_schemas"] = table_schemas
            _store["cached_schema_db"] = db_name
        except Exception as e:
            print(f"[metadata_store] Error synchronizing schema cache for {db_name}: {e}")
            _store["cached_schema_db"] = db_name
    else:
        _store["cached_schema_db"] = db_name


def set_schema(schema: dict[str, list[str]]) -> None:
    _store["schema"] = schema


def set_tables(tables: list[str]) -> None:
    """Cache the list of table names for the currently selected database."""
    _store["tables"] = tables


def set_table_schemas(schemas: dict) -> None:
    """Cache per-table detailed metadata (used from Phase 3 onward)."""
    _store["table_schemas"] = schemas


def set_cached_schema_db(name: str | None) -> None:
    """Explicitly set which database the cached schema/tables belong to."""
    _store["cached_schema_db"] = name


def get_cached_schema_db() -> str | None:
    """Get the database name for which the schema/tables are currently cached."""
    return _store.get("cached_schema_db")


def clear() -> None:
    """Reset the entire store — called on server restart or DB deselect."""
    _store["databases"] = []
    _store["selected_db"] = None
    _store["schema"] = {}
    _store["tables"] = []
    _store["table_schemas"] = {}
    _store["routing_summaries"] = {}   # Phase 7
    _store["cached_schema_db"] = None



# ─── Phase 7 — Routing Summaries ──────────────────────────────────────────────────

def set_routing_summaries(summaries: dict) -> None:
    """
    Cache the cross-database routing index.
    Format: { db_name: [table1, table2, ...] }
    Separate from the full schema — only table names, no columns.
    """
    _store["routing_summaries"] = summaries


def refresh_routing_summaries() -> None:
    """
    Lazily rebuild the routing summaries by fetching table names for every
    known database. Called only when the database/table structure changes
    (CREATE DATABASE, DROP DATABASE, CREATE TABLE, DROP TABLE).
    Not called on every /chat request.
    """
    from db.schema_fetcher import fetch_schema

    databases = _store.get("databases", [])
    summaries: dict[str, list[str]] = {}

    for db_name in databases:
        try:
            schema = fetch_schema(db_name)   # returns {table: [columns...]}
            summaries[db_name] = list(schema.keys())
        except Exception as e:
            print(f"[Phase 7] Could not fetch tables for '{db_name}': {e}")
            summaries[db_name] = []

    _store["routing_summaries"] = summaries
    print(f"[Phase 7] Routing summaries refreshed: {list(summaries.keys())}")

def refresh_metadata_after_ddl(sql: str, target_db: str | None, operation: str) -> None:
    """
    Refresh routing summaries and schema caches dynamically after a successful DDL statement.
    """
    import re
    from db.executor import _clean_sql
    cleaned_sql = _clean_sql(sql)
    op = (operation or "").upper()
    is_db_change = (
        op == "CREATE DATABASE"
        or op == "DROP DATABASE"
        or (op == "DROP" and "DATABASE" in cleaned_sql.upper())
    )
    
    if is_db_change:
        from db.schema_fetcher import fetch_all_databases
        try:
            dbs = fetch_all_databases()
            set_databases(dbs)
        except Exception as e:
            print(f"[Phase 8.10] Error fetching databases: {e}")
        refresh_routing_summaries()
        
        # Invalidate cache if the currently selected or cached DB was dropped
        is_drop_db = (
            op == "DROP DATABASE"
            or (op == "DROP" and "DATABASE" in cleaned_sql.upper())
        )
        if is_drop_db:
            match = re.search(r"DROP\s+DATABASE\s+(?:IF\s+EXISTS\s+)?([a-zA-Z0-9_\"'`]+)", cleaned_sql, re.IGNORECASE)
            if match:
                dropped_db = match.group(1).strip('`"\'').lower()
                selected_db = _store.get("selected_db")
                cached_schema_db = _store.get("cached_schema_db")
                
                if (selected_db and selected_db.lower() == dropped_db) or (cached_schema_db and cached_schema_db.lower() == dropped_db):
                    print(f"[metadata_store] Dropped active database '{dropped_db}' - clearing schema cache context.")
                    _store["selected_db"] = None
                    _store["cached_schema_db"] = None
                    _store["schema"] = {}
                    _store["tables"] = []
                    _store["table_schemas"] = {}
        
    elif op in {"CREATE", "ALTER", "DROP"}:
        refresh_routing_summaries()
        if target_db and target_db == _store.get("cached_schema_db"):
            from db.schema_fetcher import fetch_schema, fetch_table_schemas
            from db.table_manager import fetch_tables
            try:
                tables = fetch_tables(target_db)
                schema = fetch_schema(target_db)
                table_schemas = fetch_table_schemas(target_db)
                set_tables(tables)
                set_schema(schema)
                set_table_schemas(table_schemas)
            except Exception as e:
                print(f"[Phase 8.10] Error refreshing tables/schema for selected db '{target_db}': {e}")


