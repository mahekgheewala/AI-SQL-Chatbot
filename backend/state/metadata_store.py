from collections import defaultdict
from utils.logging_config import metadata_version_var, user_id_var

def _default_store():
    return {
        "databases": [],
        "selected_db": None,
        "schema": {},
        "tables": [],
        "table_schemas": {},
        "routing_summaries": {},
        "cached_schema_db": None,
    }

_user_stores = defaultdict(_default_store)
_cache_generation = 0

def get_metadata() -> dict:
    user_id = user_id_var.get()
    return _user_stores[user_id]

def get_cache_generation() -> int:
    global _cache_generation
    return _cache_generation

def _increment_cache_generation() -> None:
    global _cache_generation
    _cache_generation += 1
    try:
        metadata_version_var.set(_cache_generation)
    except Exception:
        pass

def set_databases(dbs: list[str]) -> None:
    user_id = user_id_var.get()
    _user_stores[user_id]["databases"] = dbs
    _increment_cache_generation()

def set_selected_db(name: str | None) -> None:
    sync_database_context(name)

def sync_database_context(db_name: str | None) -> None:
    user_id = user_id_var.get()
    store = _user_stores[user_id]
    store["selected_db"] = db_name
    
    if not db_name:
        store["cached_schema_db"] = None
        store["schema"] = {}
        store["tables"] = []
        store["table_schemas"] = {}
        store["foreign_keys"] = {}
        return
        
    if db_name != store.get("cached_schema_db"):
        from db.schema_fetcher import fetch_schema, fetch_table_schemas, fetch_foreign_keys
        from db.table_manager import fetch_tables
        
        try:
            schema = fetch_schema(db_name)
            tables = fetch_tables(db_name)
            table_schemas = fetch_table_schemas(db_name)
            foreign_keys = fetch_foreign_keys(db_name)
            store["schema"] = schema
            store["tables"] = tables
            store["table_schemas"] = table_schemas
            store["foreign_keys"] = foreign_keys
            store["cached_schema_db"] = db_name
            _increment_cache_generation()
        except Exception as e:
            print(f"[metadata_store] Error synchronizing schema cache for {db_name} (User {user_id}): {e}")
            store["cached_schema_db"] = db_name
    else:
        store["cached_schema_db"] = db_name

def set_schema(schema: dict[str, list[str]]) -> None:
    user_id = user_id_var.get()
    _user_stores[user_id]["schema"] = schema
    _increment_cache_generation()

def set_tables(tables: list[str]) -> None:
    user_id = user_id_var.get()
    _user_stores[user_id]["tables"] = tables
    _increment_cache_generation()

def set_table_schemas(schemas: dict) -> None:
    user_id = user_id_var.get()
    _user_stores[user_id]["table_schemas"] = schemas
    _increment_cache_generation()

def set_cached_schema_db(name: str | None) -> None:
    user_id = user_id_var.get()
    _user_stores[user_id]["cached_schema_db"] = name
    _increment_cache_generation()

def get_cached_schema_db() -> str | None:
    user_id = user_id_var.get()
    return _user_stores[user_id].get("cached_schema_db")

def clear() -> None:
    user_id = user_id_var.get()
    _user_stores[user_id] = _default_store()
    _increment_cache_generation()

def set_routing_summaries(summaries: dict) -> None:
    user_id = user_id_var.get()
    _user_stores[user_id]["routing_summaries"] = summaries
    _increment_cache_generation()

def refresh_routing_summaries() -> None:
    from db.schema_fetcher import fetch_schema
    user_id = user_id_var.get()
    store = _user_stores[user_id]
    databases = store.get("databases", [])
    summaries: dict[str, list[str]] = {}

    for db_name in databases:
        try:
            schema = fetch_schema(db_name)
            summaries[db_name] = list(schema.keys())
        except Exception:
            summaries[db_name] = []

    store["routing_summaries"] = summaries
    _increment_cache_generation()

def refresh_metadata_after_ddl(sql: str, target_db: str | None, operation: str, session_id: str | None = None) -> None:
    import re
    from db.executor import _clean_sql
    user_id = user_id_var.get()
    store = _user_stores[user_id]
    cleaned_sql = _clean_sql(sql)
    op = (operation or "").upper()
    is_db_change = (op == "CREATE DATABASE" or op == "DROP DATABASE" or (op == "DROP" and "DATABASE" in cleaned_sql.upper()))

    if is_db_change:
        match = re.search(
            r"(?:CREATE|DROP)\s+DATABASE\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?([a-zA-Z0-9_\"'`]+)",
            cleaned_sql,
            re.IGNORECASE
        )
        changed_db = match.group(1).strip('`"\'') if match else None

        from db.schema_fetcher import fetch_all_databases
        try:
            dbs = fetch_all_databases()
            set_databases(dbs)
        except Exception:
            pass
        refresh_routing_summaries()

        if changed_db:
            if (store.get("selected_db") or "").lower() == changed_db.lower():
                sync_database_context(None)
            if (store.get("cached_schema_db") or "").lower() == changed_db.lower():
                store["schema"] = {}
                store["tables"] = []
                store["table_schemas"] = {}
                store["cached_schema_db"] = None
            store["databases"] = [d for d in store.get("databases", []) if d.lower() != changed_db.lower()]
            store["routing_summaries"] = {
                k: v for k, v in store.get("routing_summaries", {}).items() if k.lower() != changed_db.lower()
            }
            _increment_cache_generation()
    elif (op.split()[0] if op else "") in {"CREATE", "ALTER", "DROP"}:
        # `op` is the two-word form ("CREATE TABLE", "ALTER TABLE", ...) for
        # every table-level DDL statement (see db/executor.py's operation
        # parsing), so this must match on the leading verb, not the whole
        # string — matching on the whole string meant this branch never fired
        # and table-level DDL never refreshed routing_summaries or the schema
        # cache.
        refresh_routing_summaries()
        store = _user_stores[user_id]
        if target_db:
            from db.schema_fetcher import fetch_schema, fetch_table_schemas
            from db.table_manager import fetch_tables
            try:
                tables = fetch_tables(target_db)
                schema = fetch_schema(target_db)
                table_schemas = fetch_table_schemas(target_db)
                set_tables(tables)
                set_schema(schema)
                set_table_schemas(table_schemas)
                store["cached_schema_db"] = target_db
            except Exception:
                pass
