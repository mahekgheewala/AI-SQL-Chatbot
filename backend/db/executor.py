import psycopg2
import re
import time
from typing import Optional
from connections.connection_manager import ConnectionManager
from db.app_database import SessionLocal
from utils.config_loader import get_superdb_name
from utils.logging_config import database_name_var, logger_query, user_id_var

_NON_TRANSACTIONAL_PATTERN = re.compile(
    r'^\s*(?:'
    r'CREATE\s+DATABASE|DROP\s+DATABASE|'
    r'CREATE\s+TABLESPACE|DROP\s+TABLESPACE|'
    r'(?:CREATE|DROP)\s+(?:UNIQUE\s+)?INDEX\s+CONCURRENTLY|'
    r'VACUUM\b|ALTER\s+SYSTEM|'
    r'REINDEX\s+(?:SYSTEM|DATABASE|TABLE|INDEX)\s+CONCURRENTLY'
    r')\b',
    re.IGNORECASE | re.DOTALL
)

_SUPERDB_COMMAND_PATTERN = re.compile(
    r'^\s*(?:CREATE|DROP)\s+DATABASE\b',
    re.IGNORECASE | re.DOTALL
)

def _is_select(sql: str) -> bool:
    return sql.strip().upper().startswith("SELECT")

def _clean_sql(sql: str) -> str:
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    sql = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    return sql.strip()

def requires_autocommit(sql: str) -> bool:
    return bool(_NON_TRANSACTIONAL_PATTERN.search(_clean_sql(sql)))

def requires_superdb(sql: str) -> bool:
    return bool(_SUPERDB_COMMAND_PATTERN.search(_clean_sql(sql)))

_CREATE_TABLE_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_\"`\[\]\.]+)",
    re.IGNORECASE
)

_ALTER_TABLE_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?([a-zA-Z0-9_\"`\[\]\.]+)",
    re.IGNORECASE
)

def extract_table_name(sql: str) -> Optional[str]:
    cleaned = _clean_sql(sql)
    m = _CREATE_TABLE_RE.match(cleaned)
    if m:
        name = m.group(1)
        if "." in name:
            name = name.split(".")[-1]
        return name.strip('"`[]')
        
    m = _ALTER_TABLE_RE.match(cleaned)
    if m:
        name = m.group(1)
        if "." in name:
            name = name.split(".")[-1]
        return name.strip('"`[]')
        
    return None

def execute_sql(sql: str, dbname: str, intent: Optional[str] = None) -> dict:
    conn = None
    cursor = None
    db_session = SessionLocal()
    user_id = user_id_var.get()
    user_rlock = ConnectionManager.get_user_execution_lock(user_id) if user_id else None
    
    if user_rlock:
        user_rlock.acquire()
        
    cleaned_sql = _clean_sql(sql)
    is_select = _is_select(cleaned_sql)
    is_non_transactional = requires_autocommit(cleaned_sql)
    is_superdb = requires_superdb(cleaned_sql)
    
    words = cleaned_sql.split()
    operation = "UNKNOWN"
    if len(words) >= 2 and words[0].upper() in {"CREATE", "DROP", "ALTER", "REINDEX"}:
        if words[1].upper() in {"UNIQUE", "DATABASE", "TABLE", "INDEX", "SYSTEM", "TABLESPACE"}:
            if words[1].upper() == "UNIQUE" and len(words) >= 3:
                operation = f"{words[0].upper()} {words[1].upper()} {words[2].upper()}"
            else:
                operation = f"{words[0].upper()} {words[1].upper()}"
        else:
            operation = words[0].upper()
    elif words:
        operation = words[0].upper()

    result = {
        "success": False,
        "operation": operation,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "message": None,
        "error": None
    }

    if is_superdb:
        dbname = get_superdb_name()

    db_token = database_name_var.set(dbname)
    start_time = time.perf_counter()

    try:
        conn = ConnectionManager.get_connection(
            user_id, db_session, target_database=dbname, is_autocommit=is_non_transactional
        )
        if is_non_transactional and not conn.autocommit:
            conn.autocommit = True

        cursor = conn.cursor()
        cursor.execute(sql)

        if is_select:
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            
            column_types = {}
            if cursor.description:
                for desc in cursor.description:
                    column_types[desc[0]] = desc[1]
            
            result["success"] = True
            result["columns"] = columns
            result["rows"] = [list(row) for row in rows]
            result["row_count"] = len(rows)
            result["column_types"] = column_types
            result["message"] = "Query executed successfully."
            
        else:
            row_count = cursor.rowcount
            if row_count >= 0:
                result["row_count"] = row_count
            
            if not is_non_transactional:
                conn.commit()

            result["success"] = True
            result["message"] = f"Operation '{operation}' executed successfully."

            if operation == "CREATE DATABASE" and user_id:
                new_db_match = re.search(
                    r"CREATE\s+DATABASE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_\"'`]+)",
                    cleaned_sql, re.IGNORECASE
                )
                if new_db_match:
                    new_db_name = new_db_match.group(1).strip('`"\'')
                    try:
                        from services.connection_service import ConnectionService
                        ConnectionService(db_session).grant_database_access(user_id, new_db_name)
                    except Exception:
                        pass

        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_query.info(
                f"SQL query executed successfully on database '{dbname}'",
                extra={
                    "category": "query",
                    "operation_type": operation,
                    "intent": intent,
                    "sql": sql,
                    "database_name": dbname,
                    "execution_time_ms": duration_ms,
                    "row_count": result["row_count"],
                    "success": True
                }
            )
        except Exception:
            pass

    except psycopg2.Error as e:
        error_msg = str(e).strip()
        result["success"] = False
        result["error"] = error_msg
        
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_query.error(
                f"SQL query execution failed on database '{dbname}': {error_msg}",
                extra={
                    "category": "query",
                    "operation_type": operation,
                    "intent": intent,
                    "sql": sql,
                    "database_name": dbname,
                    "execution_time_ms": duration_ms,
                    "success": False,
                    "error": error_msg
                }
            )
        except Exception:
            pass

        if conn and not is_non_transactional:
            try:
                conn.rollback()
            except Exception:
                pass
                
    except Exception as e:
        error_msg = str(e)
        result["success"] = False
        result["error"] = f"Internal server error: {error_msg}"
        
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_query.error(
                f"SQL query execution failed on database '{dbname}' with unexpected error: {error_msg}",
                extra={
                    "category": "query",
                    "operation_type": operation,
                    "intent": intent,
                    "sql": sql,
                    "database_name": dbname,
                    "execution_time_ms": duration_ms,
                    "success": False,
                    "error": error_msg
                }
            )
        except Exception:
            pass
        
    finally:
        if cursor:
            cursor.close()
        if conn and is_non_transactional:
            try:
                if not conn.autocommit:
                    conn.rollback()
                conn.autocommit = False
            except Exception:
                pass
        db_session.close()
        database_name_var.reset(db_token)
        if user_rlock:
            user_rlock.release()

    return result
