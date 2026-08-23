"""
PHASE 10.6 FORENSIC DIAGNOSTIC — REAL APPLICATION HTTP TESTS
Tests every query from the original failure list via live HTTP.
Tracks session state, pending_clarification, and server console output.
"""
import requests
import json
import time
import sys
import threading
import re
from collections import defaultdict

BASE = "http://localhost:8000"
EMAIL = "diag2@example.com"
PASSWORD = "diagpass123"

# ── Auth ──────────────────────────────────────────────────────────────────────
def get_token():
    r = requests.post(f"{BASE}/api/auth/login", data={"username": EMAIL, "password": PASSWORD}, timeout=15)
    if r.status_code != 200:
        requests.post(f"{BASE}/api/auth/register", json={"email": EMAIL, "password": PASSWORD, "full_name": "Diag2"}, timeout=15)
        r = requests.post(f"{BASE}/api/auth/login", data={"username": EMAIL, "password": PASSWORD}, timeout=15)
    return r.json()["access_token"]

TOKEN = get_token()
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
print(f"[AUTH] Token obtained: {TOKEN[:20]}...")

def chat(msg, session_id="diag-forensic-v1"):
    r = requests.post(
        f"{BASE}/api/chat",
        json={"message": msg, "session_id": session_id},
        headers=HEADERS,
        timeout=60,
    )
    return r.status_code, r.json()

def get_session_state(session_id="diag-forensic-v1"):
    from state.session_store import get_session
    return get_session(session_id)

# ── First: refresh metadata so routing_summaries are populated ────────────────
print("\n" + "="*60)
print("REFRESHING METADATA")
print("="*60)
try:
    r = requests.get(f"{BASE}/api/databases", headers=HEADERS, timeout=30)
    print(f"Databases list: {r.status_code} - {r.text[:300]}")
except Exception as e:
    print(f"Database refresh failed: {e}")

# ── Get current metadata state ────────────────────────────────────────────────
try:
    from state.metadata_store import get_metadata
    meta = get_metadata()
    print(f"\nMETADATA STATE:")
    print(f"  databases: {meta.get('databases', [])}")
    print(f"  tables: {meta.get('tables', [])}")
    print(f"  selected_db: {meta.get('selected_db')}")
    print(f"  routing_summaries keys: {list(meta.get('routing_summaries', {}).keys())}")
    for db, tbls in meta.get('routing_summaries', {}).items():
        print(f"    {db}: {tbls}")
    schemas = meta.get('table_schemas', meta.get('schema', {}))
    print(f"  table_schemas: {list(schemas.keys()) if schemas else 'none'}")
except Exception as e:
    print(f"Metadata check failed: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# TEST QUERIES
# ══════════════════════════════════════════════════════════════════════════════

results = []

def run_query(label, msg, session_id="diag-forensic-v1"):
    print(f"\n{'='*60}")
    print(f"QUERY: {label}")
    print(f"INPUT: {msg}")
    print(f"{'='*60}")
    t0 = time.time()
    try:
        status, body = chat(msg, session_id)
        elapsed = time.time() - t0
        print(f"HTTP STATUS: {status}")
        print(f"RESPONSE: {json.dumps(body, indent=2, default=str)[:2000]}")
        
        # Extract key fields
        intent = body.get("intent", "N/A")
        reply = body.get("reply", "N/A")
        sql = body.get("sql")
        database = body.get("database")
        valid = body.get("valid")
        question = body.get("question")
        execution = body.get("execution")
        refresh_databases = body.get("refresh_databases")
        refresh_tables = body.get("refresh_tables")
        
        print(f"\nEXTRACTED:")
        print(f"  intent: {intent}")
        print(f"  reply: {str(reply)[:200]}")
        print(f"  sql: {sql}")
        print(f"  database: {database}")
        print(f"  valid: {valid}")
        print(f"  question: {question}")
        if execution:
            print(f"  execution.success: {execution.get('success') if isinstance(execution, dict) else 'ExecutionResult object'}")
            print(f"  execution.operation: {execution.get('operation') if isinstance(execution, dict) else getattr(execution, 'operation', 'N/A')}")
            print(f"  execution.row_count: {execution.get('row_count') if isinstance(execution, dict) else getattr(execution, 'row_count', 'N/A')}")
        
        # Check session state after
        try:
            sess = get_session_state(session_id)
            pending = sess.get("pending_clarification")
            semantic_pending = sess.get("semantic_pending_frame")
            print(f"\nSESSION STATE:")
            print(f"  selected_database: {sess.get('selected_database')}")
            print(f"  selected_table: {sess.get('selected_table')}")
            print(f"  pending_clarification: {json.dumps(pending, default=str)[:300] if pending else 'None'}")
            print(f"  semantic_pending_frame: {semantic_pending}")
            print(f"  last_successful_intent: {sess.get('last_successful_intent')}")
        except Exception as e:
            print(f"  [session check error: {e}]")
        
        results.append({
            "label": label,
            "input": msg,
            "status": status,
            "intent": intent,
            "reply": str(reply)[:300],
            "sql": sql,
            "database": database,
            "valid": valid,
            "elapsed": elapsed,
        })
    except Exception as e:
        elapsed = time.time() - t0
        print(f"ERROR: {e}")
        results.append({
            "label": label,
            "input": msg,
            "status": "ERROR",
            "intent": "ERROR",
            "reply": str(e)[:300],
            "sql": None,
            "database": None,
            "valid": None,
            "elapsed": elapsed,
        })

# ── Group 1: CREATE TABLE flow ───────────────────────────────────────────────
run_query("T01 - create table id, marks", "create table id, marks")
run_query("T02 - create table students", "create table students")
run_query("T03 - id INTEGER, name TEXT (continuation)", "id INTEGER, name TEXT")
run_query("T04 - id marks (continuation attempt)", "id marks")

# ── Group 2: Greetings / context reset ───────────────────────────────────────
run_query("G01 - heyaa", "heyaa")
run_query("G02 - thanks", "thanks")

# ── Group 3: Basic data queries ──────────────────────────────────────────────
run_query("R01 - show employees", "show employees")
run_query("R02 - show me employees in the Engineering department", "show me employees in the Engineering department")
run_query("R03 - what is the average salary in the salaries table", "what is the average salary in the salaries table")
run_query("R04 - give me a summary of salary trends by department", "give me a summary of salary trends by department")

# ── Group 4: Visualization ───────────────────────────────────────────────────
run_query("V01 - create a pie chart", "create a pie chart")

# ── Group 5: Entity grounding ────────────────────────────────────────────────
run_query("E01 - add table mine, duo", "add table mine, duo")
run_query("E02 - add table in it", "add table in it")
run_query("E03 - add sample data in that database", "add sample data in that database")
run_query("E04 - add sample data", "add sample data")

# ── Group 6: SQL detection ───────────────────────────────────────────────────
run_query("S01 - SELECT * FROM employees;", "SELECT * FROM employees;")

# ── Group 7: DB management ───────────────────────────────────────────────────
run_query("D01 - list databases", "list databases")
run_query("D02 - list tables", "list tables")
run_query("D03 - create a new database whose name is my_yellow_world", "create a new database whose name is my_yellow_world")

# ── Group 8: Follow-up context ───────────────────────────────────────────────
run_query("C01 - show me the top 5 of those", "show me the top 5 of those")
run_query("C02 - filter them by age greater than 20", "filter them by age greater than 20")
run_query("C03 - group them by department", "group them by department")

# ── Group 9: Edge cases ──────────────────────────────────────────────────────
run_query("L01 - empty context reference", "what about those ones")
run_query("L02 - completely unknown", "xyzzy plugh")

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
print("\n\n" + "="*80)
print("SUMMARY TABLE")
print("="*80)
print(f"{'Label':<45} {'HTTP':>4} {'Intent':<25} {'Valid':>5} {'SQL?':>4}")
print("-"*80)
for r in results:
    has_sql = "YES" if r.get("sql") else "NO"
    print(f"{r['label']:<45} {str(r['status']):>4} {str(r['intent']):<25} {str(r.get('valid')):>5} {has_sql:>4}")

# Save raw results
with open("forensic_raw_results.json", "w") as f:
    json.dump(results, f, indent=2, default=str)
print("\nRaw results saved to forensic_raw_results.json")
