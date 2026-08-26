# CLAUDE.md

Project knowledge for a Claude Code session working on this repo. Read this
before making changes — it captures things that aren't obvious from the code
alone: architectural traps, environment quirks, and where the bodies are
buried.

For the general architecture/setup/folder layout, see `README.md` — it's
accurate and current (rewritten 2026-08-23). This file is the layer above
that: gotchas, testing notes, and pointers into `docs/`.

## Start here

- `README.md` — architecture, setup, run instructions, current phase status.
- `docs/roadmap.md` — what's done, what's intentionally not done, known rough edges.
- `docs/errors_and_solutions.md` — non-obvious bugs found and fixed, with root cause.
- `docs/technical_reference.md` — technology/library reference, viva-ready.
- `docs/changes.md` — changelog for changes not yet folded into `git log` as commits.

## Critical environment quirk: killing the dev server on Windows

**`pkill -f uvicorn` from Git Bash frequently does not kill the process.**
Git Bash PIDs and Windows PIDs are different numbering systems, and a
backgrounded `uvicorn` survives `pkill` more often than not — the next
`uvicorn --port 8001` then silently fails to bind (`WinError 10048`), and
whatever `curl` you run next hits the **stale old process still running old
code**. This caused a genuine multi-hour debugging detour in this session
(see `docs/errors_and_solutions.md` — "Follow-up context stayed broken after
a fix that worked in isolation").

Always verify before trusting a restart:

```bash
# Bash: check what's actually listening
netstat -ano | grep ":8001" | grep LISTENING
```

```powershell
# PowerShell: reliably kill whatever holds the port
Get-NetTCPConnection -LocalPort 8001 -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
```

Then confirm the *new* process actually bound (check the log for
`Uvicorn running on...`, not just that `curl` got a 200 — a stale process
also returns 200).

## Architecture traps specific to this codebase

### Two parallel "understanding" systems — only one is live

There are **two intent-classification systems** in this codebase:

1. **Live / current**: `agent/semantic_frame.py` (NL → `SemanticFrame`) →
   `agent/universal_gateway.py` (`decide()`) → `agent/agent_coordinator.py`
   (`run()`, dispatches via `_SEMANTIC_TOOL_MAP`) → `agent/tools.py`. This is
   what `routers/chat.py` actually calls for every `/chat` request.
2. **Legacy / orphaned**: an older Gemini-router-prompt classification
   scheme (`agent/prompts.py`'s router prompt, intents like
   `SQL_RETRIEVAL`/`DATA_VISUALIZATION`/`DATA_ANALYSIS`) feeding
   `agent/handlers.py`'s `_HANDLERS_REGISTRY` (keyed by those legacy intent
   strings) and `agent/pipeline_registry.py`.

`routers/chat.py` calls `get_handler(intent)` where `intent` comes from the
*live* system's `legacy_intent` field (e.g. `"VISUALIZE"`, `"QUERY"`,
`"LIST_TABLES"`) — but `_HANDLERS_REGISTRY`'s keys were written for the
*legacy* system's vocabulary (e.g. `"DATA_VISUALIZATION"`, not
`"VISUALIZE"`). A mismatch here fails **silently** — `get_handler()` falls
back to `UnknownHandler`, which just hands the request to the general
planner. This is exactly how the chart-generation feature ended up
completely disconnected despite being fully implemented and unit-tested in
isolation (see `docs/errors_and_solutions.md`). **When adding a new
capability in `agent/capabilities.py`, check both `_SEMANTIC_TOOL_MAP`
(agent_coordinator.py) AND `_HANDLERS_REGISTRY` (agent/handlers.py) — a
capability with a `legacy_intent` that doesn't appear as a key in
`_HANDLERS_REGISTRY` will silently degrade to generic planner behavior with
no error.**

### `semantic_frame.py` builds a frame *before* context resolution runs

`interpret_message()` calls `_build_frame()` (which does entity grounding —
table/column matching against live schema) **before**
`context_resolution.resolve_references()` (which fills in an omitted table
from the previous turn). This means any grounding done inside `_build_frame`
happens with **no visibility into conversation context** by default. Two
bugs already came from this (see `docs/errors_and_solutions.md`):

- A word that's a column on the *current* table but which also
  alias-matches an unrelated table name (e.g. "salary" aliasing the
  "salaries" table via the entity resolver's pluralization rules) can
  hijack table selection before context ever gets a chance to say "no,
  we're still talking about `employees`."
- Attribute-word resolution for filters/superlatives run inside
  `_build_frame` too, so the same word can look ambiguous (matches a column
  on two different tables) even when the conversation already disambiguated
  which table is meant.

The fix pattern used (see `_parse_retrieve`/`_parse_visualize` in
`agent/semantic_frame.py`): thread a `context_table_hint` (the previous
turn's `frame.table`) down into `_build_frame` and use it *only* as a
tie-breaker for attribute-word resolution — never to override an
explicitly-named table. If you touch NL parsing in `semantic_frame.py`,
check whether it needs the same hint.

### The `EntityResolver`'s alias stage auto-generates singular/plural pairs

`utils/entity_resolver.py`'s `_generate_aliases()` automatically adds
singular↔plural forms for every candidate ("salary" ↔ "salaries", "category"
↔ "categories", etc.) as part of alias matching — this is intentional and
generally desirable, but it means a bare word that's semantically a *column*
can alias-match a *table* with a related name. Any code that tries to
ground a raw token as a table name (`grounding.ground_table`) needs to
first rule out that the token is meant as a column reference in context
(comparative filter, "X by Y" chart phrasing, etc.) — see
`_filter_reference_words()` in `semantic_frame.py` for the established
pattern.

## Testing

```bash
cd backend
source venv/Scripts/activate   # Windows Git Bash; venv\Scripts\activate.bat for cmd.exe
python -m pytest tests/ -q
```

248 tests, all passing as of this session. No CI configured — run manually.

For live/manual testing against a real Postgres connection, `backend/.env`
already has working `DB_HOST`/`DB_USER`/`DB_PASSWORD`/`DB_SUPERDB` pointing
at a local `hr_database` with sample tables (`employees`, `salaries`,
`employee` — note `employees` and `employee` are two *different* tables,
easy to confuse when testing).

Auth endpoints use OAuth2 form encoding for login, not JSON:
```bash
curl -X POST http://127.0.0.1:8001/api/auth/login \
  -d "username=<email>&password=<password>"
```
(`username` is the field name FastAPI's `OAuth2PasswordRequestForm`
expects; it holds the email.) `/register` and everything else is normal
JSON.

## Security notes

- `backend/.env` is gitignored and not tracked. **It was briefly committed
  to git history early in the project's life and pushed to the GitHub
  remote** (containing a real DB password and Gemini API keys); this was
  discovered and the history was rewritten with `git-filter-repo` +
  force-push to purge it (2026-08-25). See `docs/errors_and_solutions.md`.
  If you ever see `backend/.env` staged in `git status`, stop — it must
  never be committed.
- SQL statement-stacking (`;`-separated multi-statement SQL) is rejected at
  `validation/safety_checker.py`'s Gate 1 for every intent, not just DDL —
  added 2026-08-25. Don't remove this without understanding why (see
  `docs/errors_and_solutions.md`); it closes a real confirmation-bypass
  exploit.
- Multi-tenancy: every per-user data path keys off `current_user.id` (JWT,
  server-verified) or the `user_id_var` ContextVar it seeds — never a
  client-supplied field. This was audited (2026-08-25) and found sound; keep
  it that way. If you add a new endpoint that fetches "the user's X", filter
  by `current_user.id` at the query level, not just at the response level.
