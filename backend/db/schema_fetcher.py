from connections.connection_manager import ConnectionManager
from db.app_database import SessionLocal
from utils.logging_config import user_id_var

def fetch_all_databases() -> list[str]:
    """
    Returns the databases this user may actually work with: their own
    default_database plus any explicitly ACL'd allowed_databases (Phase 9.6),
    intersected with what genuinely exists on the server.

    Previously this queried every non-template database on the shared
    Postgres server and excluded whichever one DB_SUPERDB pointed to. That
    leaked every other user's/test's databases into this user's routing
    context, and — if a user's own default_database happened to share its
    name with DB_SUPERDB — made their own database invisible everywhere
    (list databases/tables, database switching, schema-aware SQL
    generation all read from this list).
    """
    conn = None
    cursor = None
    db_session = SessionLocal()
    user_id = user_id_var.get()
    try:
        conn = ConnectionManager.get_connection(user_id, db_session)
        allowed = ConnectionManager.get_allowed_databases(user_id, db_session)
        allowed_lower = {name.lower() for name in allowed}

        cursor = conn.cursor()
        cursor.execute("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;")
        server_dbs = [row[0] for row in cursor.fetchall()]

        return [name for name in server_dbs if name.lower() in allowed_lower]
    finally:
        if cursor:
            cursor.close()
        db_session.close()

def fetch_schema(dbname: str) -> dict[str, list[str]]:
    conn = None
    cursor = None
    db_session = SessionLocal()
    user_id = user_id_var.get()
    try:
        conn = ConnectionManager.get_connection(user_id, db_session, target_database=dbname)
        cursor = conn.cursor()

        # Seed every table first (including columnless ones — e.g. a table
        # right after CREATE TABLE x() before any ALTER TABLE ADD COLUMN).
        # Deriving the table list purely from information_schema.columns, as
        # this used to, silently drops any table with zero columns from
        # every listing, the schema sent to the AI, and entity resolution.
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;")
        schema: dict[str, list[str]] = {row[0]: [] for row in cursor.fetchall()}

        cursor.execute("""
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position;
        """)
        for table_name, column_name in cursor.fetchall():
            schema.setdefault(table_name, []).append(column_name)
        return schema
    finally:
        if cursor:
            cursor.close()
        db_session.close()

def format_schema_for_prompt(schema: dict, table_schemas: dict | None = None, foreign_keys: dict | None = None) -> str:
    lines = []
    for table, columns in schema.items():
        lines.append(f"Table: {table}")
        if table_schemas and table in table_schemas:
            cols_fmt = ", ".join([f"{col} ({table_schemas[table].get(col, 'text')})" for col in columns])
            lines.append(f"Columns: {cols_fmt}")
        elif isinstance(columns, dict):
            cols_fmt = ", ".join([f"{col} ({dtype})" for col, dtype in columns.items()])
            lines.append(f"Columns: {cols_fmt}")
        else:
            lines.append(f"Columns: {', '.join(columns)}")
        if foreign_keys and table in foreign_keys:
            fk_strs = [f"{fk['column']} -> {fk['foreign_table']}.{fk['foreign_column']}" for fk in foreign_keys[table]]
            lines.append(f"Foreign Keys: {', '.join(fk_strs)}")
    return "\n".join(lines)

def fetch_table_schemas(dbname: str) -> dict[str, dict[str, str]]:
    conn = None
    cursor = None
    db_session = SessionLocal()
    user_id = user_id_var.get()
    try:
        conn = ConnectionManager.get_connection(user_id, db_session, target_database=dbname)
        cursor = conn.cursor()
        query = """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position;
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        
        table_schemas = {}
        for table_name, column_name, data_type in rows:
            if table_name not in table_schemas:
                table_schemas[table_name] = {}
            table_schemas[table_name][column_name] = data_type
        return table_schemas
    finally:
        if cursor:
            cursor.close()
        db_session.close()

def fetch_foreign_keys(dbname: str) -> dict[str, list[dict[str, str]]]:
    conn = None
    cursor = None
    db_session = SessionLocal()
    user_id = user_id_var.get()
    try:
        conn = ConnectionManager.get_connection(user_id, db_session, target_database=dbname)
        cursor = conn.cursor()
        query = """
            SELECT
                tc.table_name,
                kcu.column_name,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name
            FROM
                information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                  ON ccu.constraint_name = tc.constraint_name
                  AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public';
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        
        foreign_keys: dict[str, list[dict[str, str]]] = {}
        for table_name, column_name, foreign_table_name, foreign_column_name in rows:
            if table_name not in foreign_keys:
                foreign_keys[table_name] = []
            foreign_keys[table_name].append({
                "column": column_name,
                "foreign_table": foreign_table_name,
                "foreign_column": foreign_column_name
            })
        return foreign_keys
    except Exception:
        return {}
    finally:
        if cursor:
            cursor.close()
        db_session.close()

