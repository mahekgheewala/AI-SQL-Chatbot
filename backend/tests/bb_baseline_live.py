"""
Phase 10.6 Live Baseline Runner — 45-query conversational test
==============================================================
Runs queries through chat_endpoint in-process with full metadata simulation.
Captures: raw request, raw response, routing trace (all stdout).

    python tests/bb_baseline_live.py
"""
import sys, os, io, json, time
from unittest.mock import patch, MagicMock
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Bootstrap ───────────────────────────────────────────────────────────────
from db.app_database import Base, engine
import models.domain  # noqa: F401
Base.metadata.create_all(bind=engine)

from utils.logging_config import user_id_var
from state import metadata_store
from models.schemas import ChatRequest
from state.session_store import update_session, get_session

USER_ID = "bb_baseline_v2"
user_id_var.set(USER_ID)
SESSION_ID = "bbv_live_v2"

# ── Mock user + connection ──────────────────────────────────────────────────
mock_user = type("User", (), {"id": USER_ID})()
mock_conn = MagicMock()
mock_conn.port = 5432
mock_conn.host = "localhost"
mock_conn.username = "postgres"
mock_conn.encrypted_password = "password"
mock_conn.default_database = "hr_database"

# ── Seed metadata directly ──────────────────────────────────────────────────
HR_SCHEMA = {
    "employees": ["id", "name", "department", "salary", "hire_date"],
    "departments": ["id", "name"],
}
HR_TABLE_SCHEMAS = {
    "employees": {
        "id": "integer", "name": "text", "department": "text",
        "salary": "numeric", "hire_date": "date",
    },
    "departments": {"id": "integer", "name": "text"},
}
HR_TABLES = ["employees", "departments"]

store = metadata_store._user_stores[USER_ID]
store.update({
    "databases": ["hr_database", "another"],
    "selected_db": "hr_database",
    "schema": HR_SCHEMA,
    "tables": HR_TABLES,
    "table_schemas": HR_TABLE_SCHEMAS,
    "routing_summaries": {"hr_database": HR_TABLES, "another": ["students"]},
})

# Also set the session state
update_session(SESSION_ID, selected_database="hr_database")

# ── Mock execute_sql ────────────────────────────────────────────────────────
# Simulate real DB responses for known operations
MOCK_ROWS = {
    "SELECT": [
        (1, "alice", "engineering", 90000, "2022-01-15"),
        (2, "bob", "sales", 60000, "2022-06-01"),
        (3, "carol", "hr", 75000, "2021-03-10"),
        (4, "dave", "engineering", 85000, "2023-02-20"),
        (5, "eve", "sales", 62000, "2023-08-15"),
    ],
    "SHOW TABLES": [("employees",), ("departments",)],
    "LIST TABLES": [("employees",), ("departments",)],
}


def mock_execute_sql(sql, database=None, **kwargs):
    """Simulate SQL execution for known patterns."""
    sql_upper = (sql or "").upper().strip()

    if sql_upper.startswith("CREATE TABLE"):
        return {"success": True, "message": "Table created successfully.", "operation": "CREATE_TABLE"}
    if sql_upper.startswith("DROP TABLE"):
        return {"success": True, "message": "Table dropped successfully.", "operation": "DROP_TABLE"}
    if sql_upper.startswith("CREATE DATABASE"):
        return {"success": True, "message": "Database created.", "operation": "CREATE_DATABASE"}
    if sql_upper.startswith("DROP DATABASE"):
        return {"success": True, "message": "Database dropped.", "operation": "DROP_DATABASE"}
    if sql_upper.startswith("ALTER TABLE"):
        return {"success": True, "message": "Table altered.", "operation": "ALTER_TABLE"}

    # For SELECT, return mock data
    if sql_upper.startswith("SELECT"):
        # Parse table from SQL for more realistic mock
        return {
            "success": True, "operation": "SELECT",
            "columns": ["id", "name", "department", "salary", "hire_date"],
            "rows": MOCK_ROWS["SELECT"],
            "row_count": len(MOCK_ROWS["SELECT"]),
            "message": "Query executed successfully.",
        }

    # Fallback
    return {"success": True, "message": "OK", "operation": "UNKNOWN"}


# ── Test Queries ────────────────────────────────────────────────────────────
QUERIES = [
    # === CREATE TABLE (multi-turn) ===
    ("C01", "CreateTable",  "create table"),
    ("C02", "CreateTable",  "create table students"),
    ("C03", "CreateTable",  "id INTEGER, name TEXT"),
    # === ADD COLUMN / TABLE ===
    ("C04", "AddColumn",    "add table in it"),
    # === SAMPLE DATA ===
    ("C05", "SampleData",   "add sample data"),
    # === RETRIEVAL ===
    ("R01", "Retrieve",     "show employees"),
    ("R02", "Retrieve",     "show me employees in Engineering"),
    ("R03", "Retrieve",     "show me the top 5 of those"),
    ("R04", "Retrieve",     "filter them by age greater than 20"),
    # === AGGREGATION ===
    ("A01", "Aggregation",  "average salary"),
    # === VISUALIZATION ===
    ("V01", "Visualization", "create a pie chart"),
    # === GREETINGS ===
    ("G01", "Greeting",     "hello"),
    ("G02", "Greeting",     "thanks"),
    # === TYPOS ===
    ("T01", "Typo",         "shw all employes"),
    ("T02", "Typo",         "lis al tabels"),
    ("T03", "Typo",         "creat employe databse"),
    # === DB MANAGEMENT ===
    ("D01", "DB Mgmt",      "show all databases"),
    ("D02", "DB Mgmt",      "show all tables"),
    ("D03", "DB Mgmt",      "describe employees"),
    ("D04", "DB Mgmt",      "switch to hr_database"),
    # === EXPLANATIONS ===
    ("E01", "Explanation",  "explain INNER JOIN"),
    ("E02", "Explanation",  "what is GROUP BY?"),
    # === CONTEXT SWITCHES ===
    ("X01", "CtxSwitch",    "list all databases"),
    ("X02", "CtxSwitch",    "switch to another"),
    ("X03", "CtxSwitch",    "show students"),
    ("X04", "CtxSwitch",    "switch back to hr_database"),
    ("X05", "CtxSwitch",    "show employees"),
    # === CLARIFICATIONS ===
    ("K01", "Clarify",      "create table logs"),
    ("K02", "Clarify",      "delete records"),
    ("K03", "Clarify",      "add columns"),
    # === RAW SQL ===
    ("S01", "RawSQL",       "SELECT * FROM employees LIMIT 3"),
    # === EDGE CASES ===
    ("L01", "EdgeCase",     "show employees. Also explain JOIN."),
    ("L02", "EdgeCase",     "$$$$$$$$"),
    ("L03", "EdgeCase",     "asdfghjkl"),
    # === CONVERSATION ===
    ("V02", "Conversation", "what can you do?"),
    ("V03", "Conversation", "who are you?"),
    # === FINAL STATE ===
    ("F01", "Final",        "show all tables"),
]


async def main():
    from routers.chat import chat_endpoint

    results = []

    for idx, (qid, cat, msg) in enumerate(QUERIES):
        # Reset user_id_var (it gets overwritten by chat_endpoint)
        user_id_var.set(USER_ID)

        req = ChatRequest(session_id=SESSION_ID, message=msg, history=[])
        buf = io.StringIO()
        t0 = time.perf_counter()

        # Patch ConnectionManager + execute_sql for each call
        with patch("connections.connection_manager.ConnectionManager.get_connection", return_value=mock_conn), \
             patch("db.executor.execute_sql", side_effect=mock_execute_sql):
            try:
                with redirect_stdout(buf):
                    resp = await chat_endpoint(req, current_user=mock_user)
                ms = round((time.perf_counter() - t0) * 1000, 1)
                result = {
                    "id": qid, "category": cat, "message": msg,
                    "status": "OK", "ms": ms,
                    "intent": resp.intent,
                    "reply": (resp.reply or "")[:300],
                    "sql": resp.sql,
                    "database": resp.database,
                    "valid": resp.valid,
                    "risk_level": resp.risk_level,
                    "execution_op": resp.execution.get("operation") if resp.execution else None,
                    "execution_rows": resp.execution.get("row_count") if resp.execution else None,
                    "execution_success": resp.execution.get("success") if resp.execution else None,
                    "trace": buf.getvalue(),
                }
            except Exception as e:
                ms = round((time.perf_counter() - t0) * 1000, 1)
                result = {
                    "id": qid, "category": cat, "message": msg,
                    "status": "ERROR", "ms": ms,
                    "intent": "ERROR", "reply": str(e)[:200],
                    "sql": None, "database": None, "valid": False,
                    "risk_level": None, "execution_op": None,
                    "execution_rows": None, "execution_success": None,
                    "trace": buf.getvalue() + f"\nEXCEPTION: {e}",
                }

        results.append(result)

        # ── Per-query output ────────────────────────────────────────────
        print(f"\n{'='*80}")
        print(f"[{qid}] [{cat}] Turn {idx+1}: {repr(msg)}")
        print(f"{'='*80}")
        print(f"  INTENT   : {result['intent']}")
        reply_clean = (result['reply'] or '').replace('\n', ' ')[:150]
        print(f"  REPLY    : {reply_clean}")
        if result.get("sql"):
            print(f"  SQL      : {result['sql'][:150]}")
        if result.get("database"):
            print(f"  DATABASE : {result['database']}")
        if result.get("execution_op"):
            print(f"  EXEC     : {result['execution_op']} rows={result.get('execution_rows')} success={result.get('execution_success')}")
        print(f"  TIME     : {result['ms']}ms")
        print(f"  STATUS   : {result['status']}")

    # ═══════════════════════════════════════════════════════════════════════
    # SUMMARY TABLE
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*100}")
    print("BASELINE SUMMARY")
    print(f"{'='*100}")
    hdr = f"{'ID':<6} {'Cat':<14} {'Intent':<24} {'ExecOp':<12} {'SQL':<4} {'ms':<8} Reply (truncated)"
    print(hdr)
    print("-"*100)
    for r in results:
        has_sql = "Y" if r.get("sql") else "N"
        exec_op = r.get("execution_op") or "-"
        reply_short = (r.get("reply") or "")[:55].replace("\n", " ")
        print(f"{r['id']:<6} {r['category']:<14} {r['intent']:<24} {exec_op:<12} {has_sql:<4} {r['ms']:<8} {reply_short}")

    # ── Key metrics ─────────────────────────────────────────────────────
    total = len(results)
    ok = sum(1 for r in results if r["status"] == "OK")
    errors = sum(1 for r in results if r["status"] == "ERROR")
    intents = {}
    for r in results:
        i = r.get("intent", "?")
        intents[i] = intents.get(i, 0) + 1
    print(f"\nTotal: {total} | OK: {ok} | Errors: {errors}")
    print("Intent distribution:", dict(sorted(intents.items())))

    # ── Key regressions to flag ─────────────────────────────────────────
    print(f"\n{'='*100}")
    print("REGRESSION CHECK (previously failing queries)")
    print(f"{'='*100}")
    for r in results:
        qid = r["id"]
        intent = r.get("intent", "?")
        reply = (r.get("reply") or "").replace("\n", " ")
        # Flag key items
        if qid == "C01":
            ok_flag = intent == "NEEDS_CLARIFICATION"
            print(f"  {qid} create table      -> {intent}: {'OK' if ok_flag else 'REGRESSION'} | {reply[:80]}")
        elif qid == "C02":
            ok_flag = intent == "NEEDS_CLARIFICATION"
            print(f"  {qid} create students   -> {intent}: {'OK' if ok_flag else 'REGRESSION'} | {reply[:80]}")
        elif qid == "C03":
            ok_flag = "CREATE" in (intent or "")
            print(f"  {qid} id INTEGER, name  -> {intent}: {'OK' if ok_flag else 'REGRESSION'} | {reply[:80]}")
        elif qid == "R01":
            ok_flag = intent in ("QUERY", "SQL_RETRIEVAL") or "SELECT" in (r.get("sql") or "").upper()
            print(f"  {qid} show employees    -> {intent}: {'OK' if ok_flag else 'CHECK'} | SQL={r.get('sql','')[:60]}")
        elif qid == "A01":
            ok_flag = intent != "UNDERSTANDING_FAILED"
            print(f"  {qid} average salary    -> {intent}: {'OK' if ok_flag else 'REGRESSION'} | {reply[:80]}")
        elif qid == "G01":
            ok_flag = "CONVERSATION" in (intent or "") or "GREETING" in (intent or "")
            print(f"  {qid} hello             -> {intent}: {'OK' if ok_flag else 'REGRESSION'} | {reply[:80]}")
        elif qid == "V01":
            ok_flag = intent != "UNDERSTANDING_FAILED"
            print(f"  {qid} create pie chart  -> {intent}: {'OK' if ok_flag else 'REGRESSION'} | {reply[:80]}")

    # ── Save full results ───────────────────────────────────────────────
    out_path = os.path.join(os.path.dirname(__file__), "bb_baseline_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nFull results saved to {out_path}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
