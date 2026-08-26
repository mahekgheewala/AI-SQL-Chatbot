# Errors and Solutions

Significant bugs found during the 2026-08-25 full-project audit, with root
cause and fix rationale. Kept separate from `git log` because the *why*
behind these took real investigation and is worth preserving — the commit
messages will have the *what*.

---

## 1. SQL statement-stacking bypassed the confirmation flow (critical, security)

**What happened:** Risk classification (`validation/safety_checker.py`) and
the HIGH_RISK/CRITICAL_RISK confirmation flow (`routers/chat.py`) both
looked only at the *leading* SQL statement's keyword/intent. Nothing checked
whether the SQL string contained a second, `;`-separated statement.

**Where:** `validation/safety_checker.py::check_safety`, exploited via
`routers/chat.py`'s confirm-and-execute path (`/execute-confirmed`) and
`db/executor.py`'s `cursor.execute(sql)` (psycopg2's simple-query protocol
happily runs multiple statements in one call).

**Root cause:** A statement like
`UPDATE employees SET salary=salary WHERE 1=1; DROP TABLE employees;`
classifies as intent `UPDATE` → `HIGH_RISK` → requires confirmation. The
user sees a confirmation prompt for what looks like a no-op UPDATE, clicks
confirm, and the *entire two-statement string* — including the unconfirmed
`DROP TABLE` — executes together in one transaction. `/execute-confirmed`
re-validates the SQL but only checked `valid`/`BLOCKED`, not statement
count.

**Fix:** Added a Gate-1 check in `check_safety()` that strips string
literals and comments, splits on `;`, and rejects (BLOCKED) anything with
more than one non-empty statement — for every intent, not just DDL (the
pre-existing `validation/schema_creator_validator.py` comment-injection
guard only covered `CREATE_*`/`ADD_COLUMN`/etc.).

**Why this fix works:** It's a structural check independent of intent
classification, so it can't be bypassed by choosing a SAFE-looking leading
statement. String literals and comments are stripped first so a semicolon
inside a quoted value (`WHERE name = 'a;b'`) or a `--` comment doesn't
false-positive.

**Prevention:** No legitimate single-turn request in this app's design ever
needs more than one SQL statement — the deterministic SQL builder, the LLM
prompt, and every capability all produce exactly one. If a future feature
genuinely needs multiple statements, it should execute them as separate,
separately-validated calls, not as one concatenated string.

---

## 2. `backend/.env` (real DB password + Gemini keys) was committed and pushed to GitHub

**What happened:** `backend/.env` — with a real Postgres password and two
live Gemini API keys — was committed at `7a3c056` ("Phase 9 checkpoint") and
later removed from *tracking* in `ad7e9ff`, but never purged from *history*.
That commit was already the tip of `origin/main` on GitHub — i.e., publicly
retrievable (`git show 7a3c056:backend/.env`) the entire time, even after
the file itself stopped being tracked going forward.

**Where:** git history, not the working tree — `.gitignore` was correctly
excluding `.env` going forward, which is what made this easy to miss (the
working tree and `git status` looked clean).

**Root cause:** `.env` was committed before `.gitignore` existed for it.
Removing it from tracking in a later commit does not remove it from history
— every prior commit's tree still contains the blob.

**Fix:** `git-filter-repo --path backend/.env --invert-paths --force` to
rewrite every commit and drop the file entirely, then `git push --force` to
overwrite `origin/main` with the rewritten history. Verified via
`git log --all --diff-filter=A --name-only | grep backend/.env` (zero
matches) and `git ls-remote origin` (only one ref, pointing at the
rewritten tip).

**Why this fix works:** `git-filter-repo` rewrites every commit's tree, not
just adds a revert commit — the blob is gone from every version, not just
the latest. Force-pushing overwrites the only remote ref, so GitHub's
default view of the repo no longer has any path to the leaked commit
(though GitHub may cache the orphaned commit object for a period before
GC — see prevention note).

**Prevention / residual risk:** Rotate the leaked credentials
(`DB_PASSWORD`, both `GEMINI_*_API_KEY` values) regardless of the history
purge — anyone who cloned the repo before the rewrite still has them, and
GitHub's object cache can retain a force-pushed-over commit for some time.
**This was flagged to the user but rotation is a manual step outside this
codebase (Postgres admin, Google AI Studio) — confirm it was actually
done.** Never commit `.env` in the first place; a pre-commit hook checking
for `.env`/`*.pem`/etc. would have caught this at the source.

---

## 3. Follow-up questions silently switched to the wrong table

**What happened:** `"show me all employees"` → `"only show those with
salary above 110000"` executed against the `salaries` table instead of
continuing on `employees`, even though `employees` has its own `salary`
column.

**Where:** `agent/semantic_frame.py::_parse_retrieve`, feeding
`agent/context_resolution.py::resolve_references`.

**Root cause (two layers):**

1. `_parse_retrieve`'s table-detection loop tried `ground_table()` on
   *every* token in the message, including "salary" — which
   `utils/entity_resolver.py`'s alias-generation stage matches to the
   `salaries` table via automatic singular/plural pairing (`salary` ↔
   `salaries`). This locked `frame.table = "salaries"` before the sentence
   was even understood as a filter clause.
2. Table grounding happens inside `_build_frame`, which runs *before*
   `context_resolution.resolve_references()` — the function that would
   otherwise fall back to the previous turn's table when the current
   message doesn't name one. Since step 1 had already (wrongly) set
   `frame.table`, that fallback never triggered — it only fires when
   `frame.table` is still `None`.

**Fix:**
- Added `_filter_reference_words()` — extracts words used as the
  referenced attribute in a comparative/filter clause ("salary above
  110000") or `_parse_filters()`'s own column-group patterns, and excludes
  them from the table-grounding loop. A word being used as a filter
  attribute is never also the table name in the same clause.
- Threaded a `context_table_hint` (the previous turn's `frame.table`) from
  `interpret_message()` down into `_build_frame` → `_parse_retrieve`, used
  *only* to resolve attribute-word ambiguity when the current message
  doesn't name its own table (e.g. "salary" matching a column on two
  different tables) — not to override an explicitly-named table.

**Why this fix works:** It fixes the actual ambiguity at its source (a word
can't be simultaneously "the filter column" and "the table") instead of
trying to patch it after the fact, and it gives the *existing* context-fallback
mechanism in `context_resolution.py` the chance to actually run instead of
being pre-empted.

**A follow-on bug found while fixing this:** with table-grounding correctly
suppressed, the *filter's own* attribute-word resolution (`resolve_attribute_word`)
then had no table to disambiguate against either (still running before
context resolution), and "salary" matching a column on *both* `employees`
and `salaries` produced a spurious "which column do you mean: id, salary?"
clarification. This is what `context_table_hint` fixes — it's passed into
`_parse_comparative_filters`/`resolve_attribute_word` too, not just used to
suppress the table loop.

---

## 4. Chart/visualization generation was fully built but completely disconnected

**What happened:** The README claimed "Phase 10.4 — Automated chart
generation: Complete," and the frontend fully implements chart rendering
(`PlotlyChart.jsx`), but a live chart request like `"show a bar chart of
total salary by department"` silently executed as a plain `SELECT` with no
chart returned. `ChatResponse.visualization` was always `null`.

**Where:** The break was in the dispatch layer between
`agent/agent_coordinator.py` and `agent/handlers.py`, not in the chart
engine itself (`visualization/engine.py`, `visualization/parser.py`,
`visualization/selection.py` — all correct and unit-tested in isolation via
`test_visualization_engine.py`, `test_phase10_5.py`).

**Root cause:** Two independent, disconnected intent-classification systems
coexist in this codebase (see `CLAUDE.md` for the general pattern). The
*live* pipeline (`semantic_frame.py` → `universal_gateway.py` →
`agent_coordinator.py`) correctly classified a chart request as
`capability_id="visualize"` with `legacy_intent="VISUALIZE"`. But
`agent/handlers.py`'s `_HANDLERS_REGISTRY` — which `routers/chat.py`
dispatches to via `get_handler(intent)` — only had a key for
`"DATA_VISUALIZATION"` (the *legacy* Gemini-router classification scheme's
vocabulary), not `"VISUALIZE"`. `get_handler("VISUALIZE")` silently fell
back to `UnknownHandler`, which hands the request to the generic planner —
which just answered the underlying data question as an ordinary query,
ignoring the chart framing entirely. No error, no log message pointing at
the mismatch — it looked like the chatbot had simply decided not to make a
chart.

Separately, even `agent_coordinator.py`'s own tool-dispatch map
(`_SEMANTIC_TOOL_MAP`) explicitly listed `"visualize": None` with a comment
saying it "falls through to the planner / visualization pipeline" — but
nothing in `agent_coordinator.run()` actually ever called the visualization
pipeline. The `VisualizationPipeline`/`get_pipeline("VISUALIZATION")`
machinery in `agent/pipeline_registry.py` was itself only reachable via the
same orphaned `DataVisualizationHandler`.

**Fix (reuses all existing, already-tested infrastructure — no new
architecture):**
1. `agent/semantic_frame.py`: `_parse_visualize` now grounds `measure` and
   `dimension` against the real schema (`G.ground_column`) instead of
   passing raw regex-matched words through ungrounded, and excludes them
   from table-candidacy the same way `_parse_retrieve` does (same
   singular/plural alias-conflation risk as bug #3 — "employees" in
   "chart of employees by department" must stay eligible as the *table*
   even though the "X by Y" regex slot calls it a "measure").
2. Added `frame_to_chart_query_intent()` — builds a `QueryIntent` for
   "measure aggregated by dimension" (or the bare measure column when
   there's no dimension), reusing the exact same `deterministic_sql_builder`
   already used for plain retrieve queries.
3. `agent/agent_coordinator.py`: `_SEMANTIC_TOOL_MAP["visualize"]` now maps
   to `"execute_sql"`, with a new `cap_id == "visualize"` branch in
   `_tool_from_frame` mirroring the existing `"retrieve"` branch — builds
   SQL from the chart's data requirements and dispatches through the same
   validated `execute_sql` tool.
4. `routers/chat.py`: after a successful execution, if
   `decision.semantic_frame.capability_id == "visualize"`, calls
   `VisualizationEngine.generate(processed_result, request.message,
   session_id)` (previously never invoked from the live path) and attaches
   `.model_dump()` to `ChatResponse.visualization`. Uses the *gateway's*
   frame (`decision.semantic_frame`), not `agent_result["intent"]` — the
   latter reflects the underlying SQL's shape (`"QUERY"` for a `SELECT`),
   not the original chart request, so it's not a reliable "was this a
   chart request" signal downstream.

**Verified:** `"show a bar chart of total salary by department"` now
executes `SELECT department, SUM(salary) FROM salaries GROUP BY
department` and returns a complete Plotly chart spec (title, axes, styled
bar trace) in `ChatResponse.visualization`.

**Lesson:** A capability existing in `agent/capabilities.py`, having a
`legacy_intent` string, and having a fully-built, unit-tested handler
somewhere in the codebase does not mean it's reachable from a live request.
The dispatch *key* has to actually match at every hop. See `CLAUDE.md`'s
"Two parallel understanding systems" section before adding a new capability.

---

## 5. Admin dashboard's API client never sent the JWT

**What happened:** Every admin dashboard API call (`getLogs`, `getAnalytics`,
`getHealth`, export) would 401 for an actual logged-in admin user.

**Where:** `frontend/src/services/adminApi.js`.

**Root cause:** `adminApi.js` was written when the admin endpoints didn't
require authentication ("dev mode", per its own now-stale comment and a
matching stale comment in `AdminDashboard.jsx`). At some point
`routers/admin.py` was locked down behind `get_current_admin` (a real,
correctly-implemented role check verified during this audit — see
`CLAUDE.md`'s security notes), but `adminApi.js` was never updated to
attach the JWT. The route guard (`ProtectedRoute` + role check in
`App.jsx`) correctly let an admin user *reach* the page — so the bug was
invisible from routing/navigation and only showed up as every API call on
the page failing.

**Fix:** Added the same request interceptor (`Authorization: Bearer
<token>` from `localStorage`) and 401-triggered silent-refresh response
interceptor that `services/api.js` already had, mirrored exactly for
consistency.

**Prevention:** When a backend endpoint's auth requirement changes, grep
the frontend for every client that calls it — `services/api.js` and
`services/adminApi.js` are separate axios instances and don't share
interceptors by default.

---

## 6. `/forgot-password` had no rate limiting

**What happened:** Unlike `/login` (which has failure-count-based lockout
via `auth/rate_limit.py`), `/forgot-password` could be called unlimited
times for any email address — an email-bombing vector.

**Where:** `routers/auth.py::forgot_password`.

**Root cause:** `auth/rate_limit.py`'s `is_rate_limited`/`record_failure`
pattern is failure-count-based, designed around login's clear
success/failure signal. `/forgot-password` intentionally always returns the
same generic message (by design, to avoid account enumeration) regardless
of whether the email exists — so there was no "failure" event to hook the
existing pattern onto, and it was simply never wired up.

**Fix:** Every call to `/forgot-password` now records a "failure" against a
`forgot_password:<email>` identifier (a separate namespace from login's, so
it doesn't interact with that lockout) regardless of outcome. Once rate
limited, the actual `auth_service.forgot_password()` call (which sends the
email) is silently skipped — but the response body stays identical either
way, so rate-limiting itself doesn't become a new account-enumeration
signal.

---

## 7. Windows dev-server restarts silently ran stale code (process/tooling issue, not app code)

**What happened:** During this session, a fix to `semantic_frame.py` was
verified working in an isolated unit test, but the *same* live HTTP request
kept showing the old broken behavior after a server "restart."

**Root cause:** `pkill -f uvicorn` from Git Bash did not actually kill the
previous `uvicorn` process (PID-numbering mismatch between Git Bash and
Windows). The next `uvicorn --port 8001` failed to bind
(`WinError 10048`) and was silently backgrounded as a dead process, while
the *old* server kept serving on the same port. `curl` against that port
returned `200` either way, so nothing about the restart looked wrong.

**Fix:** Use `netstat -ano | grep LISTENING` to check who actually holds
the port, and PowerShell's `Get-NetTCPConnection`/`Stop-Process -Force` to
reliably kill it, before trusting any restart. See `CLAUDE.md`.

**Not an application bug** — recorded here because it cost real debugging
time and will recur for the next person restarting the dev server on
Windows via Git Bash.
