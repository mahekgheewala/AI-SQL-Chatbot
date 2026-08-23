"""
PHASE 10.6 FORENSIC DIAGNOSTIC v2 — PURE HTTP CLIENT
Runs all queries via live HTTP. No backend imports.
"""
import requests
import json
import time
import sys

BASE = "http://localhost:8000"
EMAIL = "diag2@example.com"
PASSWORD = "diagpass123"
SID = "forensic-v2-" + str(int(time.time()))

def get_token():
    r = requests.post(f"{BASE}/api/auth/login", data={"username": EMAIL, "password": PASSWORD}, timeout=15)
    if r.status_code != 200:
        requests.post(f"{BASE}/api/auth/register", json={"email": EMAIL, "password": PASSWORD, "full_name": "Diag2"}, timeout=15)
        r = requests.post(f"{BASE}/api/auth/login", data={"username": EMAIL, "password": PASSWORD}, timeout=15)
    return r.json()["access_token"]

TOKEN = get_token()
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
print(f"[AUTH] Token obtained: {TOKEN[:20]}...")
print(f"[SESSION] {SID}")

def chat(msg):
    try:
        r = requests.post(f"{BASE}/api/chat", json={"message": msg, "session_id": SID}, headers=HEADERS, timeout=60)
        return r.status_code, r.json()
    except Exception as e:
        return "ERR", {"error": str(e)}

results = []

def run_query(label, msg):
    print(f"\n{'='*60}")
    print(f"QUERY: {label} | INPUT: {msg}")
    print(f"{'='*60}")
    t0 = time.time()
    status, body = chat(msg)
    elapsed = time.time() - t0

    if status == "ERR" or not isinstance(body, dict):
        print(f"  ERROR: {body}")
        results.append({"label": label, "input": msg, "status": str(status), "intent": "ERROR",
                         "reply": str(body)[:200], "sql": None, "valid": None, "elapsed": elapsed})
        return

    intent = body.get("intent", "N/A")
    reply = body.get("reply", "N/A")
    sql = body.get("sql")
    valid = body.get("valid")
    refresh_db = body.get("refresh_databases")
    refresh_tb = body.get("refresh_tables")
    execution = body.get("execution")

    reply_short = str(reply)[:150] if reply else "None"
    has_sql = "YES" if sql else "NO"
    exec_info = ""
    if execution:
        if isinstance(execution, dict):
            exec_info = f" exec_ok={execution.get('success')} rows={execution.get('row_count')} op={execution.get('operation')}"
        else:
            exec_info = f" exec_obj={type(execution).__name__}"

    print(f"  status={status} intent={intent} valid={valid} sql={has_sql}{exec_info}")
    print(f"  reply: {reply_short}")

    results.append({
        "label": label, "input": msg, "status": status, "intent": intent,
        "reply": reply_short, "sql": sql, "valid": valid, "elapsed": elapsed,
        "refresh_db": refresh_db, "refresh_tb": refresh_tb,
    })

# ── Group 1: CREATE TABLE flow ───────────────────────────────────────────────
run_query("T01 create table students", "create table students")
run_query("T02 create table id, marks", "create table id, marks")
run_query("T03 id INTEGER, name TEXT", "id INTEGER, name TEXT")
run_query("T04 id marks", "id marks")

# ── Group 2: DDL - databases ────────────────────────────────────────────────
run_query("D01 create database named test_db", "create database named test_db")
run_query("D02 create a new database whose name is test_db_2", "create a new database whose name is test_db_2")
run_query("D03 list databases", "list databases")
run_query("D04 list tables", "list tables")
run_query("D05 add sample data in that database", "add sample data in that database")
run_query("D06 add sample data", "add sample data")

# ── Group 3: Routing / intent ────────────────────────────────────────────────
run_query("R01 show table", "show table")
run_query("R02 show employees", "show employees")
run_query("R03 show employees in the Engineering department", "show employees in the Engineering department")
run_query("R04 show employees hired after 2022", "show employees hired after 2022")
run_query("R05 what is the average salary in the salaries table", "what is the average salary in the salaries table")
run_query("R06 give me a summary of salary trends by department", "give me a summary of salary trends by department")

# ── Group 4: Visualization ───────────────────────────────────────────────────
run_query("V01 create a pie chart", "create a pie chart")
run_query("V02 plot sales by department", "plot sales by department")

# ── Group 5: Follow-ups ─────────────────────────────────────────────────────
run_query("F01 show all employees in Engineering", "show all employees in Engineering")
run_query("F02 what about those ones", "what about those ones")
run_query("F03 group them by department", "group them by department")
run_query("F04 show me the top 5 of those", "show me the top 5 of those")
run_query("F05 filter them by age greater than 20", "filter them by age greater than 20")

# ── Group 6: Greetings / edge ────────────────────────────────────────────────
run_query("G01 hi", "hi")
run_query("G02 hey", "hey")
run_query("G03 heyaa", "heyaa")
run_query("G04 hello", "hello")
run_query("G05 thanks", "thanks")
run_query("E01 xyzzy plugh", "xyzzy plugh")

# ── Group 7: Raw SQL ─────────────────────────────────────────────────────────
run_query("SQL01 SELECT * FROM employees;", "SELECT * FROM employees;")

# ── Group 8: Entity grounding ────────────────────────────────────────────────
run_query("EN01 add table mine, duo", "add table mine, duo")

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
print("\n\n" + "=" * 100)
print("SUMMARY TABLE")
print("=" * 100)
print(f"{'Label':<40} {'HTTP':>4} {'Intent':<30} {'Valid':>5} {'SQL':>3} {'s':>5}")
print("-" * 100)
for r in results:
    has_sql = "YES" if r.get("sql") else "NO"
    print(f"{r['label']:<40} {str(r['status']):>4} {str(r['intent']):<30} {str(r.get('valid')):>5} {has_sql:>3} {r['elapsed']:>4.1f}")

# Save raw
with open("forensic_raw_results_v2.json", "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"\n{len(results)} queries complete. Saved to forensic_raw_results_v2.json")
