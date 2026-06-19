import psycopg2
import re
from typing import Optional
from db.connection import get_connection

# Matches PostgreSQL commands that cannot run inside a transaction block
_NON_TRANSACTIONAL_PATTERN = re.compile(
    r'^\s*(?:'
    r'CREATE\s+DATABASE|'
    r'DROP\s+DATABASE|'
    r'(?:CREATE|DROP)\s+(?:UNIQUE\s+)?INDEX\s+CONCURRENTLY'
    r')\b',
    re.IGNORECASE | re.DOTALL
)

# Matches statements that must be executed against the super/admin database
_SUPERDB_COMMAND_PATTERN = re.compile(
    r'^\s*(?:CREATE|DROP)\s+DATABASE\b',
    re.IGNORECASE | re.DOTALL
)

def _is_select(sql: str) -> bool:
    return sql.strip().upper().startswith("SELECT")

def _clean_sql(sql: str) -> str:
    """Strips block and single line comments to isolate the executable commands."""
    # Strip block comments /* ... */
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    # Strip single line comments -- ...
    sql = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    return sql.strip()

def requires_autocommit(sql: str) -> bool:
    """Return True if the statement cannot run inside a transaction block."""
    return bool(_NON_TRANSACTIONAL_PATTERN.search(_clean_sql(sql)))

def requires_superdb(sql: str) -> bool:
    """Return True if the statement must run against the super/admin database."""
    return bool(_SUPERDB_COMMAND_PATTERN.search(_clean_sql(sql)))

# Matches CREATE TABLE statements, ignoring single quotes in table names
_CREATE_TABLE_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_\"`\[\]\.]+)",
    re.IGNORECASE
)

# Matches ALTER TABLE statements, ignoring single quotes in table names
_ALTER_TABLE_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?([a-zA-Z0-9_\"`\[\]\.]+)",
    re.IGNORECASE
)

def extract_table_name(sql: str) -> Optional[str]:
    """
    Extracts the clean table name from CREATE TABLE and ALTER TABLE DDL statements.
    Correctly handles IF NOT EXISTS, IF EXISTS, ONLY, schema qualification, and quote stripping (excluding single quotes).
    """
    cleaned = _clean_sql(sql)
    
    # 1. Match CREATE TABLE
    m = _CREATE_TABLE_RE.match(cleaned)
    if m:
        name = m.group(1)
        if "." in name:
            name = name.split(".")[-1]
        return name.strip('"`[]')
        
    # 2. Match ALTER TABLE
    m = _ALTER_TABLE_RE.match(cleaned)
    if m:
        name = m.group(1)
        if "." in name:
            name = name.split(".")[-1]
        return name.strip('"`[]')
        
    return None

def execute_sql(sql: str, dbname: str) -> dict:
    """
    Phase 5 Executor: Safely executes validated SQL against PostgreSQL.
    
    This function assumes the SQL has ALREADY passed the Phase 4 validator.
    It is generic and intent-agnostic:
      - Automatically detects CREATE DATABASE to enable autocommit.
      - Automatically detects SELECT to fetch rows without committing.
      - Automatically commits all other DML/DDL modifications.
      - Automatically rolls back on failure to prevent aborted transaction states.
    
    Returns a dict matching the ExecutionResult Pydantic schema.
    """
    conn = None
    cursor = None
    
    cleaned_sql = _clean_sql(sql)
    is_select = _is_select(cleaned_sql)
    is_non_transactional = requires_autocommit(cleaned_sql)
    is_superdb = requires_superdb(cleaned_sql)
    
    # Determine operation name
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

    # Default result payload
    result = {
        "success": False,
        "operation": operation,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "message": None,
        "error": None
    }

    print("\n====================================")
    print("PHASE 5 EXECUTION")
    print("=================")
    print(f"Selected Database:\n{dbname}\n")
    print(f"Generated SQL:\n{sql}\n")
    print("Running Executor...")

    try:
        # 1. Open connection (override with super/admin DB if needed)
        if is_superdb:
            import os
            dbname = os.getenv("DB_SUPERDB", "postgres")
        conn = get_connection(dbname)
        print(f"Connection Opened to: {dbname}")

        # 2. Handle autocommit requirement for non-transactional commands
        if is_non_transactional:
            conn.autocommit = True
            print("Autocommit Enabled")

        cursor = conn.cursor()

        # 3. Execute the SQL
        print("Executing SQL...")
        cursor.execute(sql)

        # 4. Handle results based on operation type
        if is_select:
            # Fetch all rows for SELECT queries
            rows = cursor.fetchall()
            # Extract column names from cursor description
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            
            result["success"] = True
            result["columns"] = columns
            result["rows"] = [list(row) for row in rows]
            result["row_count"] = len(rows)
            result["message"] = "Query executed successfully."
            
            print(f"Rows Returned:\n{result['row_count']}")
            print(f"Columns:\n{', '.join(columns)}")
            print("Execution Success")
            
        else:
            # For non-SELECT operations, get rowcount if applicable
            row_count = cursor.rowcount
            if row_count >= 0:
                result["row_count"] = row_count
            
            # Commit the transaction (if not in autocommit mode)
            if not is_non_transactional:
                conn.commit()
                print("Transaction Committed")
                
            result["success"] = True
            result["message"] = f"Operation '{operation}' executed successfully."
            print("Execution Success")

    except psycopg2.Error as e:
        # Handle execution failure gracefully
        error_msg = str(e).strip()
        result["success"] = False
        result["error"] = error_msg
        
        print(f"\nExecution Error:\n{error_msg}\n")
        
        if conn and not is_non_transactional:
            print("Rolling Back Transaction...")
            try:
                conn.rollback()
                print("Rollback Complete")
            except Exception as rb_err:
                print(f"Rollback Failed: {str(rb_err)}")
                
    except Exception as e:
        # Catch-all for non-Psycopg2 errors
        error_msg = str(e)
        result["success"] = False
        result["error"] = f"Internal server error: {error_msg}"
        print(f"\nInternal Error:\n{error_msg}\n")
        
    finally:
        # Ensure resources are always closed
        if cursor:
            cursor.close()
        if conn:
            conn.close()
            
        print("====================================\n")

    return result
