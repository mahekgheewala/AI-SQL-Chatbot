import psycopg2
import re
import time
from typing import Optional
from connections.connection_manager import ConnectionManager
from db.app_database import SessionLocal
from utils.config_loader import get_superdb_name, get_max_result_rows
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

_STRING_LITERAL_FOR_LOGGING_RE = re.compile(r"'(?:[^']|'')*'")


def _redact_sql_for_logging(sql: str) -> str:
    """Replace the CONTENTS of quoted string literals with a fixed
    placeholder before this SQL is written to any log — a literal value
    (an SSN, an email, a name) embedded in generated SQL should not land in
    plaintext logs / log aggregation / backups. Every keyword, table name,
    column name, and the query's overall shape (which columns, which
    clauses, how many literals) stays fully intact, preserving debugging/
    audit value. This never touches the SQL that actually executes against
    the database — only the copy written to logs."""
    if not sql:
        return sql
    return _STRING_LITERAL_FOR_LOGGING_RE.sub("'***'", sql)


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
        # Database-enforced backstop (Issue 1): any statement that is
        # textually a SELECT gets a connection PostgreSQL itself has been
        # told is read-only (see ConnectionManager.get_connection's
        # read_only param) — independent of whatever the application's own
        # safety_checker decided upstream. If a write ever reaches here
        # disguised as/smuggled inside something is_select() calls a
        # SELECT (a stacking bypass, a "SELECT ... INTO" that actually
        # creates a table, a future regex gap), PostgreSQL rejects the
        # write outright instead of the app's own classification being the
        # only thing standing between the request and it actually running.
        conn = ConnectionManager.get_connection(
            user_id, db_session, target_database=dbname, is_autocommit=is_non_transactional,
            read_only=is_select,
        )
        if is_non_transactional and not conn.autocommit:
            conn.autocommit = True

        cursor = conn.cursor()
        cursor.execute(sql)

        if is_select:
            # Bounded fetch, not fetchall() — a query with no LIMIT (e.g.
            # "show me all orders") could otherwise pull millions of rows
            # into Python process memory and the HTTP response body. The
            # query itself still runs to completion server-side and
            # PostgreSQL still computes the real aggregate/grouped result
            # over the full table either way — this only caps how many
            # rows get pulled to the client, so COUNT/SUM/GROUP BY results
            # (already at most one row per group, essentially never near
            # this cap) are completely unaffected. Fetches one extra row
            # purely to detect truncation, then discards it — never
            # returned or counted.
            max_rows = get_max_result_rows()
            fetched = cursor.fetchmany(max_rows + 1)
            truncated = len(fetched) > max_rows
            rows = fetched[:max_rows]
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
            result["truncated"] = truncated
            if truncated:
                result["message"] = (
                    f"Query executed successfully. Showing the first {max_rows} rows "
                    f"— more rows were available but not returned."
                )
            else:
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
                    "sql": _redact_sql_for_logging(sql),
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

        # Issue 9: the app's cached schema (state/metadata_store.py) is
        # only refreshed when this app's own DDL runs, or when switching
        # to a database it hasn't cached before — staying on one database
        # means the schema is fetched once and trusted indefinitely. If a
        # table/column changed through any other means (a DBA, another
        # tool, another session), the cache doesn't know, and the AI keeps
        # generating SQL against the stale picture. PostgreSQL's own
        # undefined_column (42703) / undefined_table (42P01) SQLSTATEs are
        # an unambiguous, cheap signal that the cache disagrees with
        # reality — not e.g. a plain typo, which fails with the same kind
        # of error but refreshing wouldn't fix anyway — so the refresh is
        # scoped to only these two codes rather than every failure.
        if getattr(e, "pgcode", None) in ("42703", "42P01") and dbname:
            try:
                from state.metadata_store import set_cached_schema_db, sync_database_context
                set_cached_schema_db(None)
                sync_database_context(dbname)
                logger_query.info(
                    f"Schema cache invalidated for '{dbname}' after {e.pgcode} "
                    f"(undefined column/table) — likely stale metadata.",
                    extra={"category": "query", "database_name": dbname, "pgcode": e.pgcode},
                )
            except Exception:
                pass

        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_query.error(
                f"SQL query execution failed on database '{dbname}': {error_msg}",
                extra={
                    "category": "query",
                    "operation_type": operation,
                    "intent": intent,
                    "sql": _redact_sql_for_logging(sql),
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
                    "sql": _redact_sql_for_logging(sql),
                    "database_name": dbname,
                    "execution_time_ms": duration_ms,
                    "success": False,
                    "error": error_msg
                }
            )
        except Exception:
            pass

        # Same cleanup the psycopg2.Error branch above already does. A
        # non-driver exception (e.g. a bug in the result-row processing
        # code, after cursor.execute() already succeeded) previously left
        # this branch with no rollback at all — the connection could be
        # returned to the cache mid-transaction, relying entirely on a
        # LATER caller's own defensive idle-check (in
        # ConnectionManager.get_connection()) to eventually clean it up.
        # Every failure path now leaves the connection in a known-clean
        # state before it's ever reused.
        if conn and not is_non_transactional:
            try:
                conn.rollback()
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
