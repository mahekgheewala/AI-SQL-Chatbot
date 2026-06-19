from db.connection import get_connection
import os

def fetch_all_databases() -> list[str]:
    super_db = os.getenv("DB_SUPERDB", "postgres")
    conn = None
    cursor = None
    try:
        conn = get_connection(super_db)
        cursor = conn.cursor()
        query = """
            SELECT datname FROM pg_database
            WHERE datistemplate = false AND datname NOT IN ('postgres')
            ORDER BY datname;
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        return [row[0] for row in rows]
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def fetch_schema(dbname: str) -> dict[str, list[str]]:
    conn = None
    cursor = None
    try:
        conn = get_connection(dbname)
        cursor = conn.cursor()
        query = """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position;
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        
        schema = {}
        for table_name, column_name in rows:
            if table_name not in schema:
                schema[table_name] = []
            schema[table_name].append(column_name)
        return schema
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def format_schema_for_prompt(schema: dict[str, list[str]]) -> str:
    lines = []
    for table, columns in schema.items():
        lines.append(f"Table: {table}")
        lines.append(f"Columns: {', '.join(columns)}")
    return "\n".join(lines)

def fetch_table_schemas(dbname: str) -> dict[str, dict[str, str]]:
    """
    Fetch column names and their data types for all tables in the public schema.
    Returns: { table_name: { column_name: data_type } }
    """
    conn = None
    cursor = None
    try:
        conn = get_connection(dbname)
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
        if conn:
            conn.close()
