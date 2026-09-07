import json
import os
import time
import psycopg2
from threading import Lock, RLock, BoundedSemaphore
from sqlalchemy.orm import Session
from services.connection_service import ConnectionService
from auth.hashing import decrypt_data
from utils.config_loader import get_superdb_name, get_db_connect_timeout_seconds, get_db_statement_timeout_ms

# In-memory connection pool: { user_id: {"connection": conn, "last_active": timestamp} }
_active_connections = {}
_connection_lock = Lock()

# Per-user execution locks (RLock for thread reentrancy per user session)
_user_execution_locks: dict[int, RLock] = {}
_locks_guard = Lock()

IDLE_TIMEOUT_SECONDS = 1800 # 30 minutes

# Bounded ceiling on how many real PostgreSQL connections this one process
# will ever hold open at once, across ALL users combined — the previous
# design cached one connection per active user with no upper bound at all,
# so PostgreSQL's own max_connections could be exhausted purely by having
# enough distinct users active simultaneously (different users' separate
# credentials/databases don't change the fact that a single Postgres
# *server* has one finite connection ceiling). A slot is acquired before a
# NEW physical connection is opened and released whenever one is closed
# (idle cleanup, explicit close, or a stale/dropped connection being
# discarded before its replacement is created) — reusing an
# already-cached connection needs no slot, since it doesn't open a new one.
# NOTE (multi-worker deployments): this cap is per-process. Running N
# uvicorn/gunicorn workers means a true ceiling of roughly
# N * DB_POOL_MAX_CONNECTIONS real connections, since each worker holds
# its own independent semaphore — there is no cross-process coordination
# here. Size DB_POOL_MAX_CONNECTIONS with that multiplication in mind.
_MAX_TOTAL_CONNECTIONS = int(os.getenv("DB_POOL_MAX_CONNECTIONS", "50"))
_CONNECTION_ACQUIRE_TIMEOUT_SECONDS = int(os.getenv("DB_POOL_ACQUIRE_TIMEOUT_SECONDS", "10"))
_connection_slots = BoundedSemaphore(_MAX_TOTAL_CONNECTIONS)

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
    def get_connection(cls, user_id: int, db: Session, target_database: str = None, is_autocommit: bool = False, read_only: bool = False) -> psycopg2.extensions.connection:
        """
        Get or create a connection for the given user.
        If target_database is specified and different from the cached connection,
        it will close the old one and create a new one.
        If is_autocommit is True, sets autocommit mode BEFORE executing any queries.

        Phase 9.6: The requested database is enforced against the user's
        per-user database ACL (allowed_databases). The maintenance/superdb
        database (DB_SUPERDB) is always reachable so CREATE/DROP DATABASE and
        other superuser operations keep working.

        read_only: when True, the connection's session is put into
        PostgreSQL's own read-only transaction mode (psycopg2's
        set_session(readonly=True) — equivalent to
        SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY) before it's
        handed back. This is a database-enforced backstop, independent of
        anything the application's own SQL-safety checks decided: even if
        a write statement somehow reaches this connection while read_only
        is requested, PostgreSQL itself rejects it — it does not depend on
        the app's classification being correct. Callers needing to write
        (INSERT/UPDATE/DELETE/DDL) must explicitly pass read_only=False
        (the default), same as they do today.
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
                        _connection_slots.release()
                    else:
                        # Reset transaction block if open and set autocommit state before health check
                        if conn.status != psycopg2.extensions.TRANSACTION_STATUS_IDLE or conn.autocommit != is_autocommit:
                            try:
                                if not conn.autocommit:
                                    conn.rollback()
                            except Exception:
                                pass
                            conn.autocommit = is_autocommit

                        # This same cached connection is reused across both
                        # read and write calls for a user, interleaved, so
                        # the read-only flag has to be re-asserted (or
                        # lifted) on every checkout rather than set once.
                        # set_session() requires an idle connection, which
                        # the block above already guarantees.
                        if conn_info.get("readonly") != read_only:
                            conn.set_session(readonly=read_only)
                            conn_info["readonly"] = read_only

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
                    _connection_slots.release()

            # Create a new connection — bounded: wait up to the acquisition
            # timeout for a free slot rather than opening an unlimited
            # number of real PostgreSQL connections. On timeout, fail
            # gracefully with a clear error instead of either hanging or
            # silently exceeding the cap.
            if not _connection_slots.acquire(timeout=_CONNECTION_ACQUIRE_TIMEOUT_SECONDS):
                raise ValueError(
                    f"Too many active database connections right now "
                    f"(limit: {_MAX_TOTAL_CONNECTIONS}). Please try again shortly."
                )

            # From here on, a slot has been acquired — any failure before
            # the connection is safely cached must release it back, or a
            # single failed connection attempt would permanently shrink
            # the pool's effective capacity.
            try:
                password = decrypt_data(conn_model.encrypted_password) if conn_model.encrypted_password else ""
                if not password and conn_model.remember_password:
                    raise ValueError("Password is required but missing.")

                # connect_timeout bounds how long establishing the
                # connection itself may take (network-level).
                # statement_timeout is passed via `options` so PostgreSQL
                # enforces it as a session GUC on every statement executed
                # on this connection from here on — the correct
                # server-side mechanism, not a Python-side timer that
                # can't actually stop a query already running on the
                # server. Set once at connect time; persists for the
                # connection's whole cached lifetime (unlike read_only
                # above, this doesn't vary per checkout, so no need to
                # reassert it on reuse).
                conn = psycopg2.connect(
                    host=conn_model.host,
                    port=conn_model.port,
                    user=conn_model.username,
                    password=password,
                    dbname=db_to_use,
                    connect_timeout=get_db_connect_timeout_seconds(),
                    options=f"-c statement_timeout={get_db_statement_timeout_ms()}",
                )

                if is_autocommit:
                    conn.autocommit = True

                conn.set_session(readonly=read_only)
            except Exception:
                _connection_slots.release()
                raise

            _active_connections[user_id] = {
                "connection": conn,
                "last_active": time.time(),
                "readonly": read_only,
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
                _connection_slots.release()
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
                _connection_slots.release()

        if stale_users:
            with _locks_guard:
                for user_id in stale_users:
                    _user_execution_locks.pop(user_id, None)
