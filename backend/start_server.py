import os
import uvicorn
import threading
import time
import urllib.request
import sys

HOST = os.getenv("SERVER_HOST", "127.0.0.1")
PORT = int(os.getenv("SERVER_PORT", "8000"))


def run():
    uvicorn.run('main:app', host=HOST, port=PORT, log_level='warning')

t = threading.Thread(target=run, daemon=True)
t.start()

health_url = f'http://{HOST}:{PORT}/docs'
for i in range(20):
    time.sleep(1)
    try:
        r = urllib.request.urlopen(health_url, timeout=3)
        print(f'SERVER_READY')
        sys.stdout.flush()
        break
    except Exception:
        pass
else:
    print('SERVER_FAILED')
    sys.stdout.flush()
    sys.exit(1)

while True:
    time.sleep(60)
