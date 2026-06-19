import re
import os
import psycopg2
from db.connection import get_connection

# Only allow names that start with a letter and contain letters, numbers, or underscores.
# This whitelist pattern prevents SQL injection in database/table name positions
# where parameterized queries cannot be used.
_VALID_IDENTIFIER = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*$')


def validate_db_name(name: str) -> bool:
    """Return True if name is a safe PostgreSQL identifier."""
    return bool(_VALID_IDENTIFIER.match(name))


def create_database(name: str) -> None:
    """
    Dynamically create a new PostgreSQL database.

    PostgreSQL requires autocommit=True before issuing CREATE DATABASE —
    it cannot run inside a transaction block. We set conn.autocommit = True
    immediately after opening the connection and before executing the statement.

    The connection is made to the admin/superdb (DB_SUPERDB from .env) so
    we can issue a server-level DDL command.

    Raises:
        ValueError: if the database name fails validation or already exists.
        Exception:  any other psycopg2 / connection error is re-raised.
    """
    name = name.strip()
    if not validate_db_name(name):
        raise ValueError(
            f"Invalid database name: '{name}'. "
            "Name must start with a letter and contain only letters, numbers, or underscores."
        )

    super_db = os.getenv("DB_SUPERDB", "postgres")
    conn = None
    try:
        conn = get_connection(super_db)
        conn.autocommit = True          # Required: CREATE DATABASE cannot run in a transaction
        with conn.cursor() as cursor:
            # Using quoted identifier is safe here because we validated name above.
            cursor.execute(f'CREATE DATABASE "{name}"')
    except psycopg2.errors.DuplicateDatabase:
        raise ValueError(f"Database '{name}' already exists.")
    finally:
        if conn:
            conn.close()
