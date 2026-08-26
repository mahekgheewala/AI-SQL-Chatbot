# Technical Reference

Technology and architecture reference for this project. Items marked
**"This may be asked in a viva"** are the kind of thing an examiner is
likely to probe — know the *why*, not just the name.

---

## Stack

| Layer | Technology | Notes |
|---|---|---|
| Backend framework | FastAPI (0.110.0) | Async Python web framework, auto-generates OpenAPI docs at `/docs`. |
| Backend server | Uvicorn | ASGI server running FastAPI. |
| App database | SQLite via SQLAlchemy 2.x + Alembic | Stores users, sessions, connections, chat history — **not** the user's own data. |
| User data database | PostgreSQL 15+ | Each user connects their own instance; this is what NL questions actually query. |
| ORM | SQLAlchemy | Declarative models in `backend/models/domain.py`. |
| Migrations | Alembic | `backend/alembic/versions/` — three migrations: initial schema, chat sessions, Phase 9 security columns. |
| Auth | python-jose (JWT) + passlib/bcrypt | See "Authentication" below. |
| Encryption | `cryptography` (Fernet, symmetric) | Encrypts each user's stored Postgres credentials at rest. |
| AI / LLM | Google Gemini (`google-generativeai`, being deprecated in favor of `google-genai`) | Used only for complex NL→SQL requests the deterministic path can't handle. |
| Data processing | pandas, numpy | Result profiling (`agent/result_processing/`). |
| Charting | Plotly (backend spec generation) + `react-plotly.js` (frontend rendering) | Backend builds a JSON chart spec; frontend renders it as-is. |
| Fuzzy matching | Custom Levenshtein implementation + `rapidfuzz` (dependency present, custom implementation actually used in `EntityResolver`) | See "Entity Resolution" below. |
| Frontend framework | React 19 + Vite 8 | `frontend/src/`. |
| Frontend routing | `react-router-dom` v7 | `Chat`, `Dashboard`, `SetupDB`, `Auth/Login`, `Auth/Register`, `AdminDashboard`. |
| HTTP client | axios | Two separate instances: `services/api.js` (main app) and `services/adminApi.js` (admin dashboard). |

---

## Architecture

### Request pipeline (the one that matters — see CLAUDE.md for the orphaned legacy one)

```
User message
  → frontend Chat.jsx (POST /api/chat, JWT attached)
  → routers/chat.py::chat_endpoint
      → auth/dependencies.py::get_current_user (JWT verify)
      → session ownership check (_resolve_chat_session — 403 if session belongs to another user)
      → agent/universal_gateway.py::decide()
          → agent/semantic_frame.py::interpret_message()
              → action detection (regex/keyword-based, NOT an LLM call)
              → entity grounding (agent/grounding.py + utils/entity_resolver.py,
                against LIVE schema metadata — never invents table/column names)
              → capability lookup (agent/capabilities.py — a fixed registry,
                not LLM-decided)
              → context resolution (agent/context_resolution.py — follow-ups,
                pronouns, pending-clarification merging)
          → returns a GatewayDecision carrying route + SemanticFrame
      → agent/agent_coordinator.py::run()
          → _tool_from_frame(): maps the already-grounded frame straight to
            a tool call (deterministic SQL builder) when possible — NO
            Gemini call for simple requests ("local planner" bypass)
          → falls through to Gemini (ai/gemini_service.py) only when the
            frame needs reasoning the deterministic builder can't do
      → validation/ (3 gates: safety_checker → schema_checker →
        schema_creator_validator) — every SQL string, deterministic OR
        Gemini-generated, passes through this before execution
      → db/executor.py — runs against the user's own pooled Postgres
        connection (connections/connection_manager.py)
      → agent/result_processing/ — profiles the result (row/col counts,
        column semantic types, stats)
      → visualization/engine.py — IF the request was a chart capability,
        builds a Plotly spec from the profiled result
  → ChatResponse (reply, sql, execution, visualization, risk_level, ...)
  → frontend renders table / chart / clarification / confirmation prompt
```

**This may be asked in a viva:** *"Why doesn't every query go through the
LLM?"* — Because most NL requests ("show me all employees", "employees with
salary over 100000") have a small, well-defined structure that a
deterministic parser + SQL builder can handle exactly and safely, with zero
LLM latency/cost/hallucination risk. `agent/local_planner.py` and
`agent/deterministic_sql_builder.py` handle these. Gemini is reserved for
genuinely ambiguous or complex requests (multi-step reasoning, unusual
phrasing) that the deterministic path explicitly declines
(`is_deterministically_executable()` returning `False`). This is tracked —
`gemini_metrics.py` counts "Saved by Deterministic Routing" so you can see
the ratio live via `/api/admin/health`.

### Why two databases (SQLite app DB + user's own Postgres)?

**This may be asked in a viva:** The app's *own* operational data (user
accounts, sessions, chat history, encrypted connection credentials) is
completely separate from the data users are asking NL questions *about*.
This is a deliberate multi-tenancy boundary: the app database is shared
infrastructure the app itself owns; each user's Postgres connection is data
the app never owns, only borrows a connection to. SQLite is sufficient for
the app DB because access patterns are simple CRUD, not analytical.

### Validation: three gates, in order

1. **Safety checker** (`validation/safety_checker.py`) — permanently-blocked
   patterns (`ALTER SYSTEM`, `pg_read_file`, etc.), TRUNCATE promotion to
   CRITICAL_RISK, multi-statement rejection, and the authoritative
   intent→risk-level mapping (SAFE/HIGH_RISK/CRITICAL_RISK/BLOCKED).
2. **Schema checker** (`validation/schema_checker.py`) — does the
   table/column referenced actually exist, for DML intents.
3. **Schema/DDL creator validator** (`validation/schema_creator_validator.py`)
   — comment-injection guard, column-type allowlist, for CREATE/ALTER
   intents specifically.

**This may be asked in a viva:** *"Why three separate gates instead of
one?"* — Separation of concerns and fail-fast ordering: safety (can this
statement type ever run at all) is checked before schema validity (does
this specific table/column exist) is checked before DDL-specific structural
rules. A BLOCKED result from gate 1 stops the pipeline immediately — no
point checking whether a blocked statement's schema references are valid.

### Entity resolution (four-stage NL→schema matching)

`utils/entity_resolver.py`'s `EntityResolver.resolve()`:
1. Exact match (raw string equality)
2. Normalized exact match (case/separator-insensitive)
3. Alias match (auto-generated singular/plural + camelCase/snake_case
   splitting + static domain synonyms like `dept`→`department`)
4. Fuzzy match (Levenshtein ratio, threshold 0.72, ties within 0.05 are
   "ambiguous" not "best guess")

**This may be asked in a viva:** *"How does the system know 'dept' means
'department'?"* — Stage 3, a static synonym table plus automatically
generated naming variations (not a hardcoded list per domain — the
pluralization/abbreviation rules are generic English rules, applied to
whatever tables/columns actually exist live). This is also the exact
mechanism that caused a real bug in this session — "salary" auto-aliasing
to "salaries" and hijacking table selection during a follow-up question
(`docs/errors_and_solutions.md` #3). Know both the design intent and the
edge case it doesn't handle for free (word used as a column reference vs.
table reference — resolved by callers excluding known column-context words
from table candidacy, not by the resolver itself).

---

## Authentication

**This may be asked in a viva:** *"Walk me through what happens when a user
logs in."*

1. `POST /api/auth/login` (OAuth2 form-encoded, not JSON — `username` field
   holds the email) → `AuthenticationService.login()`.
2. Password checked via `passlib`/bcrypt (`auth/hashing.py`).
3. Rate limiting: 5 failures → 5-minute lockout, keyed by email
   (`auth/rate_limit.py` — in-memory, not persisted; see "Known
   limitations" below).
4. On success: an access JWT (30 min expiry, payload = `{sub: user_id,
   exp, type: "access"}` — **no role in the token**) and a refresh JWT (7
   days, includes a unique `jti`, hashed and stored server-side as a
   `UserSession` row for revocation).
5. `JWT_SECRET_KEY` is required at import time — the app refuses to start
   with no key or a known-insecure default (`auth/jwt.py`).

**This may be asked in a viva:** *"Why isn't the user's role embedded in
the JWT?"* — So a role change (e.g. promoting a user to admin) takes effect
on their very next request, not only after their token expires and they
re-login. `auth/dependencies.py::get_current_user` re-fetches the user row
(including role) from the database on every request; the JWT only proves
*identity*, not *authorization*.

**Logout** deletes the server-side `UserSession` row (refresh token
revoked) and deactivates all `ChatSession` rows for that user — it's not
purely client-side token deletion, though the short-lived access token
itself remains valid until its own expiry (a standard, accepted tradeoff
for stateless-JWT access tokens).

**Multi-tenancy / isolation:** every per-user lookup (chat sessions,
Postgres connections, in-memory metadata/session caches) is keyed by
`current_user.id` (from the verified JWT) or `user_id_var` (a `ContextVar`
seeded from that same verified identity in `main.py` middleware) — never
by a client-supplied field. Verified via `tests/test_multi_user_concurrency.py`
and a live audit in this session (register two users, confirm session
cross-access returns 403, confirm Postgres connections/metadata don't
leak).

---

## Known limitations (be honest about these if asked)

- **Rate limiting is in-process and non-persistent.** `auth/rate_limit.py`
  uses a plain `defaultdict` — resets on server restart, and doesn't share
  state across multiple worker processes if ever deployed with more than
  one uvicorn worker. Fine for a single-process dev/demo deployment; would
  need Redis or similar for real production scale-out.
- **Session state is in-memory** (`state/session_store.py`,
  `state/metadata_store.py`) — lost on restart, single-process only. Same
  caveat as above.
- **No CSV/Excel/PDF export, AI-generated reports, or results-table
  pagination/sorting** — these were in the *original* Phase 10 spec
  (`sql_phases/Phase 10.docx`) but development went toward the
  agent/entity-resolution architecture instead (Phase 10.4/10.5). Genuinely
  not built.
- **Two parallel intent-classification systems exist in the codebase** (one
  live, one orphaned) — see `CLAUDE.md`. This is a real piece of technical
  debt from an architecture migration, not a deliberate design.
