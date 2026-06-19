# AI SQL Assistant

An AI-powered chat interface that connects to a live PostgreSQL database, allows dynamic database and table management, and (in later phases) generates SQL queries using an LLM based on natural language questions.

---

## Current Project Status

**Phase 1 — Project Setup & Basic UI:** ✅ Complete  
**Phase 2 — PostgreSQL Connection & Schema System:** ✅ Complete  
**Phase 3 — AI SQL Generation:** ✅ Complete  
**Phase 3.5 — AI Usability, Clarification, & Refresh Infrastructure:** ✅ Complete  
**Phase 4 — SQL Validation, Risk Classification & Confirmation Layer:** ✅ Complete  
**Phase 5+ — SQL Execution & Results Display:** 🔲 Not started

---

## Implemented Phase 1 Features

| Feature | Status |
|---|---|
| React + Vite frontend scaffold | ✅ |
| Tailwind CSS integration | ✅ |
| `ChatWindow`, `MessageBubble`, `InputBar` components | ✅ |
| Enter to send, Shift+Enter for newline | ✅ |
| `api.js` Axios instance with `/api` proxy | ✅ |
| FastAPI backend with CORS middleware | ✅ |
| `POST /api/chat` echo stub | ✅ |
| Loading spinner & error banner | ✅ |
| Database dropdown in header | ✅ |
| **"＋ Create New Database" option in dropdown** | ✅ |
| **Create Database modal (name input, validation, loading)** | ✅ |
| **`POST /api/create-database` call from modal** | ✅ |
| **Auto-refresh dropdown after DB creation** | ✅ |
| **Auto-select newly created database** | ✅ |
| **Table dropdown (shown after DB selected)** | ✅ |
| **Auto-fetch tables when database changes** | ✅ |
| **"＋ Create New Table" option in table dropdown** | ✅ |
| **Create Table modal (dynamic column builder)** | ✅ |
| **Column type dropdown (TEXT/INTEGER/BOOLEAN/DATE/FLOAT/TIMESTAMP)** | ✅ |
| **Add/remove columns dynamically** | ✅ |
| **`POST /api/create-table` call** | ✅ |
| **Auto-select newly created table** | ✅ |
| **Toast notifications (success & error)** | ✅ |
| Global state: `databases`, `selectedDb`, `tables`, `selectedTable` | ✅ |
| Client-side validation on all modals | ✅ |

## Implemented Phase 2 Features

| Feature | Status |
|---|---|
| `backend/db/connection.py` — psycopg2 connection helper | ✅ |
| `backend/db/schema_fetcher.py` — fetch databases + schema | ✅ |
| `backend/db/schema_fetcher.py` — `format_schema_for_prompt()` | ✅ |
| `backend/state/metadata_store.py` — in-memory session cache | ✅ |
| `GET /api/databases` endpoint | ✅ |
| `POST /api/select-database` endpoint (caches schema + tables) | ✅ |
| `GET /api/schema` endpoint | ✅ |
| `.env` with DB credentials | ✅ |
| **`backend/db/database_manager.py` — dynamic DB creation** | ✅ |
| **`backend/db/table_manager.py` — table discovery + creation** | ✅ |
| **`POST /api/create-database` endpoint** | ✅ |
| **`GET /api/tables/{database_name}` endpoint** | ✅ |
| **`POST /api/create-table` endpoint** | ✅ |
| **DB name validation (regex whitelist)** | ✅ |
| **Column name/type validation** | ✅ |
| **Auto `id SERIAL PRIMARY KEY` on table creation** | ✅ |
| **Metadata store extended: `tables`, `table_schemas` fields** | ✅ |
| **Auto-refresh metadata after DB/table creation** | ✅ |
| Connection error handling (HTTP 400/500) | ✅ |

---

## Pending Features

| Feature | Phase |
|---|---|
| Gemini AI integration for SQL generation | Phase 3 |
| SQL query execution on selected DB | Phase 3 |
| SQL validation before execution | Phase 3 |
| Results display panel | Phase 3 |
| Conversation memory / context | Phase 6 |
| Multi-database routing | Phase 7 |

---

## Folder Structure

```
ai-sql-assistant/
├── backend/                        # FastAPI Python backend
│   ├── main.py                     # App entry point, CORS, router mounting
│   ├── routers/
│   │   ├── chat.py                 # POST /api/chat (echo stub, Phase 3 will expand)
│   │   └── schema.py               # DB/table management endpoints
│   ├── models/
│   │   └── schemas.py              # Pydantic request/response models
│   ├── db/
│   │   ├── __init__.py
│   │   ├── connection.py           # psycopg2 connection helper
│   │   ├── schema_fetcher.py       # fetch databases, schema, format for AI prompt
│   │   ├── database_manager.py     # dynamic database creation + validation
│   │   └── table_manager.py        # table discovery + dynamic table creation
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── gemini_service.py       # Single-call Gemini JSON architecture
│   │   ├── context_builder.py      # Merges DB/table/schema into AI context
│   │   ├── prompts.py              # System prompt (17 intents, NL support)
│   │   └── logger.py              # Gemini audit logger
│   ├── validation/                 # Phase 4 — SQL Validation Pipeline
│   │   ├── __init__.py
│   │   ├── safety_checker.py       # Gate 1: blocked pattern detection + risk classification
│   │   ├── schema_checker.py       # Gate 2: table/column existence validation
│   │   ├── schema_creator_validator.py  # Gate 3: DDL identifier + type validation
│   │   └── sql_validator.py        # Coordinator: orchestrates all gates, emits debug log
│   ├── state/
│   │   └── metadata_store.py       # in-memory session cache
│   ├── .env                        # DB credentials (not committed)
│   └── requirements.txt
│
├── frontend/                       # React + Vite frontend
│   ├── src/
│   │   ├── components/
│   │   │   ├── ChatWindow.jsx      # Scrollable message list
│   │   │   ├── MessageBubble.jsx   # User/assistant chat bubble
│   │   │   ├── InputBar.jsx        # Textarea + Send button
│   │   │   ├── CreateDatabaseModal.jsx  # Modal for creating a DB
│   │   │   ├── CreateTableModal.jsx     # Modal for creating a table
│   │   │   └── Toast.jsx           # Success/error toast notifications
│   │   ├── services/
│   │   │   └── api.js              # Axios instance + all API call functions
│   │   ├── App.jsx                 # Root component, all state management
│   │   └── main.jsx                # ReactDOM entry point
│   ├── index.html
│   ├── vite.config.js              # Dev server proxy /api → localhost:8000
│   ├── tailwind.config.js
│   └── package.json
│
├── sql_phases/                     # Phase requirement documents (Phase 1–10)
├── prompts/                        # AI prompt templates
├── docs/                           # Architecture, roadmap, devlogs
├── logs/
└── README.md
```

---

## Setup Instructions

### Prerequisites
- Python 3.11+
- Node.js 18+
- PostgreSQL 15+ running locally
- A PostgreSQL user with CREATE DATABASE privileges

### Backend Setup

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

Edit `backend/.env` with your PostgreSQL credentials:

```env
DB_HOST=localhost
DB_PORT=5432
DB_USER=your_pg_user
DB_PASSWORD=your_pg_password
DB_SUPERDB=postgres   # The DB to connect to when listing/creating databases
```

### Frontend Setup

```bash
cd frontend
npm install
```

---

## Run Instructions

### Start the Backend

```bash
cd backend
venv\Scripts\activate       # Windows
uvicorn main:app --reload --port 8000
```

Backend runs at: `http://localhost:8000`  
API docs available at: `http://localhost:8000/docs`

### Start the Frontend

```bash
cd frontend
npm run dev
```

Frontend runs at: `http://localhost:5173`  
All `/api/*` requests are proxied to the backend automatically.

---

## Changelog

### 2026-05-29

**Phase 1 & Phase 2 Additional Features Implemented**

**Backend:**
- Created `backend/db/database_manager.py` — dynamic database creation with `autocommit=True`, DB name validation
- Created `backend/db/table_manager.py` — table discovery via `information_schema`, dynamic `CREATE TABLE` with column type whitelist and auto-`id SERIAL PRIMARY KEY`
- Extended `backend/state/metadata_store.py` — added `tables` and `table_schemas` fields + `set_tables()` / `set_table_schemas()` functions
- Extended `backend/models/schemas.py` — added `CreateDatabaseRequest`, `ColumnDefinition`, `CreateTableRequest` Pydantic models
- Extended `backend/routers/schema.py` — added `POST /api/create-database`, `GET /api/tables/{database_name}`, `POST /api/create-table`; updated `POST /api/select-database` to also cache tables

**Frontend:**
- Created `frontend/src/components/Toast.jsx` — auto-dismiss success/error toast notifications
- Created `frontend/src/components/CreateDatabaseModal.jsx` — modal with name input, validation, loading state
- Created `frontend/src/components/CreateTableModal.jsx` — modal with dynamic column builder, type dropdown, auto-`id` row
- Extended `frontend/src/services/api.js` — added `createDatabase()`, `getTables()`, `createTable()`
- Updated `frontend/src/App.jsx` — added `tables`, `selectedTable`, modal states, toast system, table dropdown, full create-DB/table flow
- Fixed `frontend/index.html` — page title changed from "frontend" to "AI SQL Assistant"

### 2026-05-30

**Phase 3 Implementation: Gemini AI SQL Generation**

**Backend:**
- Created `backend/ai/logger.py` — dedicated Python logger for auditing Gemini intent, context, and generated SQL.
- Created `backend/ai/prompts.py` — centralized system instructions enforcing strict structured JSON output and hallucination prevention.
- Created `backend/ai/context_builder.py` — abstraction layer that merges `current_db`, `current_table`, `available_dbs`, and `raw_schema` into a dynamic context block.
- Created `backend/ai/gemini_service.py` — implemented single-call architecture utilizing Gemini 1.5 Pro with `response_mime_type="application/json"` to simultaneously classify intent and generate SQL in a single deterministic pass.
- Updated `backend/models/schemas.py` — added `intent`, `database`, `sql` fields to `ChatResponse`; added `table` field to `ChatRequest`.
- Updated `backend/routers/chat.py` — completely replaced the mock echo endpoint with the fully integrated AI pipeline.

**Frontend:**
- Updated `frontend/src/components/MessageBubble.jsx` — added UI block to dynamically render the AI's detected `intent`, context `database`, and a formatted monospace block for the generated `sql`.
- Updated `frontend/src/App.jsx` — integrated `selectedTable` into the chat request state.
- Updated `frontend/src/services/api.js` — modified `sendMessage` to pass the currently selected table to the backend.

### 2026-06-03

**Phase 3.5 Implementation: AI Usability, Clarification, & Refresh Infrastructure**

**Backend:**
- Updated Pydantic models in `backend/models/schemas.py` to support `history` in `ChatRequest` and clarification fields + `refresh_*` indicators in `ChatResponse` (as infrastructure for Phase 4 execution).
- Updated `GEMINI_SYSTEM_PROMPT` in `backend/ai/prompts.py` to cover all 17 intents, handle broken/incomplete English statements, and guide clarification questions.
- Refactored `backend/ai/gemini_service.py` to format conversation histories into prompt contexts and parse the LLM's clarification responses.
- Refactored `backend/routers/chat.py` to forward history state, configure response models with refresh flags (defaulting to `False` pending Phase 4 execution), and clean up duplicate imports.

**Frontend:**
- Updated API client `frontend/src/services/api.js` to send conversation history turns in the request body.
- Refactored state manager `frontend/src/App.jsx` to manage chat history state, transmit context windows, and define auto-refresh hooks that will trigger dropdown updates once the backend returns `True` flags upon execution success.
- Visualized clarification mode inside `frontend/src/components/MessageBubble.jsx` with a unique amber style card and prefix icon.

### 2026-06-04

**Phase 4 Implementation: SQL Validation, Risk Classification & Confirmation Layer**

**Backend:**
- Created `backend/validation/__init__.py` — Python package marker for the new validation module.
- Created `backend/validation/safety_checker.py` — Gate 1: detects permanently-blocked PostgreSQL system operations (`ALTER SYSTEM`, `ALTER ROLE`, `COPY TO/FROM PROGRAM`, `pg_read_file`, `pg_write_file`) via compiled regex. Maps all 17 Gemini intents to their authoritative risk level (SAFE / HIGH_RISK / CRITICAL_RISK). Promotes `TRUNCATE` SQL to `CRITICAL_RISK` even when classified under `DELETE`. `RENAME_COLUMN` classified as `HIGH_RISK` (can break existing queries).
- Created `backend/validation/schema_checker.py` — Gate 2: validates DML SQL references tables/columns that exist in the cached `metadata_store` schema. Uses intent-specific keyword extraction (`FROM`, `INTO`, `UPDATE`) to identify the target table. Validates `SET` clause column names for `UPDATE` operations.
- Created `backend/validation/schema_creator_validator.py` — Gate 3: validates DDL creation SQL for SQL injection comment patterns and column type whitelist compliance (`ADD_COLUMN`, `MODIFY_COLUMN`).
- Created `backend/validation/sql_validator.py` — Coordinator: orchestrates all three gates with early-exit logic. `NEEDS_CLARIFICATION` skips validation entirely (`risk_level=null`). Emits a detailed `PHASE 4 VALIDATION` debug log block to the Uvicorn console for every request.
- Updated `backend/models/schemas.py` — added `valid`, `risk_level: str | None`, `requires_confirmation`, `blocked_reason` to `ChatResponse`.
- Updated `backend/routers/chat.py` — integrated Phase 4 validation after Gemini generation; moved `build_schema_context` import to module level; risk-aware reply text for all validation outcomes; all Phase 3.5 branches preserved exactly.

**Frontend:**
- Updated `frontend/src/components/MessageBubble.jsx` — added SAFE green badge, HIGH_RISK amber card (Confirm/Cancel), CRITICAL_RISK red card (type `CONFIRM` exactly to enable Proceed), BLOCKED slate card with reason, validation failure card. All new cards render after the existing SQL block. Phase 3.5 UI unchanged.
- Updated `frontend/src/components/ChatWindow.jsx` — threads `confirmedMessages`, `cancelledMessages`, `onConfirm`, `onCancel` props to `MessageBubble`. Changed key from array index to `msg.id`.
- Updated `frontend/src/App.jsx` — added `confirmedMessages`/`cancelledMessages` Set state with `handleConfirm`/`handleCancel` callbacks; extended `assistantMsg` with Phase 4 validation fields; passed new props to `ChatWindow`. Confirmation is purely visual — no API calls.

### 2026-06-04

**Phase 5 Implementation: SQL Execution & Response System**

**Backend:**
- Created `backend/db/executor.py` — A generic, intent-agnostic execution engine. It intercepts `SELECT` queries to `fetchall()` without committing, intercepts `CREATE DATABASE` to bypass transaction blocks using `conn.autocommit = True`, and wraps all other DDL/DML statements in strict `commit()` / `rollback()` blocks to guarantee transaction integrity.
- Updated `backend/models/schemas.py` — Added `ExecutionResult` and `ExecuteConfirmedRequest` schemas; attached `execution` field to `ChatResponse`.
- Updated `backend/routers/chat.py` — Added execution auto-trigger for `SAFE` operations directly inside `/chat`.
- Updated `backend/routers/chat.py` — Created `POST /execute-confirmed` endpoint for `HIGH_RISK` and `CRITICAL_RISK` operations. Implements a critical dual-validation step: it runs the Phase 4 validation engine *again* on the incoming request to prevent client-side tampering before executing the SQL.
- Configured dynamic state refresh: execution of schema-altering operations automatically sets `refresh_databases`, `refresh_tables`, and `refresh_schema` flags to sync the frontend UI and metadata cache.

**Frontend:**
- Created `frontend/src/components/ResultsTable.jsx` — A responsive, scrollable grid to cleanly render arrays of data returned by `SELECT` queries.
- Created `frontend/src/components/ExecutionMessage.jsx` — A status card module to visually confirm successful DML/DDL modifications (e.g., `✅ UPDATE successful`) or display backend execution failures caught during transaction rollback.
- Updated `frontend/src/services/api.js` — Added `executeConfirmed()` async HTTP method.
- Updated `frontend/src/App.jsx` — Updated the `handleConfirm` callback to fire the new API endpoint. Updates the original message *in place* to seamlessly replace the Phase 4 warning card with the executed Phase 5 result without duplicating chat bubbles. Automatically re-fetches UI state (databases, tables) if the backend returns refresh flags.
- Updated `frontend/src/components/MessageBubble.jsx` — Automatically mounts `ResultsTable` or `ExecutionMessage` directly below the SQL code block as soon as the `execution` field is populated, masking the confirmation cards.

### 2026-06-04 (Phase 6)

**Phase 6 Implementation: Session Context Memory Layer**

**Architecture:**
Phase 6 adds a structured RAM-only session memory layer. Each browser session generates a transient UUID, which acts as a key into a Python dictionary held in process memory. The session tracks conversational context (selected database, selected table, recent databases/tables, recent operations, last successful intent) independently from the global schema cache (`metadata_store.py`). All memory is lost on backend restart or browser refresh — this is intentional by design.

**Session Update Rule (critical):** Session memory is updated **only** after confirmed successful execution (i.e., `ExecutionResult.success == True`). Memory is never updated based on SQL generation alone, preventing stale state from failed operations.

**Backend:**
- Created `backend/state/session_store.py` — Lightweight RAM-only store (`dict[session_id → session_fields]`). Exposes `get_session()`, `update_session()`, and `log_session_state()` helpers. Completely independent from `metadata_store.py`.
- Updated `backend/models/schemas.py` — Added `session_id: str | None` to `ChatRequest` and `ExecuteConfirmedRequest`.
- Updated `backend/ai/context_builder.py` — Extended `build_schema_context` to accept an optional `session` dict. When present, prepends a `CURRENT SESSION MEMORY` block to the Gemini context string, providing the AI with `selected_database`, `selected_table`, `recent_databases`, `recent_tables`, `recent_operations`, and `last_successful_intent`.
- Updated `backend/routers/chat.py` — Added `_update_session_from_execution()` helper that extracts the new database/table name from the SQL string and commits the update. Session is loaded before context building and updated in both `/chat` (SAFE auto-execute) and `/execute-confirmed` (HIGH_RISK/CRITICAL_RISK confirmation) endpoints.

**Frontend:**
- Updated `frontend/src/App.jsx` — Added `const [sessionId] = useState(() => crypto.randomUUID())`. The UUID is generated once when the tab opens and lives only in React state. Passed to `sendMessage` and `executeConfirmed` calls. No localStorage, sessionStorage, or cookies used.
- Updated `frontend/src/services/api.js` — Updated `sendMessage` and `executeConfirmed` signatures to accept and forward `session_id` in POST body.

### 2026-06-04 (Phase 6.1)

**Phase 6.1 Refinement: Bridging Session Memory to Execution Pipeline**

**Architecture:**
Phase 6.1 closes the logic gap between Gemini's language comprehension and the backend executor. When Gemini uses Phase 6 Session Memory to resolve pronouns like "it" to a specific database, that resolution is now passed down the pipeline and explicitly controls the physical PostgreSQL connection opened by the executor. 

**Backend:**
- Updated `backend/ai/prompts.py` — Extended JSON schema to include `execution_database`. Added explicit rules preventing its use for SQL concepts (tables, columns) and enforcing it as the physical connection target.
- Updated `backend/ai/gemini_service.py` — Parser updated to extract and return `execution_database`.
- Updated `backend/ai/prompts.py` — Extended JSON schema to include `execution_database`. Added explicit rules preventing its use for SQL concepts (tables, columns) and enforcing it as the physical connection target.
- Updated `backend/ai/gemini_service.py` — Parser updated to extract and return `execution_database`.
- Updated `backend/routers/chat.py` — Re-wrote executor database resolution to follow a strict fallback chain: `execution_database (AI resolved)` → `current_db (Frontend dropdown)` → `session.selected_database (RAM memory)`. Phase 4 Validators remain strictly untouched passthrough filters. Added `PHASE 6.1 EXECUTION CONTEXT` debug logging.

### 2026-06-05 (Phase 7)

**Phase 7 Implementation: AI Database Routing Layer**

**Architecture:**
Phase 7 adds a lightweight AI pre-routing step that fires **before** SQL generation on every `/chat` request. A dedicated Gemini router model instance (`router_service.py`) examines the user's question, the cross-database routing summary, Phase 6 session memory, and conversation history to select the correct database target. This result feeds into an expanded 4-level fallback chain for `target_db` resolution.

**Routing Call Flow:**
```
User Request
   ↓
Phase 6: Load Session
   ↓
Phase 7: route_to_database() → router_db  ← ONE Gemini call
   ↓
target_db = router_db or execution_database or current_db or session.selected_database
   ↓
Phase 3: Generate SQL (unchanged)
   ↓
Phase 4: Validate (unchanged)
   ↓
Phase 5: Execute against target_db (unchanged)
```

**Lazy Routing Summaries:**
A `routing_summaries` dict (`{ db_name: [table1, table2, ...] }`) is cached in `metadata_store`. It is rebuilt lazily only when database/table structure changes — not on every request. This ensures the router always has current metadata without database overhead.

**Backend:**
- Created `backend/ai/router_service.py` — Lightweight router that makes ONE Gemini call using `DATABASE_ROUTER_PROMPT`. Validates the returned database name against known routing summaries to prevent hallucination. Returns `None` on any failure to fall back gracefully.
- Updated `backend/ai/prompts.py` — Added `DATABASE_ROUTER_PROMPT` constant with routing rules and examples.
- Updated `backend/state/metadata_store.py` — Added `routing_summaries` to `_store`, `set_routing_summaries()`, and `refresh_routing_summaries()` lazy builder.
- Updated `backend/routers/chat.py` — Added Phase 7 routing call. Extended fallback chain from 3-level to 4-level. Added lazy routing refresh after successful DDL execution.
- Updated `backend/routers/schema.py` — Added `refresh_routing_summaries()` calls after `GET /databases`, `POST /create-database`, and `POST /create-table` endpoints to keep routing index in sync.
