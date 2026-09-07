"""
Phase 9 — Multi-User Concurrency & Isolation Verification Suite
================================================================
Proves that N distinct users can use the application concurrently
without their state, database access, sessions, caches, results or
authentication being mixed between users.

Two layers are exercised:

  * MOCKED LAYER (always runs)  — real FastAPI app + real JWT auth +
    real session/ownership boundary + real metadata/session/analytics/
    visualization/connection stores, with only the PostgreSQL driver and
    schema-fetcher layer replaced by per-user fakes. The ACL enforcement
    in ConnectionManager.get_connection runs for real.

  * REAL LAYER (skipped if PostgreSQL is unreachable) — full integration:
    real users in the SQLite app DB, real JWT tokens, real per-user
    PostgreSQL databases/tables and connection rows, real ACL checks and
    real SQL execution through the universal semantic gateway.

Run with:
    python  tests/test_multi_user_concurrency.py
    pytest  tests/test_multi_user_concurrency.py -s
"""

import asyncio
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import httpx
import psycopg2

from main import app
from utils.config_loader import init_env
from utils.logging_config import (
    user_id_var, session_id_var, database_name_var, selected_table_var,
)
from auth.jwt import create_access_token
from auth.hashing import encrypt_data
from db.app_database import SessionLocal, engine
from models.domain import User, PostgresConnection, Role
from repositories.user_repository import UserRepository
from repositories.connection_repository import ConnectionRepository
from repositories.chat_session_repository import ChatSessionRepository

from state.metadata_store import _user_stores, get_metadata, sync_database_context
from state.session_store import store as session_store
from analytics.engine import _SOURCE_CACHE, _RESULT_CACHE, AnalyticsEngine
from visualization.engine import _VIZ_CACHE, _store_viz_cache, _make_cache_key
from connections import connection_manager as cm_mod

N_USERS = 5

RESULTS = []  # (section, check_no, name, passed, detail)


def check(section, no, name, passed, detail=""):
    passed = bool(passed)
    RESULTS.append((section, no, name, passed, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {no}. {name}"
          + (f"  -- {detail}" if detail else ""))
    return passed


# ─────────────────────────────────────────────────────────────────────
# ContextVar helpers
# ─────────────────────────────────────────────────────────────────────
def _enter(vars_vals):
    toks = []
    for var, val in vars_vals.items():
        toks.append((var, var.set(val)))
    return toks


def _exit(toks):
    for var, tok in reversed(toks):
        var.reset(tok)


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────
USERS = []  # [{id, email, token, session_id, default_db, allowed, real_db, real_secret}]
REAL_DBS = []


def _reset_in_memory_state():
    _user_stores.clear()
    session_store._sessions.clear()
    _SOURCE_CACHE.clear()
    _RESULT_CACHE.clear()
    _VIZ_CACHE.clear()
    cm_mod._active_connections.clear()


def _tune_sqlite():
    from sqlalchemy import event as _sa_event

    @_sa_event.listens_for(engine, "connect")
    def _busy_timeout(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA busy_timeout=10000")
        finally:
            cur.close()

    try:
        engine.pool._pool.maxsize = 200
    except Exception:
        pass
    engine.dispose()
    with engine.connect() as c:
        c.exec_driver_sql("PRAGMA journal_mode=WAL").close()


def _create_app_users():
    db = SessionLocal()
    repo = UserRepository(db)
    try:
        stamp = int(time.time() * 1000)
        for i in range(N_USERS):
            email = f"conc_user_{stamp}_{i}@test.local"
            u = repo.create_user(User(
                email=email, password_hash="x", role=Role.USER,
                is_active=True, is_verified=True,
            ))
            USERS.append({
                "id": u.id,
                "email": email,
                "token": create_access_token({"sub": str(u.id)}),
            })
    finally:
        db.close()


def _create_chat_sessions():
    db = SessionLocal()
    repo = ChatSessionRepository(db)
    try:
        stamp = int(time.time() * 1000)
        for u in USERS:
            u["session_id"] = f"sess_conc_{u['id']}_{stamp}"
            repo.create(u["session_id"], u["id"])
    finally:
        db.close()


def _delete_test_users():
    db = SessionLocal()
    repo = UserRepository(db)
    cr = ConnectionRepository(db)
    chr_ = ChatSessionRepository(db)
    try:
        for u in USERS:
            cr.delete_connection(u["id"])
            chr_.deactivate_all_for_user(u["id"])
            user = repo.get_user_by_id(u["id"])
            if user:
                db.delete(user)
        db.commit()
    except Exception as e:
        print(f"[cleanup] user cleanup error: {e}")
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Mock layer infrastructure
# ─────────────────────────────────────────────────────────────────────
MOCK = {}


def _init_mock_registry():
    for u in USERS:
        uid = u["id"]
        default_db = f"db_u{uid}"
        extra = f"db_u{uid}_extra"
        tables = {f"tbl_{uid}": [f"col_{uid}_a", f"col_{uid}_b"]}
        u["default_db"] = default_db
        u["allowed"] = [default_db, extra]
        u["real_db"] = None
        MOCK[uid] = {
            "default_db": default_db,
            "allowed": [default_db, extra],
            "databases": [default_db, extra],
            "schema": tables,
            "tables": list(tables.keys()),
            "table_schemas": {t: {c: "text" for c in cols} for t, cols in tables.items()},
        }
    for u in USERS:
        MOCK[u["id"]]["conn"] = _FakeConnModel(u["id"])


class _FakeCursor:
    def __init__(self):
        self.description = None
        self._rows = []

    def execute(self, sql, params=None):
        self._rows = [(1,)]

    def fetchall(self):
        return self._rows

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeInfo:
    def __init__(self, dbname):
        self.dbname = dbname


class _FakePgConn:
    def __init__(self, dbname):
        self.info = _FakeInfo(dbname)
        self.status = psycopg2.extensions.TRANSACTION_STATUS_IDLE
        self.autocommit = False
        self.readonly = False

    def cursor(self):
        return _FakeCursor()

    def rollback(self):
        pass

    def close(self):
        pass

    def set_session(self, readonly=None, **kwargs):
        # Mirrors real psycopg2.connection.set_session's readonly kwarg —
        # ConnectionManager.get_connection() (Issue 1's database-level
        # read-only backstop) calls this on every checkout.
        if readonly is not None:
            self.readonly = readonly


class _FakeConnModel:
    def __init__(self, uid):
        f = MOCK[uid]
        self.default_database = f["default_db"]
        self.allowed_databases = json.dumps(f["allowed"])
        self.host = "localhost"
        self.port = 5432
        self.username = "fakeuser"
        self.encrypted_password = encrypt_data("fakepass")
        self.remember_password = True


class _FakeConnectionService:
    def get_user_connection(self, user_id):
        return MOCK.get(user_id, {}).get("conn")


class _Patches:
    """Patch the DB driver + schema-fetcher layer; everything else stays real."""

    def __enter__(self):
        import db.schema_fetcher as sf
        import db.table_manager as tm
        import routers.schema as rsch

        self._saved = []

        def save(obj, attr, val):
            self._saved.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, val)

        def mock_fetch_all_databases():
            uid = user_id_var.get()
            f = MOCK.get(uid) or {}
            return list(f.get("databases", []))

        def mock_fetch_schema(dbname):
            uid = user_id_var.get()
            f = MOCK.get(uid) or {}
            return dict(f.get("schema", {}))

        def mock_fetch_table_schemas(dbname):
            uid = user_id_var.get()
            f = MOCK.get(uid) or {}
            return dict(f.get("table_schemas", {}))

        def mock_fetch_foreign_keys(dbname):
            return {}

        def mock_fetch_tables(dbname):
            uid = user_id_var.get()
            f = MOCK.get(uid) or {}
            return list(f.get("tables", []))

        for target in (sf, rsch):
            save(target, "fetch_all_databases", mock_fetch_all_databases)
            save(target, "fetch_schema", mock_fetch_schema)
            save(target, "fetch_table_schemas", mock_fetch_table_schemas)
        save(sf, "fetch_foreign_keys", mock_fetch_foreign_keys)
        save(tm, "fetch_tables", mock_fetch_tables)
        save(rsch, "fetch_tables", mock_fetch_tables)

        def fake_connect(*a, **k):
            return _FakePgConn(k.get("dbname", ""))

        save(cm_mod.psycopg2, "connect", fake_connect)
        save(cm_mod, "ConnectionService", lambda db: _FakeConnectionService())
        return self

    def __exit__(self, *exc):
        for obj, attr, val in reversed(self._saved):
            setattr(obj, attr, val)


# ─────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────
async def _post(client, token, path, payload=None):
    return await client.post(path, headers={"Authorization": f"Bearer {token}"},
                             json=payload or {})


async def _get(client, token, path):
    return await client.get(path, headers={"Authorization": f"Bearer {token}"})


def _rows_text(body):
    """Flatten a chat response's execution rows into a searchable string."""
    exec_ = (body or {}).get("execution") or {}
    if not exec_.get("success"):
        return ""
    return json.dumps(exec_.get("rows", []))


# ─────────────────────────────────────────────────────────────────────
# MOCK LAYER CHECKS (13 verification points)
# ─────────────────────────────────────────────────────────────────────
async def m_session_ownership(client):
    """1. User A cannot access User B's chat session."""
    a, b = USERS[0], USERS[1]
    r = await _post(client, a["token"], "/api/chat",
                    {"message": "list databases", "session_id": b["session_id"]})
    return check("MOCK", 1, "A cannot access B's chat session",
                 r.status_code == 403, f"status={r.status_code} body={r.text[:120]}")


async def m_selected_database(client):
    """2. User A cannot use User B's selected database."""
    a, b = USERS[0], USERS[1]
    db_a, db_b = a["default_db"], b["default_db"]
    ra = await _post(client, a["token"], "/api/select-database", {"database": db_a})
    rb = await _post(client, b["token"], "/api/select-database", {"database": db_b})
    if ra.status_code != 200 or rb.status_code != 200:
        return check("MOCK", 2, "A cannot use B's selected database", False,
                     f"select statuses {ra.status_code},{rb.status_code}")
    sa = await _get(client, a["token"], "/api/schema")
    sb = await _get(client, b["token"], "/api/schema")
    sel_a = sa.json().get("selected") if sa.status_code == 200 else None
    sel_b = sb.json().get("selected") if sb.status_code == 200 else None
    ok = (sel_a == db_a and sel_b == db_b
          and (sel_a or "").lower() != (sel_b or "").lower())
    return check("MOCK", 2, "A cannot use B's selected database", ok,
                 f"A selected={sel_a!r} B selected={sel_b!r}")


def m_selected_table():
    """3. User A cannot access User B's selected table."""
    for u in USERS:
        session_store.update_session(u["id"], u["session_id"],
                                     selected_table=f"tbl_{u['id']}")
    ok = all(session_store.get_session(u["id"], u["session_id"])["selected_table"]
             == f"tbl_{u['id']}" for u in USERS)
    ok = ok and all(session_store.get_session(USERS[0]["id"], USERS[j]["session_id"])
                    .get("selected_table") is None for j in range(1, N_USERS))
    return check("MOCK", 3, "A cannot access B's selected table", ok)


def m_metadata_isolation():
    """4. User A cannot retrieve User B's metadata."""
    def work(u):
        with _exit_wrapped([(user_id_var, user_id_var.set(u["id"]))]):
            sync_database_context(u["default_db"])
        return True

    with ThreadPoolExecutor(max_workers=N_USERS) as ex:
        list(ex.map(work, USERS))

    ok = True
    for i in range(N_USERS):
        a = USERS[i]
        toks = _enter({user_id_var: a["id"]})
        try:
            meta = get_metadata()
        finally:
            _exit(toks)
        for j in range(N_USERS):
            if i != j:
                if meta.get("selected_db") == USERS[j]["default_db"]:
                    ok = False
    return check("MOCK", 4, "A cannot retrieve B's metadata", ok)


def _exit_wrapped(toks):
    """Context manager that exits stored (var, token) pairs."""
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        try:
            yield
        finally:
            _exit(toks)
    return _cm()


def m_analytics_cache_isolation():
    """5. User A cannot retrieve User B's cached analytics/result data."""
    class _Res:
        pass

    for u in USERS:
        res = _Res()
        res.execution = _Res()
        res.execution.row_count = u["id"] % 97
        AnalyticsEngine.cache_source(
            u["session_id"], res, [f"tbl_{u['id']}"],
            u["default_db"], f"fp_{u['id']}", "MOCK", user_id=u["id"])

    ok = True
    for i in range(N_USERS):
        a = USERS[i]
        own = AnalyticsEngine.get_source_cache(a["session_id"], user_id=a["id"])
        if own is None:
            ok = False
        for j in range(N_USERS):
            if i != j:
                b = USERS[j]
                if AnalyticsEngine.get_source_cache(b["session_id"], user_id=a["id"]) is not None:
                    ok = False
                toks = _enter({user_id_var: a["id"]})
                try:
                    if AnalyticsEngine.get_source_cache(b["session_id"]) is not None:
                        ok = False
                finally:
                    _exit(toks)
    return check("MOCK", 5, "A cannot retrieve B's analytics cache", ok)


def m_viz_isolation():
    """6. User A cannot retrieve User B's visualization data."""
    class _Viz:
        def __init__(self, uid):
            self.chart_id = f"viz_{uid}"
            self.session_id = None

    for u in USERS:
        key = _make_cache_key(f"fp_{u['id']}", "BAR", "x", "y", ("x", "y"), ())
        _store_viz_cache(u["session_id"], key, _Viz(u["id"]))

    distinct = len({u["session_id"] for u in USERS}) == N_USERS
    no_cross = all(v["session_id"] not in _VIZ_CACHE for u in USERS
                   for v in USERS if v["id"] != u["id"]
                   for v_key in _VIZ_CACHE.get(u["session_id"], {}).values()
                   if getattr(v_key, "chart_id", "") == f"viz_{v['id']}")
    # Simpler, exact check: A's session bucket only contains A's charts.
    for u in USERS:
        for key, result in _VIZ_CACHE.get(u["session_id"], {}).items():
            if getattr(result, "chart_id", "") != f"viz_{u['id']}":
                no_cross = False
    return check("MOCK", 6, "A cannot retrieve B's visualization data",
                 distinct and no_cross)


def m_acl_enforcement():
    """7. User A cannot use User B's PostgreSQL permission (ACL)."""
    from connections.connection_manager import ConnectionManager
    a, b = USERS[0], USERS[1]
    db = SessionLocal()
    try:
        def blocked(uid, target):
            try:
                ConnectionManager.get_connection(uid, db, target_database=target,
                                                 is_autocommit=False)
                return False
            except ValueError as e:
                return "not allowed" in str(e)

        ok_a_blocked_b = blocked(a["id"], b["default_db"])
        ok_b_blocked_a = blocked(b["id"], a["default_db"])
        own_ok = ConnectionManager.get_connection(
            a["id"], db, target_database=a["default_db"], is_autocommit=False) is not None
        ok = ok_a_blocked_b and ok_b_blocked_a and own_ok
        return check("MOCK", 7, "A cannot use B's PostgreSQL permission", ok,
                     f"A→B blocked={ok_a_blocked_b} B→A blocked={ok_b_blocked_a} own_ok={own_ok}")
    finally:
        db.close()


async def m_mirror(client):
    """8. User B cannot access User A's resources."""
    a, b = USERS[0], USERS[1]
    r = await _post(client, b["token"], "/api/chat",
                    {"message": "list databases", "session_id": a["session_id"]})
    rb = await _get(client, b["token"], "/api/databases")
    dbs_b = rb.json().get("databases", []) if rb.status_code == 200 else []
    mirror_session = r.status_code == 403
    mirror_db = a["default_db"] not in dbs_b
    return check("MOCK", 8, "B cannot access A's resources",
                 mirror_session and mirror_db,
                 f"session={r.status_code} B sees A's db={not mirror_db}")


def m_session_no_overwrite():
    """9. Concurrent requests do not overwrite each other's session state."""
    def work(u):
        for _ in range(6):
            session_store.update_session(u["id"], u["session_id"],
                                         selected_database=u["default_db"],
                                         add_operation="SELECT")
            session_store.update_session(u["id"], u["session_id"],
                                         last_query=f"q-{u['id']}")

    with ThreadPoolExecutor(max_workers=N_USERS) as ex:
        futs = [ex.submit(work, u) for u in USERS]
        for f in futs:
            f.result()

    ok = all(session_store.get_session(u["id"], u["session_id"])["selected_database"]
             == u["default_db"] for u in USERS)
    ok = ok and all(all(session_store.get_session(u["id"], u["session_id"])["selected_database"]
                        != USERS[j]["default_db"] for j in range(N_USERS) if j != USERS.index(u))
                    for u in USERS)
    return check("MOCK", 9, "Concurrent requests don't overwrite session state", ok)


def m_connection_pool():
    """10. Concurrent requests do not corrupt connection-pool state."""
    from connections.connection_manager import ConnectionManager
    from connections.connection_manager import _active_connections

    def work(u):
        db = SessionLocal()
        try:
            conn = ConnectionManager.get_connection(u["id"], db, target_database=None,
                                                    is_autocommit=False)
            return (u["id"], conn.info.dbname)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=N_USERS) as ex:
        out = list(ex.map(work, USERS))

    per_user = {uid: dbname for uid, dbname in out}
    ok = all(per_user[u["id"]] == u["default_db"] for u in USERS)
    ok = ok and set(_active_connections.keys()) == {u["id"] for u in USERS}
    return check("MOCK", 10, "Connection-pool state not corrupted", ok,
                 f"pool users={sorted(_active_connections.keys())}")


async def m_no_cross_auth(client):
    """11. Concurrent requests do not cause cross-user authentication."""
    me = await asyncio.gather(*[_get(client, u["token"], "/api/auth/me") for u in USERS])
    ok = all(r.status_code == 200 and r.json().get("email") == USERS[i]["email"]
             for i, r in enumerate(me))
    return check("MOCK", 11, "No cross-user authentication", ok)


async def m_no_race_exceptions(client):
    """12. No race-condition-related exceptions occur."""
    tasks = []
    for _round in range(3):
        for u in USERS:
            tasks.append(_post(client, u["token"], "/api/chat",
                               {"message": "list databases", "session_id": u["session_id"]}))
            tasks.append(_get(client, u["token"], "/api/databases"))
    results = await asyncio.gather(*tasks)
    ok = all(r.status_code == 200 for r in results)
    failed = [r.status_code for r in results if r.status_code != 200]
    return check("MOCK", 12, "No race-condition-related exceptions", ok,
                 f"{len(results)} concurrent requests, non-200={failed[:5]}")


async def m_no_state_fallback(client):
    """13. No request accidentally falls back to another user's state."""
    results = await asyncio.gather(*[
        _get(client, u["token"], "/api/databases") for u in USERS
    ])
    ok = True
    for i, r in enumerate(results):
        if r.status_code != 200:
            ok = False
            continue
        dbs = r.json().get("databases", [])
        if USERS[i]["default_db"] not in dbs:
            ok = False
        for j in range(N_USERS):
            if i != j and USERS[j]["default_db"] in dbs:
                ok = False
    return check("MOCK", 13, "No fallback to another user's state", ok)


# ─────────────────────────────────────────────────────────────────────
# REAL LAYER
# ─────────────────────────────────────────────────────────────────────
def _pg_available():
    init_env()
    try:
        c = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            user=os.getenv("DB_USER", ""),
            password=os.getenv("DB_PASSWORD", ""),
            dbname=os.getenv("DB_SUPERDB", "postgres"),
            connect_timeout=4,
        )
        c.close()
        return True
    except Exception:
        return False


def _create_real_dbs():
    host = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "5432"))
    user = os.getenv("DB_USER", "")
    pw = os.getenv("DB_PASSWORD", "")
    superdb = os.getenv("DB_SUPERDB", "postgres")
    stamp = int(time.time())
    c = psycopg2.connect(host=host, port=port, user=user, password=pw,
                         dbname=superdb, connect_timeout=5)
    c.autocommit = True
    cur = c.cursor()
    try:
        for u in USERS:
            uid = u["id"]
            dbname = f"conc_u{uid}_{stamp}"
            cur.execute(f'CREATE DATABASE "{dbname}"')
            REAL_DBS.append(dbname)
            u["real_db"] = dbname
            u["real_secret"] = f"SECRET_U{uid}"
            c2 = psycopg2.connect(host=host, port=port, user=user, password=pw,
                                  dbname=dbname, connect_timeout=5)
            c2.autocommit = True
            cur2 = c2.cursor()
            cur2.execute(f'CREATE TABLE t_u{uid} (id serial primary key, owner text, value text)')
            cur2.execute(f"INSERT INTO t_u{uid} (owner, value) VALUES ('u{uid}', '{u['real_secret']}')")
            c2.close()
    finally:
        cur.close()
        c.close()


def _create_real_connections():
    db = SessionLocal()
    repo = ConnectionRepository(db)
    try:
        for u in USERS:
            repo.create_connection(PostgresConnection(
                user_id=u["id"],
                host=os.getenv("DB_HOST", "localhost"),
                port=int(os.getenv("DB_PORT", "5432")),
                username=os.getenv("DB_USER", ""),
                encrypted_password=encrypt_data(os.getenv("DB_PASSWORD", "")),
                default_database=u["real_db"],
                remember_password=True,
                allowed_databases=json.dumps([u["real_db"]]),
            ))
    finally:
        db.close()


def _drop_real_dbs():
    host = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "5432"))
    user = os.getenv("DB_USER", "")
    pw = os.getenv("DB_PASSWORD", "")
    superdb = os.getenv("DB_SUPERDB", "postgres")
    try:
        c = psycopg2.connect(host=host, port=port, user=user, password=pw,
                             dbname=superdb, connect_timeout=5)
        c.autocommit = True
        cur = c.cursor()
        for dbname in REAL_DBS:
            try:
                cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
            except Exception:
                pass
        cur.close()
        c.close()
    except Exception as e:
        print(f"[cleanup] drop db error: {e}")


async def r_real_sql_isolation(client):
    """R1. A uses own DB + SELECTs own data; B's data never appears."""
    a, b = USERS[0], USERS[1]
    ra = await _post(client, a["token"], "/api/chat",
                     {"message": f"use database {a['real_db']}", "session_id": a["session_id"]})
    if ra.status_code != 200:
        return check("REAL", 1, "A SELECT returns only A's data (use-db)", False,
                     f"use failed {ra.status_code}: {ra.text[:200]}")
    rq = await _post(client, a["token"], "/api/chat",
                     {"message": f"SELECT * FROM t_u{a['id']} LIMIT 1",
                      "session_id": a["session_id"]})
    if rq.status_code != 200:
        return check("REAL", 1, "A SELECT returns only A's data", False,
                     f"select failed {rq.status_code}: {rq.text[:200]}")
    rows = _rows_text(rq.json())
    has_own = a["real_secret"] in rows
    has_other = b["real_secret"] in rows
    ok = has_own and not has_other
    return check("REAL", 1, "A SELECT returns only A's data", ok,
                 f"own_secret={has_own} B_secret_present={has_other} | rows={rows[:160]!r}")


async def r_real_acl(client):
    """R2. A is blocked from B's database (connection-layer ACL + real HTTP path)."""
    from connections.connection_manager import ConnectionManager
    a, b = USERS[0], USERS[1]
    db = SessionLocal()
    try:
        try:
            ConnectionManager.get_connection(a["id"], db, target_database=b["real_db"],
                                             is_autocommit=False)
            direct_blocked = False
        except ValueError as e:
            direct_blocked = "not allowed" in str(e)
    finally:
        db.close()
    r = await _post(client, a["token"], "/api/select-database", {"database": b["real_db"]})
    http_blocked = r.status_code == 500 and "not allowed" in r.text.lower()
    ok = direct_blocked and http_blocked
    return check("REAL", 2, "A blocked from B's database by ACL", ok,
                 f"direct_blocked={direct_blocked} http_blocked={http_blocked} "
                 f"status={r.status_code} body={r.text[:160]!r}")


async def r_real_session_ownership(client):
    """R3. Real session ownership: A cannot use B's session."""
    a, b = USERS[0], USERS[1]
    r = await _post(client, a["token"], "/api/chat",
                    {"message": "list databases", "session_id": b["session_id"]})
    return check("REAL", 3, "Session ownership enforced (A→B session 403)", r.status_code == 403,
                 f"status={r.status_code} body={r.text[:120]}")


async def r_real_mirror(client):
    """R4. B mirror: B SELECTs only B's data and is blocked from A's db."""
    b, a = USERS[1], USERS[0]
    ra = await _post(client, b["token"], "/api/chat",
                     {"message": f"use database {b['real_db']}", "session_id": b["session_id"]})
    rq = await _post(client, b["token"], "/api/chat",
                     {"message": f"SELECT * FROM t_u{b['id']} LIMIT 1",
                      "session_id": b["session_id"]})
    rows = _rows_text(rq.json()) if rq.status_code == 200 else ""
    has_own = b["real_secret"] in rows
    has_other = a["real_secret"] in rows
    rb = await _post(client, b["token"], "/api/select-database", {"database": a["real_db"]})
    blocked = rb.status_code == 500 and "not allowed" in rb.text.lower()
    ok = has_own and not has_other and blocked
    return check("REAL", 4, "B mirror (own data + blocked from A)", ok,
                 f"own={has_own} A_secret={has_other} A_db_blocked={blocked}")


async def r_real_concurrent(client):
    """R5. 5 users concurrently SELECT their own table; no cross-talk/exceptions."""
    async def select(u):
        await _post(client, u["token"], "/api/chat",
                    {"message": f"use database {u['real_db']}",
                     "session_id": u["session_id"]})
        r = await _post(client, u["token"], "/api/chat",
                        {"message": f"SELECT * FROM t_u{u['id']} LIMIT 1",
                         "session_id": u["session_id"]})
        rows = _rows_text(r.json()) if r.status_code == 200 else ""
        own = u["real_secret"] in rows
        others = [v["real_secret"] in rows for v in USERS if v["id"] != u["id"]]
        return r.status_code == 200 and own and not any(others)

    results = await asyncio.gather(*[select(u) for u in USERS])
    ok = all(results)
    return check("REAL", 5, "5 users concurrent SELECT, no cross-talk", ok,
                 f"per-user ok={results}")


# ─────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────
async def _run_mock_layer():
    print("\n" + "=" * 70)
    print("MOCK LAYER — real app/auth/session/stores, fake DB driver")
    print("=" * 70)
    _reset_in_memory_state()
    with _Patches():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            await m_session_ownership(client)
            await m_selected_database(client)
            m_selected_table()
            m_metadata_isolation()
            m_analytics_cache_isolation()
            m_viz_isolation()
            m_acl_enforcement()
            await m_mirror(client)
            m_session_no_overwrite()
            m_connection_pool()
            await m_no_cross_auth(client)
            await m_no_race_exceptions(client)
            await m_no_state_fallback(client)


async def _run_real_layer():
    print("\n" + "=" * 70)
    print("REAL LAYER — real users, real Postgres DBs, real ACL/SQL")
    print("=" * 70)
    if not _pg_available():
        print("  SKIPPED — PostgreSQL is unreachable. Real integration NOT proven.")
        return
    _create_real_dbs()
    _create_real_connections()
    try:
        _reset_in_memory_state()
        # Seed each user's routing summaries to avoid the full-catalogue lazy sweep.
        from state.metadata_store import set_databases, refresh_routing_summaries
        for u in USERS:
            toks = _enter({user_id_var: u["id"]})
            try:
                set_databases([u["real_db"]])
                refresh_routing_summaries()
            finally:
                _exit(toks)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            await r_real_sql_isolation(client)
            await r_real_acl(client)
            await r_real_session_ownership(client)
            await r_real_mirror(client)
            await r_real_concurrent(client)
    finally:
        _drop_real_dbs()


def run_all(verbose=True):
    RESULTS.clear()
    _reset_in_memory_state()
    _tune_sqlite()
    _create_app_users()
    _init_mock_registry()
    _create_chat_sessions()
    try:
        asyncio.run(_run_mock_layer())
        asyncio.run(_run_real_layer())
    finally:
        _delete_test_users()

    print("\n" + "=" * 70)
    print("RESULT SUMMARY")
    print("=" * 70)
    for section in ("MOCK", "REAL"):
        rows = [r for r in RESULTS if r[0] == section]
        if not rows:
            continue
        passed = sum(1 for r in rows if r[3])
        print(f"  {section}: {passed}/{len(rows)} passed")
        for r in rows:
            if not r[3]:
                print(f"    FAILED: {r[1]}. {r[2]}  -- {r[4]}")
    total_ok = all(r[3] for r in RESULTS)
    print(f"\n  TOTAL: {sum(1 for r in RESULTS if r[3])}/{len(RESULTS)} passed")
    print("  CROSS-USER LEAKAGE FOUND: " + ("YES" if not total_ok else "NO"))
    return 0 if total_ok else 1


def test_multi_user_concurrency_suite():
    assert run_all(verbose=False) == 0


if __name__ == "__main__":
    sys.exit(run_all(verbose=True))
