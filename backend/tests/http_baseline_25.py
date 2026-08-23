"""
25-query HTTP baseline for pre/post architectural comparison.
Pure HTTP client — no backend imports.
"""
import requests, json, time, sys

BASE = "http://localhost:8000"
EMAIL = "diag2@example.com"
PASSWORD = "diagpass123"
SID = "baseline-25-" + str(int(time.time()))

def get_token():
    r = requests.post(f"{BASE}/api/auth/login", data={"username": EMAIL, "password": PASSWORD}, timeout=15)
    if r.status_code != 200:
        requests.post(f"{BASE}/api/auth/register", json={"email": EMAIL, "password": PASSWORD, "full_name": "Diag2"}, timeout=15)
        r = requests.post(f"{BASE}/api/auth/login", data={"username": EMAIL, "password": PASSWORD}, timeout=15)
    return r.json()["access_token"]

TOKEN = get_token()
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

def chat(msg):
    try:
        r = requests.post(f"{BASE}/api/chat", json={"message": msg, "session_id": SID}, headers=HEADERS, timeout=60)
        return r.status_code, r.json()
    except Exception as e:
        return "ERR", {"error": str(e)}

QUERIES = [
    ("01", "create table students"),
    ("02", "id INTEGER, name TEXT"),
    ("03", "create table students"),
    ("04", "id marks"),
    ("05", "create table with id and name"),
    ("06", "add table mine, duo"),
    ("07", "boyish"),
    ("08", "add sample data"),
    ("09", "add sample data to students"),
    ("10", "add sample data in that database"),
    ("11", "add table in it"),
    ("12", "show table employees"),
    ("13", "show employees"),
    ("14", "show employees in the Engineering department"),
    ("15", "show employees hired after 2022"),
    ("16", "what is the average salary in the salaries table"),
    ("17", "give me a summary of salary trends by department"),
    ("18", "create a pie chart of total salary by department"),
    ("19", "plot sales by department"),
    ("20", "show me all employees in the Engineering department"),
    ("21", "show me the top 5 of those"),
    ("22", "filter them by age greater than 20"),
    ("23", "group them by department"),
    ("24", "list databases"),
    ("25", "SELECT * FROM employees;"),
]

results = []
for num, msg in QUERIES:
    status, body = chat(msg)
    if status == "ERR" or not isinstance(body, dict):
        results.append({"num": num, "input": msg, "status": str(status), "intent": "ERROR",
                         "reply": str(body)[:200], "sql": None, "route": "N/A"})
        continue
    intent = body.get("intent", "?")
    reply = body.get("reply", "?")
    sql = body.get("sql")
    results.append({"num": num, "input": msg, "status": status, "intent": intent,
                     "reply": str(reply)[:200], "sql": sql})

# Save
with open("http_baseline_25.json", "w") as f:
    json.dump(results, f, indent=2, default=str)

# Print
for r in results:
    print(f"Q{r['num']}: intent={r['intent']:<25} sql={'YES' if r.get('sql') else 'NO':>3} reply={r['reply'][:100]}")

print(f"\n{len(results)} queries complete. Saved to http_baseline_25.json")
