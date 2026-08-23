import re
import psycopg2
import time
from connections.connection_manager import ConnectionManager
from db.app_database import SessionLocal
from utils.config_loader import get_superdb_name
from utils.logging_config import logger_db_schema, database_name_var, user_id_var

_VALID_IDENTIFIER = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*$')

def validate_db_name(name: str) -> bool:
    return bool(_VALID_IDENTIFIER.match(name))

def create_database(name: str) -> None:
    name = name.strip()
    if not validate_db_name(name):
        raise ValueError(
            f"Invalid database name: '{name}'. "
            "Name must start with a letter and contain only letters, numbers, or underscores."
        )

    start_time = time.perf_counter()
    try:
        logger_db_schema.info(
            f"Database creation started: '{name}'",
            extra={
                "category": "db_schema",
                "operation_type": "CREATE_DATABASE",
                "database_name": name,
                "success": True
            }
        )
    except Exception:
        pass

    super_db = get_superdb_name()
    conn = None
    db_session = SessionLocal()
    db_token = database_name_var.set(name)
    user_id = user_id_var.get()
    try:
        conn = ConnectionManager.get_connection(
            user_id, db_session, target_database=super_db, is_autocommit=True
        )
        if not conn.autocommit:
            conn.autocommit = True
        with conn.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{name}"')
            
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_db_schema.info(
                f"Database '{name}' created successfully",
                extra={
                    "category": "db_schema",
                    "operation_type": "CREATE_DATABASE",
                    "database_name": name,
                    "execution_time_ms": duration_ms,
                    "success": True
                }
            )
        except Exception:
            pass
    except psycopg2.errors.DuplicateDatabase:
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_db_schema.error(
                f"Database creation failed: Database '{name}' already exists",
                extra={
                    "category": "db_schema",
                    "operation_type": "CREATE_DATABASE",
                    "database_name": name,
                    "execution_time_ms": duration_ms,
                    "success": False,
                    "error": "Database already exists"
                }
            )
        except Exception:
            pass
        raise ValueError(f"Database '{name}' already exists.")
    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_db_schema.error(
                f"Database creation failed for '{name}': {str(e)}",
                exc_info=True,
                extra={
                    "category": "db_schema",
                    "operation_type": "CREATE_DATABASE",
                    "database_name": name,
                    "execution_time_ms": duration_ms,
                    "success": False,
                    "error": str(e)
                }
            )
        except Exception:
            pass
        raise
    finally:
        if conn:
            try:
                conn.rollback()
                conn.autocommit = False
            except:
                pass
        db_session.close()
        database_name_var.reset(db_token)
