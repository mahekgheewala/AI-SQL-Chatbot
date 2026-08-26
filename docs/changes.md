# Changes

Uncommitted-as-of-writing changes from the 2026-08-25 full-project audit.
Once these land as commits, `git log` becomes the record — this file exists
because several of these changes need more "why" than a commit message
carries, and because they were made before being organized into commits.
See `docs/errors_and_solutions.md` for full root-cause writeups of the
starred (★) items.

## Security

- ★ **Rejected multi-statement SQL at the safety gate**
  (`validation/safety_checker.py`) — closes a confirmation-bypass exploit
  where a second `;`-separated statement could ride along on a confirmed
  lower-risk statement. Applies to every intent, not just DDL.
- ★ **Purged `backend/.env` from git history and force-pushed** — it had
  been committed early in the project and was live on the GitHub remote
  (real DB password + Gemini API keys). Rewrote history with
  `git-filter-repo`, verified zero remaining references, force-pushed to
  `origin/main`. **Action still needed by the project owner: rotate the
  leaked DB password and Gemini API keys** — the history purge doesn't
  invalidate credentials that were already exposed.
- Added rate limiting to `POST /api/auth/forgot-password`
  (`routers/auth.py`) — previously uncapped, an email-bombing vector.

## Multi-tenancy (in progress before this session, completed/verified here)

- `connections/connection_manager.py::get_allowed_databases` +
  `db/schema_fetcher.py::fetch_all_databases` — database listing now scoped
  to the requesting user's own `default_database` + ACL'd
  `allowed_databases`, intersected with what actually exists on the server.
  Previously leaked every database on the shared Postgres server to every
  user, and excluded whichever database `DB_SUPERDB` happened to point to
  (which could be a user's own database, making it invisible to them).
- `db/schema_fetcher.py::fetch_schema` — now seeds every table name from
  `information_schema.tables` first, then fills in columns — a table with
  zero columns (e.g. right after `CREATE TABLE x()`, before any `ALTER
  TABLE ADD COLUMN`) no longer silently disappears from every listing.
- `routers/chat.py` — a session with no explicitly selected database now
  defaults to the user's actually-connected database
  (`PostgresConnection.default_database`) instead of falling through to a
  clarification prompt or an empty result for ambiguous first requests.
- `agent/semantic_frame.py` — "update"/"insert" (bare, no row/column/table
  cues) and value-assignment language ("give Alice a raise") now fail into
  a clarification instead of the "select" catch-all silently running a
  SELECT and reporting a requested data *change* as if it had succeeded.
  There is genuinely no `update_row`/`insert_row` capability yet
  (`agent/capabilities.py`), so this is the correct behavior, not a
  regression.

## Chatbot pipeline correctness

- ★ **Fixed follow-up questions resolving to the wrong table**
  (`agent/semantic_frame.py::_parse_retrieve`) — a filter-clause word that
  happens to alias-match an unrelated table name (via the entity
  resolver's automatic singular/plural pairing) no longer hijacks table
  selection away from the conversation's actual established table.
- ★ **Wired the chart/visualization pipeline into the live request path**
  (`agent/semantic_frame.py`, `agent/agent_coordinator.py`,
  `routers/chat.py`) — a fully-built, unit-tested visualization engine
  existed but was completely unreachable from real requests due to an
  intent-key mismatch between two parallel classification systems. Chart
  requests now build correct SQL, execute it, and return a real Plotly
  spec instead of silently degrading to a plain query with no chart.

## Frontend

- `frontend/src/services/adminApi.js` — added the JWT request interceptor
  (and matching 401-refresh interceptor) that every admin dashboard API
  call needs; previously every one of them 401'd against the (correctly)
  auth-gated backend, even for a logged-in admin.
- `frontend/src/pages/AdminDashboard.jsx` — corrected a stale
  "Unprotected in dev" comment that no longer matched reality.

## Testing performed

- `pytest tests/ -q` — 248/248 passing, re-run after every backend change
  in this batch.
- `npm run build` (frontend) — clean build after the `adminApi.js` change.
- Live end-to-end testing against a real local Postgres instance
  (`hr_database`): registration, login (two separate users), cross-user
  session-access rejection (403), unauthenticated chat rejection (401),
  basic query, follow-up/context query (before and after the fix),
  dangerous-operation confirmation flow (`DROP TABLE` correctly required
  `CONFIRM`), admin endpoint access control (200 for admin / 401
  unauthenticated / 403 authenticated-non-admin), and full chart generation
  end-to-end.

## Not changed (flagged, not fixed — see technical_reference.md "Known limitations")

- Rate limiting remains in-process/non-persistent (would need Redis+ for
  real multi-worker production use).
- Session/metadata state remains in-memory (same caveat).
- The legacy/orphaned intent-classification system
  (`agent/handlers.py`'s `_HANDLERS_REGISTRY`,
  `agent/pipeline_registry.py`'s analytics pipeline path via
  `DATA_ANALYSIS`) was left in place — only the visualize path was
  re-wired, since that's the one with a live, user-facing gap. The
  analytics pipeline may have the same class of bug; not audited in this
  pass.
