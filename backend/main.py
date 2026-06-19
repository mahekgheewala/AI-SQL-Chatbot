from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import chat, schema, debug

app = FastAPI(title="AI SQL Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api")
app.include_router(schema.router, prefix="/api")
app.include_router(debug.router, prefix="/api")   # Phase 8.6: Gemini metrics


# ─── Phase 8.7: Startup Cache Initialization ─────────────────────────────────
# Populate routing_summaries and the database list at server start so the
# backend is self-sufficient before the frontend makes any /api/databases call.
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
