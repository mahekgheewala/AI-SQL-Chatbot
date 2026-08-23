# AI SQL Assistant

An AI-powered chat interface that connects to a user's own PostgreSQL database,
supports multi-user accounts, and turns natural-language questions into SQL —
generating, validating, executing, and visualizing results — with an admin
dashboard for logs and analytics.

---

## Current Project Status

**Phases 1–9 — Core assistant, validation, execution, session memory, routing, logging:** ✅ Complete
**Phase 9.5 — Auth, multi-tenancy, and admin dashboard:** ✅ Complete
**Phase 10.4 — Automated chart generation:** ✅ Complete
**Phase 10.5 — Universal entity resolution & metadata registry:** ✅ Complete
**Phase 10 (original spec) — CSV/Excel/PDF export, AI-generated reports, results-table pagination/sorting:** 🔲 Not started

The project outgrew its original 10-phase plan partway through: instead of the
originally-scoped export/reporting features, development went deeper on a more
general agent architecture (entity resolution, a local planner that skips the
LLM for simple requests, and automatic chart generation). See `phases_content.txt`
and `sql_phases/` for the original phase specs; they describe the plan as
conceived, not always the path actually taken.

---

## Architecture

### Backend (`backend/`, FastAPI)

| Area | Modules | Purpose |
|---|---|---|
| Persistence | `db/app_database.py`, `models/domain.py`, `alembic/` | SQLAlchemy-backed SQLite app database (users, sessions, connections, chat history) with Alembic migrations. Separate from the user's own Postgres data. |
| Auth | `auth/`, `services/authentication_service.py`, `services/user_service.py`, `repositories/user_repository.py`, `repositories/session_repository.py`, `repositories/password_reset_repository.py`, `routers/auth.py`, `mail/` | JWT auth, password hashing/encryption, rate limiting/lockout, password reset via email. |
| Connections | `connections/`, `services/connection_service.py`, `repositories/connection_repository.py`, `routers/db_setup.py` | Per-user encrypted Postgres connection storage and pooling — each user connects to their own database. |
| AI pipeline | `agent/`, `ai/`, `utils/entity_resolver.py`, `utils/compatibility_validator.py`, `state/metadata_registry.py` | Typo correction → local planner (deterministic SQL for simple requests) → universal entity resolution against live schema → Gemini generation for complex requests → capability gating. |
| Validation | `validation/` | Three-gate SQL safety/schema/DDL validation before anything executes. |
| Execution | `db/executor.py`, `db/database_manager.py`, `db/table_manager.py` | Runs validated SQL against the caller's own Postgres connection. |
| Results & charts | `agent/result_processing/`, `visualization/` | Profiles query results and automatically builds Plotly chart specs. |
| Admin | `monitoring/`, `analytics/`, `routers/admin.py` | Structured logging, aggregated analytics, health snapshot, and CSV/JSON export — admin-role gated. |

### Frontend (`frontend/`, React + Vite)

Routed via `react-router-dom`: `Chat`, `Dashboard`, `SetupDB` (connection
wizard), `Auth/Login` + `Auth/Register`, and `AdminDashboard` (with
`LogExplorer`, `AnalyticsPanel`, `HealthPanel`, `ExportModal`). `AuthContext`
holds the JWT and is attached to every API call by `services/api.js`.
`PlotlyChart.jsx` renders chart specs returned by the backend.

---

## Setup Instructions

### Prerequisites
- Python 3.11+
- Node.js 18+
- PostgreSQL 15+ (each user connects their own instance via the setup wizard)

### Backend Setup

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
alembic upgrade head
```

Create `backend/.env` (never commit this file):

```env
GEMINI_API_KEY=your_key
GEMINI_FALLBACK_API_KEY=your_fallback_key
GEMINI_MODEL=gemini-1.5-pro
JWT_SECRET_KEY=your_secret
ENCRYPTION_KEY=your_fernet_key
DB_SUPERDB=postgres          # superuser-style connection used for CREATE DATABASE flows
SMTP_SERVER=localhost
SMTP_PORT=1025
FRONTEND_URL=http://localhost:5173
```

Each user's own Postgres connection (host/port/user/password/database) is no
longer set in `.env` — it's entered once through the in-app setup wizard
(`SetupDB.jsx` → `POST /api/db-setup/save-connection`) and stored encrypted
per-account. `DB_SUPERDB` is still a global `.env` setting because creating a
brand-new database needs a connection to an existing one first.

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
# or: python start_server.py
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

### Run Tests

```bash
cd backend
venv\Scripts\activate
pytest tests/ -q
```

---

## Folder Structure

```
ai-sql-assistant/
├── backend/                        # FastAPI Python backend
│   ├── main.py                     # App entry point, CORS, router mounting
│   ├── start_server.py             # Convenience entrypoint
│   ├── routers/                    # chat, schema, auth, admin, db_setup, debug
│   ├── models/                     # Pydantic schemas + SQLAlchemy ORM (domain.py)
│   ├── db/                         # App database, Postgres execution, schema tools
│   ├── auth/                       # JWT, hashing/encryption, rate limiting
│   ├── connections/                # Per-user Postgres connection pooling
│   ├── services/ , repositories/   # Business logic / data-access layers
│   ├── agent/                      # Typo correction, local planner, entity
│   │                                 resolution, result processing
│   ├── ai/                         # Gemini integration, prompts, model manager
│   ├── validation/                 # SQL safety/schema/DDL validation gates
│   ├── visualization/              # Automatic chart generation (Plotly)
│   ├── analytics/ , monitoring/    # Query analytics + admin logging/export
│   ├── utils/                      # Entity resolver, config loader, logging
│   ├── state/                      # In-memory metadata/session caches
│   ├── mail/                       # Password reset email delivery
│   ├── alembic/                    # Database migrations
│   └── tests/                      # Pytest suite
│
├── frontend/                       # React + Vite frontend
│   └── src/
│       ├── pages/                  # Chat, Dashboard, SetupDB, Auth, AdminDashboard
│       ├── components/             # Chat UI, PlotlyChart, admin/ dashboard widgets
│       ├── context/                # AuthContext
│       └── services/               # api.js, adminApi.js
│
├── sql_phases/                     # Original phase requirement documents (1–10)
├── prompts/                        # AI prompt templates
├── docs/                           # Architecture, roadmap, devlogs
└── README.md
```

---

## Known Gaps

- No CSV/Excel/PDF export, AI-generated report, or pagination/sorting on the
  results table — these were in the original Phase 10 spec but development
  went a different direction (see Status above).
- `pytest.ini`/CI is not configured — tests are run manually.
