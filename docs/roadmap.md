# Roadmap

## Done

Phases 1–9 (core assistant, validation, execution, session memory, AI
routing, logging), Phase 9.5 (auth/multi-tenancy/admin dashboard), Phase 10.4
(automatic chart generation), and Phase 10.5 (universal entity resolution).
See `README.md` → Architecture for the module breakdown.

## Not done

The original Phase 10 spec (`phases_content.txt`, `sql_phases/Phase 10.docx`)
called for:

- CSV / Excel / PDF export of query results (client-side, via Papa Parse /
  SheetJS / jsPDF)
- AI-generated Markdown reports over query results (new Gemini-backed
  endpoint)
- Pagination, sorting, and column filtering on `ResultsTable.jsx`
- A manual database-selector override in the UI

None of this was built — development went toward the agent/entity-resolution
architecture instead (Phase 10.4/10.5). These remain open if the export/
reporting features are still wanted.

## Known rough edges

- No CI; tests are run manually (`pytest tests/ -q` in `backend/`).
- `datetime.utcnow()` is used in a few places (`auth/jwt.py`,
  `repositories/chat_session_repository.py`, `utils/logging_config.py`) and
  is deprecated as of Python 3.12 — should move to timezone-aware
  `datetime.now(datetime.UTC)`.
