# AI SQL Assistant

## What this project is

An AI-powered chat interface that lets a user connect their **own**
PostgreSQL database and ask questions about it in plain English. The
assistant turns natural language into SQL, validates it for safety,
executes it against the user's database, and returns the results — as a
table, or as an automatically-generated chart when appropriate. It supports
multiple user accounts (each with their own isolated database connection
and chat history) and includes an admin dashboard for logs and analytics.

**The goal (the "why" behind the project):** most people who need answers
from a database don't know SQL. The motive here was to build something that
actually behaves like a careful junior data analyst sitting next to you —
understanding a question, checking it against the real schema (never
guessing table/column names that don't exist), asking for clarification
instead of silently doing the wrong thing when a request is ambiguous, and
refusing to run something dangerous without your explicit confirmation —
rather than a generic "text-to-SQL" demo that only works on cherry-picked
examples.

---

## Architecture / Pipeline

```
User message (frontend Chat UI)
  → POST /api/chat  (JWT attached)
  → Auth middleware (verifies identity, scopes everything to that user)
  → Universal Semantic Gateway  (agent/semantic_frame.py)
      → deterministic action + entity detection (regex/keyword based,
        NOT an LLM call — fast, free, and grounded against the user's
        REAL live schema, never invented table/column names)
      → if the request is simple and fully understood: build SQL directly,
        no LLM involved at all
      → if it's ambiguous or incomplete: ask a clarification question
        (tracked in session state) instead of guessing
      → if it's genuinely complex: hand off to an LLM (Groq first, Gemini
        as fallback) for reasoning
  → Validation (3 gates, every SQL statement, no exceptions)
      1. Safety gate — blocks dangerous patterns, rejects multi-statement
         SQL, classifies risk (SAFE / HIGH_RISK / CRITICAL_RISK / BLOCKED)
      2. Schema gate — confirms every referenced table/column actually
         exists
      3. DDL/creator gate — structural checks for CREATE/ALTER statements
  → Execution (against the user's own pooled Postgres connection —
    every user's data connection is fully isolated from every other user's)
  → Result profiling (pandas-based — infers column types, generates stats)
  → Chart generation (if the request was a chart — builds a Plotly spec
    automatically from the profiled result)
  → Response → frontend renders a table and/or chart, or a clarification
    prompt, or a "type CONFIRM to proceed" prompt for risky operations
```

Two databases are involved, and they're deliberately separate:
- **The app's own database** (SQLite + SQLAlchemy + Alembic migrations) —
  stores user accounts, sessions, encrypted per-user Postgres credentials,
  and chat history. This is infrastructure the app owns.
- **Each user's own Postgres database** — the actual data they're asking
  questions about. The app never owns this data, only borrows a connection
  to it, encrypted at rest and never shared between users.

---

## Technologies used

| Layer | Technology |
|---|---|
| Backend framework | FastAPI (Python) |
| Backend server | Uvicorn |
| App database | SQLite via SQLAlchemy + Alembic migrations |
| User data database | PostgreSQL (each user connects their own instance) |
| Auth | JWT (python-jose) + bcrypt password hashing + Fernet encryption for stored DB credentials |
| AI / LLM | Groq (`openai/gpt-oss-20b`, fast first-attempt reasoning) + Google Gemini (fallback + report/summary formatting) |
| Data processing | pandas, numpy |
| Charting | Plotly (backend builds the spec) + `react-plotly.js` (frontend renders it) |
| Fuzzy/entity matching | Custom 4-stage resolver (exact → normalized → alias/pluralization → Levenshtein fuzzy) against the live schema |
| Frontend | React 19 + Vite, `react-router-dom` |
| HTTP client | axios |

---

## What's actually been built (status)

**Core assistant, validation, execution, session memory, AI routing, logging** — done.
**Auth, multi-tenancy, admin dashboard** — done and independently audited (see below).
**Automatic chart generation** — done, but was completely disconnected from
live requests for a long time without anyone noticing (see "Problems we
found and fixed"); reconnected and working for the common path, though the
less-common path (see "Known, currently-broken issues" below) still has
real bugs.
**Universal entity resolution against the live schema** — done; this is
what lets the assistant say "that column doesn't exist" instead of
hallucinating one, and what powers alias matching (e.g. "dept" → "department").
**LLM-backed clarification resolution** — done: when the fast, deterministic
parser can't cleanly understand a follow-up reply (a multi-word answer to
"what should I name this table, and what columns?", or "add sample data"
needing actual row values generated), it falls back to an LLM call with
full conversational context, and validates whatever comes back against the
real schema before trusting it — never blind LLM output.
**CSV/Excel/PDF export, AI-generated reports, results-table pagination/sorting** — not started. These were in the original project plan, but development went a different direction (see "What's left," below) instead.

### A full security and correctness audit was performed on this project, covering:

- **Multi-user isolation** — verified end-to-end with two real accounts:
  cross-user session access correctly returns 403, unauthenticated requests
  correctly return 401, per-user Postgres connections and credentials never
  leak between accounts, admin endpoints correctly reject non-admin users.
- **A leaked-credentials incident** — `backend/.env` (a real database
  password and API keys) had been committed to git history early in the
  project and was live on the GitHub remote. This was discovered, the
  history was rewritten to purge it, and it was force-pushed to remove it
  from GitHub. *(Anyone reading this: if you're continuing this project,
  make sure those original credentials were rotated — a history purge
  doesn't undo an earlier exposure.)*
- **A SQL statement-stacking vulnerability** — a confirmed, "safe-looking"
  operation could smuggle a second, unconfirmed destructive SQL statement
  riding along after a semicolon. Closed at the validation layer for every
  request path, not just the one it was first found in.
- **An admin dashboard that silently 401'd on every single API call** —
  the frontend's admin API client was never updated to attach the login
  token after the backend was properly locked down. Fixed.
- **A "confirm" flow that looked like it worked but didn't** — the chatbot
  would tell you to "Type CONFIRM to proceed" for a dangerous operation, but
  typing it as a plain chat message never actually worked (a session-state
  bug meant the confirmation was never remembered) — only a dedicated UI
  button worked. Fixed so both paths actually function, and both are now
  properly re-validated before executing.

---

## Problems we found and fixed along the way (the actual development story)

This project went through several rounds of "the chatbot seems to be
hallucinating / doing the wrong thing" bug reports, and in nearly every
case, the real cause was **not** the AI model making things up — it was the
deterministic routing/parsing logic in front of the AI taking a wrong turn,
or two pieces of session state falling out of sync. Worth knowing if you
extend this project, because the pattern repeats:

- **A follow-up question would silently switch to the wrong table.** A
  filter word like "salary" would alias-match an unrelated table (like
  "salaries", via the entity resolver's own singular/plural matching)
  before the parser ever got a chance to notice the conversation was
  already talking about a specific, different table.
- **A hardcoded blocklist meant the literal word "name" could never become
  a real column name.** The app uses "name"/"named"/"called" as its own
  grammar markers ("name it X"), and that blocklist was applied too broadly
  — so a user typing "columns: id, name" would silently lose "name" out of
  their new table.
- **The entire chart-generation feature was unreachable from live
  requests**, despite being fully built and unit-tested — a one-character
  mismatch between two different intent-naming systems inside the codebase
  meant every chart request silently fell back to a plain, chart-less query.
- **A stale conversation state would hijack the next, unrelated message.**
  Finishing one clarification (e.g. "what columns should this table have?")
  wouldn't always fully clear an older, parallel piece of tracked state —
  so the very next thing you typed could get misread as more input for a
  conversation that had already finished, occasionally resulting in a
  bogus table being created out of stray words in your next sentence.
- **Sample/dummy data generation had no uniqueness guarantee.** Asking the
  assistant to "add sample data" twice in a row could generate the same
  `id` value both times. Fixed by having the app check what's already in
  the table and deterministically guarantee uniqueness afterward, rather
  than just hoping the AI got it right.

Full root-cause write-ups for each of these live in `docs/errors_and_solutions.md`.

---

## Known, currently-broken issues (being upfront about this)

These were found during testing and **have not been fixed yet**:

1. **Some chart requests can silently corrupt the data shown back to you.**
   When a chart request doesn't clearly specify a numeric measure and the
   AI picks a column on its own, the charting code can end up trying to
   treat text data as numbers — and because of how the result is handled
   internally, that corruption can leak into the plain data table shown
   alongside the chart (the values look like they turned into blanks). The
   underlying database is never touched — a fresh query afterward shows
   the real data again — but the response you're looking at in that moment
   is wrong.
2. **A chart-summary sentence is hardcoded to talk about "employees" no
   matter what table you're actually charting**, because the original code
   was only ever tested against the demo HR dataset.
3. **A rare failure path can try to execute your raw chat message as if it
   were SQL**, which safely gets rejected with a confusing error message
   rather than doing anything harmful, but shouldn't happen at all.
4. **Chart requests that don't explicitly say which column to chart (e.g.
   "create a pie chart of books" with no "by X") aren't reliably asking for
   clarification** — sometimes they do, sometimes the AI just picks
   something on its own, correctly or not. This is a design gap: the chart
   feature never *requires* a specific measure column the way it requires
   *some* chart request at all.

See the conversation history / project notes for full root-cause traces
of each of these — they've been diagnosed precisely, just not yet fixed.

---

## What's left

- Fix the four issues listed above.
- CSV/Excel/PDF export of query results, AI-generated Markdown reports over
  results, and pagination/sorting/column-filtering on the results table —
  these were in the original project plan but were never built; development
  went toward the entity-resolution/chart-generation architecture instead.
- A manual database-selector override in the UI.
- No CI is configured — the test suite (274 tests) is run manually.
- Rate limiting and session/metadata caches are in-process memory only —
  fine for a single-process demo, would need something like Redis for a
  real multi-worker production deployment.
- An older, now-unused intent-classification system still exists alongside
  the live one in a couple of backend files — only the parts of it that
  were actually causing user-visible bugs were disconnected/fixed; the rest
  was left alone since it isn't reachable from real requests.

---

## Setup Instructions

### Prerequisites
- Python 3.11+
- Node.js 18+
- PostgreSQL 15+ (each user connects their own instance via the in-app setup wizard)

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
GROQ_API_KEY=your_groq_key
JWT_SECRET_KEY=your_secret
ENCRYPTION_KEY=your_fernet_key
DB_SUPERDB=postgres          # superuser-style connection used for CREATE DATABASE flows
SMTP_SERVER=localhost
SMTP_PORT=1025
FRONTEND_URL=http://localhost:5173
```

Each user's own Postgres connection (host/port/user/password/database) is
**not** set in `.env` — it's entered once through the in-app setup wizard
(`SetupDB.jsx` → `POST /api/db-setup/save-connection`) and stored encrypted
per-account. `DB_SUPERDB` is still a global `.env` setting because creating
a brand-new database needs a connection to an existing one first.

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
│   ├── agent/                      # NL understanding, entity resolution, LLM
│   │                                 clarification resolver, result processing
│   ├── ai/                         # Gemini integration, prompts, model manager
│   ├── validation/                 # SQL safety/schema/DDL validation gates
│   ├── visualization/              # Automatic chart generation (Plotly)
│   ├── analytics/ , monitoring/    # Query analytics + admin logging/export
│   ├── utils/                      # Entity resolver, config loader, logging
│   ├── state/                      # In-memory metadata/session caches
│   ├── mail/                       # Password reset email delivery
│   ├── alembic/                    # Database migrations
│   └── tests/                      # Pytest suite (274 tests)
│
├── frontend/                       # React + Vite frontend
│   └── src/
│       ├── pages/                  # Chat, Dashboard, SetupDB, Auth, AdminDashboard
│       ├── components/             # Chat UI, PlotlyChart, admin/ dashboard widgets
│       ├── context/                # AuthContext
│       └── services/               # api.js, adminApi.js
│
├── sql_phases/                     # Original phase requirement documents
├── prompts/                        # AI prompt templates
├── docs/                           # errors_and_solutions.md, technical_reference.md, changes.md
├── CLAUDE.md                       # Developer/AI-assistant notes on codebase gotchas
└── README.md                       # This file
```
