import requests, json, time, sys
from datetime import datetime, timezone

TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIiwiZXhwIjoxNzg2MTY2MzUxfQ.-Pn8LH_FDj2-ZbPgerRjW4sZMjcEpiaSV9nf-Jm1CR0'
BASE_URL = 'http://localhost:8000'
CHAT_URL = BASE_URL + '/api/chat'
SESSION_ID = 'validation-001'

def send(message, history=None):
    payload = {'message': message, 'session_id': SESSION_ID, 'history': history or []}
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + TOKEN}
    start = time.perf_counter()
    try:
        r = requests.post(CHAT_URL, json=payload, headers=headers, timeout=120)
        ms = round((time.perf_counter() - start)*1000, 1)
        if r.status_code == 200:
            return {'ok': True, 'ms': ms, 'data': r.json()}
        return {'ok': False, 'ms': ms, 'status': r.status_code, 'err': r.text[:200]}
    except Exception as e:
        return {'ok': False, 'ms': 0, 'err': str(e)}

tests = [
    ('Hello', 'Greeting'),
    ('creat employe databse', 'Typo'),
    ('Show all employees', 'SQL Query'),
    ('Explain INNER JOIN', 'Explanation'),
    ('Thank you', 'Conversation'),
]
for msg, cat in tests:
    r = send(msg)
    if r['ok']:
        d = r['data']
        intent = d.get('intent', '')
        reply = d.get('reply', '')[:80]
        print('[' + cat + '] ' + repr(msg))
        print('  intent=' + intent + '  ms=' + str(r['ms']))
        print('  reply=' + repr(reply))
    else:
        print('[' + cat + '] FAILED: ' + str(r.get('err')) + ' status=' + str(r.get('status')))
    print()
