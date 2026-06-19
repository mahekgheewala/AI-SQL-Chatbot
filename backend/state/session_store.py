"""
Phase 6: Session Context Memory Store

Lightweight, RAM-only session manager for structured conversational context.

Each session is keyed by a UUID generated in the frontend (App.jsx).
All state is lost when the backend restarts — this is intentional.

This module is completely separate from metadata_store.py:
  - metadata_store.py → active backend schema cache (databases, tables, schema)
  - session_store.py  → transient conversational context per browser session
"""

from typing import Optional

# In-memory store: { session_id: { ...session fields... } }
_sessions: dict[str, dict] = {}

_MAX_RECENT = 10  # maximum items kept in any recent_* list


def _default_session() -> dict:
    return {
        "selected_database": None,
        "selected_table": None,
        "recent_databases": [],
        "recent_tables": [],
        "recent_operations": [],
        "last_successful_intent": None,
        "pending_clarification": None,
    }


def get_session(session_id: Optional[str]) -> dict:
    """
    Returns the session for the given ID.
    Creates a fresh default session if the ID is new or None.
    """
    if not session_id:
        return _default_session()

    if session_id not in _sessions:
        _sessions[session_id] = _default_session()

    return _sessions[session_id]


def update_session(
    session_id: Optional[str],
    *,
    selected_database: Optional[str] = None,
    selected_table: Optional[str] = None,
    add_operation: Optional[str] = None,
    last_successful_intent: Optional[str] = None,
) -> None:
    """
    Mutates the session in-place ONLY after a confirmed successful execution.

    Parameters:
        selected_database       — update the active database in context
        selected_table          — update the active table in context
        add_operation           — append to recent_operations list (e.g. "CREATE_TABLE")
        last_successful_intent  — e.g. "CREATE_DATABASE", "INSERT", "UPDATE"
    """
    if not session_id:
        return  # No session to update (anonymous request)

    session = get_session(session_id)

    log_lines = [
        "\n====================================",
        "SESSION MEMORY UPDATED",
        "====================",
    ]

    if selected_database is not None:
        session["selected_database"] = selected_database
        # Append to recent_databases if not already present
        if selected_database not in session["recent_databases"]:
            session["recent_databases"].append(selected_database)
            session["recent_databases"] = session["recent_databases"][-_MAX_RECENT:]
        log_lines.append(f"  selected_database -> {selected_database}")

    if selected_table is not None:
        session["selected_table"] = selected_table
        # Append to recent_tables if not already present
        if selected_table not in session["recent_tables"]:
            session["recent_tables"].append(selected_table)
            session["recent_tables"] = session["recent_tables"][-_MAX_RECENT:]
        log_lines.append(f"  selected_table -> {selected_table}")

    if add_operation is not None:
        session["recent_operations"].append(add_operation)
        session["recent_operations"] = session["recent_operations"][-_MAX_RECENT:]
        log_lines.append(f"  recent_operations appended -> {add_operation}")

    if last_successful_intent is not None:
        session["last_successful_intent"] = last_successful_intent
        log_lines.append(f"  last_successful_intent -> {last_successful_intent}")

    log_lines.append("====================================\n")
    print("\n".join(log_lines))


def log_session_state(session_id: Optional[str]) -> None:
    """
    Prints a structured Phase 6 debug log block to the Uvicorn console.
    Called before building the Gemini context.
    """
    session = get_session(session_id)

    display_id = session_id[:8] + "..." if session_id and len(session_id) > 8 else (session_id or "anonymous")

    print("\n====================================")
    print("PHASE 6 SESSION MEMORY")
    print("======================")
    print(f"Session ID:\n  {display_id}")
    print(f"Selected Database:\n  {session['selected_database'] or 'None'}")
    print(f"Selected Table:\n  {session['selected_table'] or 'None'}")
    print(f"Recent Databases:\n  {session['recent_databases'] or []}")
    print(f"Recent Tables:\n  {session['recent_tables'] or []}")
    print(f"Recent Operations:\n  {session['recent_operations'] or []}")
    print(f"Last Successful Intent:\n  {session['last_successful_intent'] or 'None'}")
    print("Building Enhanced Context...")
    print("Context Sent To Gemini.")
    print("====================================\n")


def clear_session_table(session_id: Optional[str], table_name: str) -> None:
    """Clear selected_table and remove from recent_tables if it matches the target table."""
    if not session_id or not table_name:
        return
    session = get_session(session_id)
    table_lower = table_name.lower()
    
    if session.get("selected_table") and session["selected_table"].lower() == table_lower:
        session["selected_table"] = None
        
    session["recent_tables"] = [
        t for t in session.get("recent_tables", [])
        if t.lower() != table_lower
    ]


def rename_session_table(session_id: Optional[str], old_name: str, new_name: str) -> None:
    """Rename selected_table and occurrences in recent_tables if it matches the target table."""
    if not session_id or not old_name or not new_name:
        return
    session = get_session(session_id)
    old_lower = old_name.lower()
    
    if session.get("selected_table") and session["selected_table"].lower() == old_lower:
        session["selected_table"] = new_name
        
    session["recent_tables"] = [
        (new_name if t.lower() == old_lower else t)
        for t in session.get("recent_tables", [])
    ]


def clear_session_database(
    session_id: Optional[str],
    db_name: str,
    db_tables: Optional[list[str]] = None,
) -> None:
    """
    Clear selected_database, selected_table, and related tables/database from history
    if the dropped database matches the session's currently selected database.
    """
    if not session_id or not db_name:
        return
    session = get_session(session_id)
    db_lower = db_name.lower()

    current_db = session.get("selected_database")
    if current_db and current_db.lower() == db_lower:
        session["selected_database"] = None
        session["selected_table"] = None

        # 3. remove tables associated with that database from recent_tables
        if db_tables:
            db_tables_lower = {t.lower() for t in db_tables}
            session["recent_tables"] = [
                t for t in session.get("recent_tables", [])
                if t.lower() not in db_tables_lower and t.split(".")[-1].lower() not in db_tables_lower
            ]

        # 4. remove database from recent_databases
        session["recent_databases"] = [
            d for d in session.get("recent_databases", [])
            if d.lower() != db_lower
        ]

