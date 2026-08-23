import requests, json, time, sys, os
from datetime import datetime, timezone

TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIiwiZXhwIjoxNzg2MTY2MzUxfQ.-Pn8LH_FDj2-ZbPgerRjW4sZMjcEpiaSV9nf-Jm1CR0'
CHAT_URL = 'http://localhost:8000/api/chat'
HEADERS = {'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'}
SESSION_ID = 'audit-full-001'

TESTS = [
    ('G01','Greeting','Hello'),
    ('G02','Greeting','Hi there'),
    ('G03','Greeting','Good morning'),
    ('G04','Greeting','Who are you?'),
    ('T01','Typo','creat employe databse'),
    ('T02','Typo','shw all employes'),
    ('T03','Typo','selct * frm employee'),
    ('T04','Typo','lis al tabels in datbase'),
    ('Q01','SQL Query','Show all employees'),
    ('Q02','SQL Query','Show employees hired after 2023'),
    ('Q03','SQL Query','Count employees by department'),
    ('Q04','SQL Query','Average salary by department'),
    ('Q05','SQL Query','Top 5 highest paid employees'),
    ('Q06','SQL Query','Show products with stock below 10'),
    ('D01','DB Mgmt','Show all databases'),
    ('D02','DB Mgmt','Show all tables'),
    ('D03','DB Mgmt','Switch to hr_database'),
    ('D04','DB Mgmt','Create database test_validation_db'),
    ('C01','Clarification','Show employees'),
    ('C02','Clarification','Create table logs'),
    ('C03','Clarification','Delete records'),
    ('E01','Explanation','Explain INNER JOIN'),
    ('E02','Explanation','What is GROUP BY?'),
    ('E03','Explanation','Explain primary key'),
    ('E04','Explanation','Explain normalization'),
    ('V01','Conversation','Thank you'),
    ('V02','Conversation','Good night'),
    ('V03','Conversation','What can you do?'),
    ('V04','Conversation','Tell me a joke'),
    ('I01','Invalid','$$$$$$$$'),
    ('I02','Invalid','asdkjhasdkjh'),
    ('I03','Invalid','select from from where'),
    ('I04','Invalid','create create create'),
    ('X01','Edge Case',' '),
    ('X02','Edge Case','Show employees. Also explain JOIN. Also count by department.'),
    ('X03','Edge Case','Hello, can you shw me employes from hr databse please?'),
    ('X04','Edge Case','a'*200),
]

def send(msg, history=None):
    payload = {'message': msg, 'session_id': SESSION_ID, 'history': history or []}
    t = time.perf_counter()
    try:
        r = requests.post(CHAT_URL, json=payload, headers=HEADERS, timeout=90)
        ms = round((time.perf_counter()-t)*1000, 1)
        if r.status_code == 200:
            return True, ms, r.json(), None
        return False, ms, None, 'HTTP ' + str(r.status_code)
    except Exception as e:
        return False, round((time.perf_counter()-t)*1000, 1), None, str(e)

def grade(tid, cat, ok, d):
    if not ok: return 'FAIL'
    if not d: return 'FAIL'
    reply = d.get('reply','')
    intent = d.get('intent','')
    if tid == 'X01': return 'PASS' if isinstance(reply, str) else 'FAIL'
    if not reply or len(reply.strip()) < 2: return 'WARN'
    if cat in ('Greeting','Typo','Conversation'): return 'PASS'
    if cat == 'SQL Query':
        return 'PASS' if (d.get('sql') or (isinstance(d.get('execution'),dict) and d['execution'].get('success')) or len(reply)>20) else 'WARN'
    if cat == 'Clarification':
        return 'PASS' if (intent == 'NEEDS_CLARIFICATION' or d.get('question')) else 'WARN'
    if cat == 'Explanation': return 'PASS' if len(reply)>30 else 'WARN'
    if cat in ('Invalid','Edge Case','DB Mgmt'): return 'PASS' if reply else 'WARN'
    return 'PASS'

print('='*70)
print('  PHASE 1 + PHASE 2 INTEGRATION VALIDATION')
print('  ' + datetime.now(timezone.utc).isoformat())
print('='*70)
print()

results = []
history = []
total = len(TESTS)

for idx, (tid, cat, msg) in enumerate(TESTS, 1):
    disp = (msg[:55] + '...') if len(msg) > 55 else msg
    print('[' + str(idx).zfill(2) + '/' + str(total) + '] ' + tid + ' [' + cat + '] "' + disp + '"')
    ok, ms, d, err = send(msg, history=history[-6:])
    g = grade(tid, cat, ok, d)
    intent = d.get('intent','N/A') if d else 'N/A'
    reply = (d.get('reply','')[:100] if d else err or '')
    sql = d.get('sql') if d else None
    q = d.get('question') if d else None
    risk = d.get('risk_level') if d else None
    status_sym = {'PASS':'OK','WARN':'!!','FAIL':'XX'}.get(g,'??')
    print('  [' + status_sym + '] ' + g + ' | ' + str(ms) + 'ms | intent=' + intent)
    print('  reply: ' + repr(reply[:100]))
    if sql: print('  sql: ' + sql[:80])
    if q: print('  question: ' + q[:80])
    if err: print('  error: ' + str(err))
    print()
    sys.stdout.flush()

    rec = {'id':tid,'category':cat,'message':msg,'grade':g,'elapsed_ms':ms,
           'intent':intent,'reply':reply,'sql':sql,'question':q,'risk_level':risk,
           'api_success':ok,'error':err}
    results.append(rec)

    if d and d.get('reply'):
        history.append({'role':'user','text':msg})
        history.append({'role':'assistant','text':d['reply'][:200]})

grades = [r['grade'] for r in results]
passed = grades.count('PASS')
warned = grades.count('WARN')
failed = grades.count('FAIL')
times = [r['elapsed_ms'] for r in results if r['api_success']]

print('='*70)
print('  SUMMARY')
print('='*70)
print('Total=' + str(total) + ' PASS=' + str(passed) + ' WARN=' + str(warned) + ' FAIL=' + str(failed))
if times:
    print('Avg=' + str(round(sum(times)/len(times))) + 'ms  Min=' + str(round(min(times))) + 'ms  Max=' + str(round(max(times))) + 'ms')
    fastest = min(results, key=lambda r: r['elapsed_ms'])
    slowest = max(results, key=lambda r: r['elapsed_ms'])
    print('Fastest: ' + fastest['id'] + ' (' + str(fastest['elapsed_ms']) + 'ms)')
    print('Slowest: ' + slowest['id'] + ' (' + str(slowest['elapsed_ms']) + 'ms)')

print()
print('Issues:')
for r in results:
    if r['grade'] in ('FAIL','WARN'):
        print('  [' + r['grade'] + '] ' + r['id'] + ' [' + r['category'] + ']: ' + str(r.get('error') or 'unexpected'))

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'full_results.json')
with open(out, 'w', encoding='utf-8') as f:
    json.dump({'run_timestamp': datetime.now(timezone.utc).isoformat(),
               'total':total,'passed':passed,'warned':warned,'failed':failed,
               'avg_ms':round(sum(times)/len(times),1) if times else None,
               'min_ms':min(times) if times else None,
               'max_ms':max(times) if times else None,
               'results':results}, f, indent=2, ensure_ascii=False)
print('Results saved to: ' + out)
