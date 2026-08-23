import os
import uuid
import time
from utils.config_loader import init_env

# Initialize environment variables
init_env()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from routers import chat, schema, debug, admin, auth, db_setup
from utils.logging_config import setup_logging, request_id_var, user_id_var, logger_http

# Initialize logging on startup
setup_logging()

app = FastAPI(title="AI SQL Assistant")

@app.middleware("http")
async def log_http_request_response(request: Request, call_next):
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())[:8]
    
    # Bind context values
    req_token = request_id_var.set(request_id)
    
    # Extract user_id from token
    user_id = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        try:
            from jose import jwt
            from auth.jwt import SECRET_KEY, ALGORITHM
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            uid = payload.get("sub")
            if uid:
                user_id = int(uid)
        except Exception:
            pass
            
    user_token = user_id_var.set(user_id)
    client_ip = request.client.host if request.client else "unknown"
    path = request.url.path
    method = request.method

    try:
        logger_http.info(
            f"Incoming HTTP request: {method} {path}",
            extra={
                "category": "http",
                "operation_type": "HTTP_REQUEST",
                "client_ip": client_ip,
                "path": path,
                "method": method
            }
        )
    except Exception:
        pass

    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000
        
        try:
            logger_http.info(
                f"HTTP response: {method} {path} - {response.status_code} in {duration_ms:.2f}ms",
                extra={
                    "category": "http",
                    "operation_type": "HTTP_RESPONSE",
                    "client_ip": client_ip,
                    "path": path,
                    "method": method,
                    "status_code": response.status_code,
                    "execution_time_ms": duration_ms
                }
            )
        except Exception:
            pass
            
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            logger_http.error(
                f"HTTP request failed: {method} {path} - {str(e)}",
                exc_info=True,
                extra={
                    "category": "http",
                    "operation_type": "HTTP_ERROR",
                    "client_ip": client_ip,
                    "path": path,
                    "method": method,
                    "execution_time_ms": duration_ms
                }
            )
        except Exception:
            pass
        raise
    finally:
        request_id_var.reset(req_token)
        user_id_var.reset(user_token)

# ALLOWED_ORIGINS (comma-separated) takes priority when set; otherwise falls
# back to FRONTEND_URL (the same variable mail/email_service.py reads for
# verification/reset links) so a deployment only sets one URL in one place.
_allowed_origins_env = os.getenv("ALLOWED_ORIGINS")
if _allowed_origins_env:
    _allowed_origins = [o.strip() for o in _allowed_origins_env.split(",") if o.strip()]
else:
    _allowed_origins = [os.getenv("FRONTEND_URL", "http://localhost:5173")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api")
app.include_router(schema.router, prefix="/api")
app.include_router(debug.router, prefix="/api")   # Phase 8.6: Gemini metrics
app.include_router(admin.router, prefix="/api")   # Phase 9.5: Admin monitoring dashboard
app.include_router(auth.router, prefix="/api/auth")
app.include_router(db_setup.router, prefix="/api/db-setup")


# ─── Phase 8.7: Startup Cache Initialization ─────────────────────────────────
# Populate routing_summaries and the database list at server start so the
# backend is self-sufficient before the frontend makes any /api/databases call.

def print_gemini_metrics():
    from agent.gemini_metrics import get_metrics
    m = get_metrics()
    calls = m.get("calls", {})
    bypasses = m.get("bypasses", {})
    
    print("\n====================")
    print("GEMINI METRICS")
    print("====================")
    print("Planner Calls")
    print(calls.get("planner", 0))
    print("Router Calls")
    print(calls.get("router", 0))
    print("SQL Generator Calls")
    print(calls.get("sql_generator", 0))
    print("Summary Calls")
    print(calls.get("summarizer", 0))
    print("Visualization Calls")
    print(calls.get("report_formatter", 0))
    print("Retries")
    print(calls.get("retry_attempts", 0))
    print("Saved by Deterministic Routing")
    print(sum(bypasses.values()))
    print("====================\n")


@app.on_event("startup")
async def startup_initialize_cache():
    """
    Runs once when Uvicorn starts the application.

    Fetches all available PostgreSQL databases and builds the cross-database
    routing index (routing_summaries) so that:
      - Phase 7 Router AI has the full database catalogue available.
      - Phase 8.4 explicit DB extraction can validate names immediately.
      - Phase 7.1 session short-circuit works from the first request onward.
    """
    try:
        from db.schema_fetcher import fetch_all_databases
        from state.metadata_store import set_databases, refresh_routing_summaries

        print("\n[Phase 8.7] Startup: fetching database list...")
        dbs = fetch_all_databases()
        set_databases(dbs)
        print(f"[Phase 8.7] Startup: {len(dbs)} database(s) cached: {dbs}")

        refresh_routing_summaries()
        print("[Phase 8.7] Startup: routing summaries initialized.")
    except Exception as e:
        # Non-fatal — the server still starts; lazy refresh in chat.py covers this.
        print(f"[Phase 8.7] WARNING: Startup cache initialization failed: {e}")
        print("[Phase 8.7] Lazy refresh in /chat will recover on first request.")
    
    print_gemini_metrics()


@app.on_event("shutdown")
def shutdown_print_metrics():
    print("\n[Phase 10.4.x] Shutdown: compiling final Gemini Metrics...")
    print_gemini_metrics()
