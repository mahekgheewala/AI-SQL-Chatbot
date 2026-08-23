import re
import psycopg2
import time
from connections.connection_manager import ConnectionManager
from db.app_database import SessionLocal
from db.column_types import ALLOWED_COLUMN_TYPES, normalize_column_type, is_allowed_column_type
from utils.logging_config import logger_db_schema, database_name_var, selected_table_var, user_id_var

_VALID_IDENTIFIER = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*$')

def _validate_identifier(name: str) -> bool:
    return bool(_VALID_IDENTIFIER.match(name))

def fetch_tables(dbname: str) -> list[str]:
    start_time = time.perf_counter()
    conn = None
    db_session = SessionLocal()
    db_token = database_name_var.set(dbname)
    user_id = user_id_var.get()
    try:
        conn = ConnectionManager.get_connection(user_id, db_session, target_database=dbname)
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name;
            """)
            tables = [row[0] for row in cursor.fetchall()]
            
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_db_schema.info(
                f"Fetched tables list for database '{dbname}'",
                extra={
                    "category": "db_schema",
                    "operation_type": "FETCH_TABLES",
                    "database_name": dbname,
                    "execution_time_ms": duration_ms,
                    "tables_found": len(tables),
                    "success": True
                }
            )
        except Exception:
            pass
        return tables
    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_db_schema.error(
                f"Failed to fetch tables list for database '{dbname}': {str(e)}",
                exc_info=True,
                extra={
                    "category": "db_schema",
                    "operation_type": "FETCH_TABLES",
                    "database_name": dbname,
                    "execution_time_ms": duration_ms,
                    "success": False,
                    "error": str(e)
                }
            )
        except Exception:
            pass
        raise
    finally:
        db_session.close()
        database_name_var.reset(db_token)

def create_table(dbname: str, table_name: str, columns: list[dict]) -> None:
    table_name = table_name.strip()
    if not _validate_identifier(table_name):
        raise ValueError(
            f"Invalid table name: '{table_name}'. "
            "Name must start with a letter and contain only letters, numbers, or underscores."
        )

    col_defs = ["id SERIAL PRIMARY KEY"]

    for col in columns:
        col_name = col.get("name", "").strip()
        col_type = col.get("type", "").strip().upper()

        if not _validate_identifier(col_name):
            raise ValueError(
                f"Invalid column name: '{col_name}'. "
                "Name must start with a letter and contain only letters, numbers, or underscores."
            )
        if not is_allowed_column_type(col_type):
            raise ValueError(
                f"Invalid column type: '{col_type}'. "
                f"Allowed types: {', '.join(sorted(ALLOWED_COLUMN_TYPES))}."
            )
        col_defs.append(f'"{col_name}" {col_type}')

    sql = f'CREATE TABLE "{table_name}" ({", ".join(col_defs)})'

    start_time = time.perf_counter()
    db_token = database_name_var.set(dbname)
    tbl_token = selected_table_var.set(table_name)
    user_id = user_id_var.get()
    try:
        logger_db_schema.info(
            f"Table creation started: '{table_name}' in database '{dbname}'",
            extra={
                "category": "db_schema",
                "operation_type": "CREATE_TABLE",
                "database_name": dbname,
                "selected_table": table_name,
                "columns": columns,
                "success": True
            }
        )
    except Exception:
        pass

    conn = None
    db_session = SessionLocal()
    try:
        conn = ConnectionManager.get_connection(user_id, db_session, target_database=dbname)
        with conn.cursor() as cursor:
            cursor.execute(sql)
        conn.commit()
        
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_db_schema.info(
                f"Table '{table_name}' created successfully in database '{dbname}'",
                extra={
                    "category": "db_schema",
                    "operation_type": "CREATE_TABLE",
                    "database_name": dbname,
                    "selected_table": table_name,
                    "execution_time_ms": duration_ms,
                    "success": True
                }
            )
        except Exception:
            pass
    except psycopg2.errors.DuplicateTable:
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_db_schema.error(
                f"Table creation failed: Table '{table_name}' already exists in database '{dbname}'",
                extra={
                    "category": "db_schema",
                    "operation_type": "CREATE_TABLE",
                    "database_name": dbname,
                    "selected_table": table_name,
                    "execution_time_ms": duration_ms,
                    "success": False,
                    "error": "Table already exists"
                }
            )
        except Exception:
            pass
        raise ValueError(f"Table '{table_name}' already exists in database '{dbname}'.")
    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_db_schema.error(
                f"Table creation failed for '{table_name}' in database '{dbname}': {str(e)}",
                exc_info=True,
                extra={
                    "category": "db_schema",
                    "operation_type": "CREATE_TABLE",
                    "database_name": dbname,
                    "selected_table": table_name,
                    "execution_time_ms": duration_ms,
                    "success": False,
                    "error": str(e)
                }
            )
        except Exception:
            pass
        raise
    finally:
        db_session.close()
        database_name_var.reset(db_token)
        selected_table_var.reset(tbl_token)
