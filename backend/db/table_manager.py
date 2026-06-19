import re
import psycopg2
from db.connection import get_connection

# Reuse same validation pattern as database_manager for consistency
_VALID_IDENTIFIER = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*$')

# Strict whitelist of allowed PostgreSQL column types (as required by Phase 2 spec)
ALLOWED_COLUMN_TYPES = {"TEXT", "INTEGER", "BOOLEAN", "DATE", "FLOAT", "TIMESTAMP"}


def _validate_identifier(name: str) -> bool:
    """Return True if name is a safe PostgreSQL identifier."""
    return bool(_VALID_IDENTIFIER.match(name))


def fetch_tables(dbname: str) -> list[str]:
    """
    Return a list of all user-defined table names in the public schema
    of the given database.

    Uses information_schema.tables — the same standard source Phase 2 doc specifies.
    """
    conn = None
    try:
        conn = get_connection(dbname)
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name;
            """)
            return [row[0] for row in cursor.fetchall()]
    finally:
        if conn:
            conn.close()


def create_table(dbname: str, table_name: str, columns: list[dict]) -> None:
    """
    Dynamically generate and execute a CREATE TABLE statement.

    The function automatically prepends `id SERIAL PRIMARY KEY` as required
    by the Phase 2 spec. Each user-supplied column is validated:
      - Column name must match the identifier pattern (letters/numbers/underscores)
      - Column type must be in ALLOWED_COLUMN_TYPES

    Args:
        dbname:     Target database name.
        table_name: Name of the new table.
        columns:    List of dicts with keys 'name' (str) and 'type' (str).

    Raises:
        ValueError: If table/column name or type fails validation, or table exists.
        Exception:  Any psycopg2 connection / execution error is re-raised.
    """
    table_name = table_name.strip()
    if not _validate_identifier(table_name):
        raise ValueError(
            f"Invalid table name: '{table_name}'. "
            "Name must start with a letter and contain only letters, numbers, or underscores."
        )

    # Start column definitions with the mandatory auto-increment primary key
    col_defs = ["id SERIAL PRIMARY KEY"]

    for col in columns:
        col_name = col.get("name", "").strip()
        col_type = col.get("type", "").strip().upper()

        if not _validate_identifier(col_name):
            raise ValueError(
                f"Invalid column name: '{col_name}'. "
                "Name must start with a letter and contain only letters, numbers, or underscores."
            )
        if col_type not in ALLOWED_COLUMN_TYPES:
            raise ValueError(
                f"Invalid column type: '{col_type}'. "
                f"Allowed types: {', '.join(sorted(ALLOWED_COLUMN_TYPES))}."
            )
        col_defs.append(f'"{col_name}" {col_type}')

    sql = f'CREATE TABLE "{table_name}" ({", ".join(col_defs)})'

    conn = None
    try:
        conn = get_connection(dbname)
        with conn.cursor() as cursor:
            cursor.execute(sql)
        conn.commit()
    except psycopg2.errors.DuplicateTable:
        raise ValueError(f"Table '{table_name}' already exists in database '{dbname}'.")
    finally:
        if conn:
            conn.close()
