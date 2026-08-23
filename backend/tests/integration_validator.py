"""
Phase 1 + Phase 2 Integration Validation Script
================================================
Sends all 10 test categories through the live /api/chat endpoint.
Captures full response + timing for each test case.
Outputs structured JSON results for the audit report.

Usage:
    cd backend
    python tests/integration_validator.py

Requires the server to be running at http://localhost:8000
"""

import json, time, sys, os, requests
from datetime import datetime, timezone

BASE_URL = "http://localhost:8000"
CHAT_URL = f"{BASE_URL}/api/chat"
LOGIN_URL = f"{BASE_URL}/api/auth/login"
SESSION_ID = "validation-session-001"

TEST_CASES = [
    {"id":"G01","category":"Greeting",      "message":"Hello"},
    {"id":"G02","category":"Greeting",      "message":"Hi there"},
    {"id":"G03","category":"Greeting",      "message":"Good morning"},
    {"id":"G04","category":"Greeting",      "message":"Who are you?"},
    {"id":"T01","category":"Typo",          "message":"creat employe databse"},
    {"id":"T02","category":"Typo",          "message":"shw all employes"},
    {"id":"T03","category":"Typo",          "message":"selct * frm employee"},
    {"id":"T04","category":"Typo",          "message":"lis al tabels in datbase"},
    {"id":"Q01","category":"SQL Query",     "message":"Show all employees"},
    {"id":"Q02","category":"SQL Query",     "message":"Show employees hired after 2023"},
    {"id":"Q03","category":"SQL Query",     "message":"Count employees by department"},
    {"id":"Q04","category":"SQL Query",     "message":"Average salary by department"},
    {"id":"Q05","category":"SQL Query",     "message":"Top 5 highest paid employees"},
    {"id":"Q06","category":"SQL Query",     "message":"Show products with stock below 10"},
    {"id":"D01","category":"DB Mgmt",       "message":"Show all databases"},
    {"id":"D02","category":"DB Mgmt",       "message":"Show all tables"},
    {"id":"D03","category":"DB Mgmt",       "message":"Switch to hr_database"},
    {"id":"D04","category":"DB Mgmt",       "message":"Create database test_validation_db"},
    {"id":"C01","category":"Clarification", "message":"Show employees"},
    {"id":"C02","category":"Clarification", "message":"Create table logs"},
    {"id":"C03","category":"Clarification", "message":"Delete records"},
    {"id":"E01","category":"Explanation",   "message":"Explain INNER JOIN"},
    {"id":"E02","category":"Explanation",   "message":"What is GROUP BY?"},
    {"id":"E03","category":"Explanation",   "message":"Explain primary key"},
    {"id":"E04","category":"Explanation",   "message":"Explain normalization"},
    {"id":"V01","category":"Conversation",  "message":"Thank you"},
    {"id":"V02","category":"Conversation",  "message":"Good night"},
    {"id":"V03","category":"Conversation",  "message":"What can you do?"},
    {"id":"V04","category":"Conversation",  "message":"Tell me a joke"},
    {"id":"I01","category":"Invalid",       "message":"$$$$$$$$"},
    {"id":"I02","category":"Invalid",       "message":"asdkjhasdkjh"},
    {"id":"I03","category":"Invalid",       "message":"select from from where"},
    {"id":"I04","category":"Invalid",       "message":"create create create"},
    {"id":"X01","category":"Edge Case",     "message":" "},
    {"id":"X02","category":"Edge Case",     "message":"Show employees. Also explain JOIN. Also count by department."},
    {"id":"X03","category":"Edge Case",     "message":"Hello, can you shw me employes from hr databse please?"},
    {"id":"X04","category":"Edge Case",     "message":"a"*400},
]

def get_auth_token():
    try:
        r = requests.post(LOGIN_URL, json={"username":"admin","password":"admin"}, timeout=10)
        if r.status_code == 200:
            return r.json().get("access_token")
    except Exception:
        pass
    return None

def send_chat(message, token, history=None):
    payload = {"message": message, "session_id": SESSION_ID, "history": history or []}
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    start = time.perf_counter()
    try:
        r = requests.post(CHAT_URL, json=payload, headers=headers, timeout=120)
        ms = (time.perf_counter() - start)*1000
        if r.status_code == 200:
            return {"success":True,"status_code":r.status_code,"response":r.json(),"elapsed_ms":round(ms,1),"headers":dict(r.headers)}
        return {"success":False,"status_code":r.status_code,"response":r.text,"elapsed_ms":round(ms,1),"error":f"HTTP {r.status_code}"}
    except requests.exceptions.ConnectionError:
        return {"success":False,"status_code":0,"response":None,"elapsed_ms":round((time.perf_counter()-start)*1000,1),"error":"CONNECTION_REFUSED"}
    except Exception as e:
        return {"success":False,"status_code":0,"response":None,"elapsed_ms":round((time.perf_counter()-start)*1000,1),"error":str(e)}

def grade_result(tc, result):
    if not result["success"]: return "FAIL"
    resp = result.get("response", {})
    if not isinstance(resp, dict): return "FAIL"
    reply = resp.get("reply", "")
    intent = resp.get("intent", "")
    cat = tc["category"]
    if tc["id"] == "X01": return "PASS" if isinstance(reply, str) else "FAIL"
    if not reply or len(reply.strip()) < 2: return "WARN"
    if cat in ("Greeting","Typo","Conversation"): return "PASS"
    if cat == "SQL Query":
        return "PASS" if (resp.get("sql") or (isinstance(resp.get("execution"),dict) and resp["execution"].get("success")) or len(reply)>20) else "WARN"
    if cat == "Clarification":
        return "PASS" if (intent=="NEEDS_CLARIFICATION" or resp.get("question")) else "WARN"
    if cat == "Explanation": return "PASS" if len(reply)>30 else "WARN"
    if cat in ("Invalid","Edge Case","DB Mgmt"): return "PASS" if reply else "WARN"
    return "PASS"

def run_validation():
    print("\n"+"="*70)
    print("  PHASE 1 + PHASE 2 INTEGRATION VALIDATION")
    print(f"  {datetime.now(timezone.utc).isoformat()}")
    print("="*70)
    try:
        hc = requests.get(f"{BASE_URL}/docs", timeout=5)
        print(f"\n[OK] Backend reachable HTTP {hc.status_code}")
    except Exception:
        print("\n[FATAL] Backend not reachable at http://localhost:8000"); sys.exit(1)
    token = get_auth_token()
    print(f"[{'OK' if token else '~~'}] Auth: {'obtained' if token else 'unavailable'}")
    results = []
    history = []
    total = len(TEST_CASES)
    for idx, tc in enumerate(TEST_CASES, 1):
        msg = tc["message"]
        display = (msg[:60]+"...") if len(msg)>60 else msg
        print(f"\n[{idx:02d}/{total}] {tc['id']} [{tc['category']}] \"{display}\"")
        result = send_chat(msg, token, history=history[-6:])
        grade = grade_result(tc, result)
        resp = result.get("response") or {}
        reply = (resp.get("reply","") if isinstance(resp,dict) else str(resp)[:100])
        intent = (resp.get("intent","N/A") if isinstance(resp,dict) else "N/A")
        icon = {"PASS":"OK","WARN":"!!","FAIL":"XX"}.get(grade,"??")
        print(f"  [{icon}] {grade:4s} | {result['elapsed_ms']:>7.1f}ms | intent={intent}")
        print(f"         reply: {reply[:120]!r}")
        if not result["success"]: print(f"         error: {result.get('error')}")
        rec = {
            "id":tc["id"],"category":tc["category"],"message":msg,"grade":grade,
            "elapsed_ms":result["elapsed_ms"],"intent":intent,"reply":reply,
            "sql": resp.get("sql") if isinstance(resp,dict) else None,
            "question": resp.get("question") if isinstance(resp,dict) else None,
            "valid": resp.get("valid") if isinstance(resp,dict) else None,
            "risk_level": resp.get("risk_level") if isinstance(resp,dict) else None,
            "clarification_data": resp.get("clarification_data") if isinstance(resp,dict) else None,
            "http_status":result["status_code"],"api_success":result["success"],"error":result.get("error"),
            "request_id":result.get("headers",{}).get("x-request-id"),
        }
        results.append(rec)
        if isinstance(resp,dict) and resp.get("reply"):
            history.append({"role":"user","text":msg})
            history.append({"role":"assistant","text":resp["reply"][:200]})

    grades = [r["grade"] for r in results]
    passed,warned,failed = grades.count("PASS"),grades.count("WARN"),grades.count("FAIL")
    times = [r["elapsed_ms"] for r in results if r["api_success"]]
    print("\n"+"="*70)
    print("  RESULTS SUMMARY")
    print("="*70)
    print(f"  Total: {total}  PASS: {passed}  WARN: {warned}  FAIL: {failed}")
    if times:
        print(f"  Avg: {sum(times)/len(times):.0f}ms  Min: {min(times):.0f}ms  Max: {max(times):.0f}ms")
        fastest = min(results, key=lambda r: r["elapsed_ms"])
        slowest = max(results, key=lambda r: r["elapsed_ms"])
        print(f"  Fastest: {fastest['id']} ({fastest['elapsed_ms']:.0f}ms)  Slowest: {slowest['id']} ({slowest['elapsed_ms']:.0f}ms)")
    for r in results:
        if r["grade"] in ("FAIL","WARN"):
            print(f"  [{'XX' if r['grade']=='FAIL' else '!!'}] {r['id']} [{r['category']}]: {r.get('error') or 'unexpected response'}")

    out = os.path.join(os.path.dirname(__file__), "integration_results.json")
    with open(out,"w",encoding="utf-8") as f:
        json.dump({"run_timestamp":datetime.now(timezone.utc).isoformat(),"total":total,"passed":passed,"warned":warned,"failed":failed,
                   "avg_ms":round(sum(times)/len(times),1) if times else None,"min_ms":min(times) if times else None,
                   "max_ms":max(times) if times else None,"results":results}, f, indent=2, ensure_ascii=False)
    print(f"\n  Results saved: {out}")
    return results

if __name__ == "__main__":
    run_validation()
