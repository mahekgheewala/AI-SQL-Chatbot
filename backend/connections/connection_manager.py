import json
import time
import psycopg2
from threading import Lock, RLock
from sqlalchemy.orm import Session
from services.connection_service import ConnectionService
from auth.hashing import decrypt_data
from utils.config_loader import get_superdb_name

# In-memory connection pool: { user_id: {"connection": conn, "last_active": timestamp} }
_active_connections = {}
_connection_lock = Lock()

# Per-user execution locks (RLock for thread reentrancy per user session)
_user_execution_locks: dict[int, RLock] = {}
_locks_guard = Lock()

IDLE_TIMEOUT_SECONDS = 1800 # 30 minutes

def _load_allowed_databases(conn_model) -> set:
    """
    Phase 9.6 — Per-user database ACL.

    Returns the set of databases the user may connect to:
      * default_database is always allowed.
      * allowed_databases (JSON list) is honored when present; an empty or
        malformed value restricts the user to default_database only.
    """
    allowed = set()
    raw = getattr(conn_model, "allowed_databases", None)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                allowed = {str(d).lower() for d in parsed}
        except (ValueError, TypeError):
            allowed = set()
    if getattr(conn_model, "default_database", None):
        allowed.add(str(conn_model.default_database).lower())
    return allowed

class ConnectionManager:
    @classmethod
    def get_allowed_databases(cls, user_id: int, db: Session) -> list[str]:
        """
        Phase 9.6 — Returns the properly-cased list of databases this user
        may access: their default_database plus any explicitly granted
        allowed_databases, deduplicated case-insensitively. Used to scope
        "list databases" / routing to what this user actually owns, instead
        of every database on the shared Postgres server.
        """
        conn_service = ConnectionService(db)
        conn_model = conn_service.get_user_connection(user_id)
        if not conn_model:
            raise ValueError("No database connection configured for this user.")

        seen: set = set()
        result: list[str] = []
        if conn_model.default_database:
            result.append(conn_model.default_database)
            seen.add(conn_model.default_database.lower())

        raw = getattr(conn_model, "allowed_databases", None)
        if raw:
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                parsed = []
            if isinstance(parsed, list):
                for name in parsed:
                    name = str(name)
                    if name.lower() not in seen:
                        result.append(name)
                        seen.add(name.lower())

        return result

    @classmethod
    def get_user_execution_lock(cls, user_id: int) -> RLock:
        """
        Atomically retrieve or create a reentrant lock (RLock) for the given user_id.
        Guarantees thread-safe access to the single psycopg2 connection instance.
        """
        with _locks_guard:
            if user_id not in _user_execution_locks:
                _user_execution_locks[user_id] = RLock()
            return _user_execution_locks[user_id]

    @classmethod
    def get_connection(cls, user_id: int, db: Session, target_database: str = None, is_autocommit: bool = False) -> psycopg2.extensions.connection:
        """
        Get or create a connection for the given user.
        If target_database is specified and different from the cached connection,
        it will close the old one and create a new one.
        If is_autocommit is True, sets autocommit mode BEFORE executing any queries.

        Phase 9.6: The requested database is enforced against the user's
        per-user database ACL (allowed_databases). The maintenance/superdb
        database (DB_SUPERDB) is always reachable so CREATE/DROP DATABASE and
        other superuser operations keep working.
        """
        cls._cleanup_idle_connections()

        # Phase 9.6: Resolve the user's connection model and enforce the ACL
        # BEFORE any cached connection is reused, so ACL changes apply
        # immediately and no path bypasses the check.
        conn_service = ConnectionService(db)
        conn_model = conn_service.get_user_connection(user_id)

        if not conn_model:
            raise ValueError("No database connection configured for this user.")

        db_to_use = target_database if target_database else conn_model.default_database

        maintenance_db = (get_superdb_name() or "").lower()
        db_lower = (db_to_use or "").lower()
        if db_lower != maintenance_db and db_lower not in _load_allowed_databases(conn_model):
            raise ValueError(
                f"Access to database '{db_to_use}' is not allowed for this user."
            )

        with _connection_lock:
            # Check if existing connection is valid and for the same DB
            if user_id in _active_connections:
                conn_info = _active_connections[user_id]
                conn = conn_info["connection"]

                try:
                    # If target_database changed, close old connection
                    if target_database and conn.info.dbname != target_database:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        del _active_connections[user_id]
                    else:
                        # Reset transaction block if open and set autocommit state before health check
                        if conn.status != psycopg2.extensions.TRANSACTION_STATUS_IDLE or conn.autocommit != is_autocommit:
                            try:
                                if not conn.autocommit:
                                    conn.rollback()
                            except Exception:
                                pass
                            conn.autocommit = is_autocommit

                        # Test connection validity
                        with conn.cursor() as cur:
                            cur.execute("SELECT 1")

                        # Health check SELECT 1 opens an implicit transaction on psycopg2 when autocommit is False.
                        # Roll back immediately to keep connection idle for subsequent autocommit changes.
                        if not conn.autocommit and conn.status == psycopg2.extensions.TRANSACTION_STATUS_INTRANS:
                            try:
                                conn.rollback()
                            except Exception:
                                pass

                        conn_info["last_active"] = time.time()
                        return conn
                except psycopg2.Error:
                    # Connection dropped or invalid
                    try:
                        conn.close()
                    except:
                        pass

            # Create a new connection
            password = decrypt_data(conn_model.encrypted_password) if conn_model.encrypted_password else ""
            if not password and conn_model.remember_password:
                raise ValueError("Password is required but missing.")

            conn = psycopg2.connect(
                host=conn_model.host,
                port=conn_model.port,
                user=conn_model.username,
                password=password,
                dbname=db_to_use
            )

            if is_autocommit:
                conn.autocommit = True

            _active_connections[user_id] = {
                "connection": conn,
                "last_active": time.time()
            }

            return conn

    @classmethod
    def close_connection(cls, user_id: int):
        with _connection_lock:
            if user_id in _active_connections:
                conn = _active_connections[user_id]["connection"]
                try:
                    conn.close()
                except:
                    pass
                del _active_connections[user_id]
        with _locks_guard:
            _user_execution_locks.pop(user_id, None)

    @classmethod
    def _cleanup_idle_connections(cls):
        """Close connections that have been idle longer than IDLE_TIMEOUT_SECONDS."""
        current_time = time.time()
        stale_users = []
        with _connection_lock:
            for user_id, info in _active_connections.items():
                if current_time - info["last_active"] > IDLE_TIMEOUT_SECONDS:
                    stale_users.append(user_id)
            
            for user_id in stale_users:
                conn = _active_connections[user_id]["connection"]
                try:
                    conn.close()
                except:
                    pass
                del _active_connections[user_id]

        if stale_users:
            with _locks_guard:
                for user_id in stale_users:
                    _user_execution_locks.pop(user_id, None)
