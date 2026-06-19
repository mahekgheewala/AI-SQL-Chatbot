from db.schema_fetcher import format_schema_for_prompt


def build_schema_context(
    current_db: str,
    current_table: str,
    available_dbs: list[str],
    raw_schema: dict,
    session: dict | None = None,          # Phase 6: structured session memory
) -> str:
    """
    Assembles the full dynamic context string sent to Gemini.

    Phase 6 augmentation: when a session dict is provided, a CURRENT SESSION
    MEMORY block is prepended to the existing schema context.  All previous
    behaviour (database, table, schema formatting) is preserved exactly.
    """

    # ── Phase 6 — Session Memory Block (prepended) ────────────────────────────
    session_block = ""
    if session:
        recent_dbs  = session.get("recent_databases") or []
        recent_tbls = session.get("recent_tables") or []
        recent_ops  = session.get("recent_operations") or []
        last_intent = session.get("last_successful_intent") or "None"
        mem_db      = session.get("selected_database") or "None"
        mem_tbl     = session.get("selected_table") or "None"

        session_block = (
            "CURRENT SESSION MEMORY\n"
            "----------------------\n"
            f"Selected Database: {mem_db}\n"
            f"Selected Table: {mem_tbl}\n"
            f"Recent Databases: {recent_dbs}\n"
            f"Recent Tables: {recent_tbls}\n"
            f"Recent Operations: {recent_ops}\n"
            f"Last Successful Intent: {last_intent}\n"
            "----------------------\n\n"
        )

    # ── Phase 3 — Schema / DB context (unchanged) ─────────────────────────────
    schema_details = format_schema_for_prompt(raw_schema) if raw_schema else "None"

    schema_block = (
        f"CURRENT DATABASE: {current_db or 'None'}\n"
        f"CURRENT TABLE: {current_table or 'None'}\n"
        f"AVAILABLE DATABASES: {', '.join(available_dbs) if available_dbs else 'None'}\n"
        f"DATABASE SCHEMA:\n{schema_details}\n"
    )

    return session_block + schema_block

