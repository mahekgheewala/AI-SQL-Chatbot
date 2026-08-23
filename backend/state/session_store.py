"""
Phase 6: Session Context Memory Store (Multi-User Refactored)
"""
from typing import Optional, Dict, Any
from abc import ABC, abstractmethod
from collections import defaultdict
from threading import RLock
from utils.logging_config import user_id_var, session_id_var

try:
    from utils.logging_config import logger_audit
except ImportError:
    import logging
    logger_audit = logging.getLogger("app.audit")

_MAX_RECENT = 10

def _default_session() -> dict:
    return {
        "selected_database": None,
        "selected_table": None,
        "recent_databases": [],
        "recent_tables": [],
        "recent_operations": [],
        "last_successful_intent": None,
        "pending_clarification": None,
        "message_count": 0,
        "last_classification": None,
        "last_user_intent": None,
        "last_execution_intent": None,
        "last_processing_pipeline": None,
        "last_visualization": None,
        "last_visualization_settings": {},
        "pending_operation": None,
        "last_query": None,
        "entity_resolution": None,
        "planner_state": None,
    }

class SessionStore(ABC):
    @abstractmethod
    def get_session(self, user_id: int, session_id: str) -> dict:
        pass
        
    @abstractmethod
    def update_session(self, user_id: int, session_id: str, **kwargs) -> None:
        pass
        
    @abstractmethod
    def clear_table(self, user_id: int, session_id: str, table_name: str) -> None:
        pass
        
    @abstractmethod
    def rename_table(self, user_id: int, session_id: str, old_name: str, new_name: str) -> None:
        pass
        
    @abstractmethod
    def clear_database(self, user_id: int, session_id: str, db_name: str, db_tables: Optional[list[str]] = None) -> None:
        pass

class MemorySessionStore(SessionStore):
    def __init__(self):
        self._sessions: Dict[int, Dict[str, Any]] = defaultdict(dict)
        self._lock = RLock()

    def get_session(self, user_id: int, session_id: str) -> dict:
        if not session_id:
            return _default_session()
        with self._lock:
            user_sessions = self._sessions[user_id]
            if session_id not in user_sessions:
                user_sessions[session_id] = _default_session()
            return user_sessions[session_id]

    def update_session(self, user_id: int, session_id: str, **kwargs) -> None:
        if not session_id:
            return
        with self._lock:
            session = self.get_session(user_id, session_id)

            selected_database = kwargs.get("selected_database")
            selected_table = kwargs.get("selected_table")
            add_operation = kwargs.get("add_operation")

            if selected_database is not None:
                old_db = session.get("selected_database")
                session["selected_database"] = selected_database
                if selected_database not in session["recent_databases"]:
                    session["recent_databases"].append(selected_database)
                    session["recent_databases"] = session["recent_databases"][-_MAX_RECENT:]

                if old_db != selected_database:
                    from analytics.engine import AnalyticsEngine
                    AnalyticsEngine.invalidate_source_cache(session_id, reason="DATABASE_SWITCH", new_db=selected_database, user_id=user_id)

            if selected_table is not None:
                session["selected_table"] = selected_table
                if selected_table not in session["recent_tables"]:
                    session["recent_tables"].append(selected_table)
                    session["recent_tables"] = session["recent_tables"][-_MAX_RECENT:]

            if add_operation is not None:
                session["recent_operations"].append(add_operation)
                session["recent_operations"] = session["recent_operations"][-_MAX_RECENT:]
                if add_operation != "SELECT":
                    from analytics.engine import AnalyticsEngine
                    reason = "DDL_OPERATION" if add_operation in {"CREATE", "ALTER", "DROP"} else "DML_OPERATION"
                    AnalyticsEngine.invalidate_source_cache(session_id, reason=reason, user_id=user_id)

            for key in ["last_successful_intent", "last_user_intent", "last_execution_intent",
                       "last_processing_pipeline", "last_analysis_type", "last_visualization",
                       "last_visualization_settings", "last_query", "pending_operation",
                       "entity_resolution", "planner_state"]:
                if key in kwargs and kwargs[key] is not None:
                    session[key] = kwargs[key]

    def clear_table(self, user_id: int, session_id: str, table_name: str) -> None:
        if not session_id or not table_name:
            return
        with self._lock:
            session = self.get_session(user_id, session_id)
            table_lower = table_name.lower()
            if session.get("selected_table") and session["selected_table"].lower() == table_lower:
                session["selected_table"] = None
            session["recent_tables"] = [t for t in session.get("recent_tables", []) if t.lower() != table_lower]

    def rename_table(self, user_id: int, session_id: str, old_name: str, new_name: str) -> None:
        if not session_id or not old_name or not new_name:
            return
        with self._lock:
            session = self.get_session(user_id, session_id)
            old_lower = old_name.lower()
            if session.get("selected_table") and session["selected_table"].lower() == old_lower:
                session["selected_table"] = new_name
            session["recent_tables"] = [(new_name if t.lower() == old_lower else t) for t in session.get("recent_tables", [])]

    def clear_database(self, user_id: int, session_id: str, db_name: str, db_tables: Optional[list[str]] = None) -> None:
        if not session_id or not db_name:
            return
        with self._lock:
            session = self.get_session(user_id, session_id)
            db_lower = db_name.lower()
            current_db = session.get("selected_database")
            if current_db and current_db.lower() == db_lower:
                session["selected_database"] = None
                session["selected_table"] = None
                if db_tables:
                    db_tables_lower = {t.lower() for t in db_tables}
                    session["recent_tables"] = [t for t in session.get("recent_tables", []) if t.lower() not in db_tables_lower and t.split(".")[-1].lower() not in db_tables_lower]
                session["recent_databases"] = [d for d in session.get("recent_databases", []) if d.lower() != db_lower]

    def clear_user(self, user_id: int) -> None:
        """Remove all in-memory conversation state for a user (logout / reset)."""
        with self._lock:
            self._sessions.pop(user_id, None)

    def active_session_count(self) -> int:
        """Total number of active in-memory chat sessions across all users."""
        with self._lock:
            return sum(len(sessions) for sessions in self._sessions.values())


store = MemorySessionStore()

# Backward-compatible alias: `_sessions` was historically the module-level backing dict.
# It now aliases the singleton's multi-user store ({ user_id: { session_id: session } }).
_sessions = store._sessions

def get_session(session_id: Optional[str]) -> dict:
    user_id = user_id_var.get()
    return store.get_session(user_id, session_id)

def update_session(session_id: Optional[str], **kwargs) -> None:
    user_id = user_id_var.get()
    store.update_session(user_id, session_id, **kwargs)

def clear_session_table(session_id: Optional[str], table_name: str) -> None:
    user_id = user_id_var.get()
    store.clear_table(user_id, session_id, table_name)

def rename_session_table(session_id: Optional[str], old_name: str, new_name: str) -> None:
    user_id = user_id_var.get()
    store.rename_table(user_id, session_id, old_name, new_name)

def clear_session_database(session_id: Optional[str], db_name: str, db_tables: Optional[list[str]] = None) -> None:
    user_id = user_id_var.get()
    store.clear_database(user_id, session_id, db_name, db_tables)

def log_session_state(session_id: str) -> None:
    pass

class ConversationState:
    def __init__(self, session_id: Optional[str]):
        self.user_id = user_id_var.get()
        self.session_id = session_id
        self.session = get_session(session_id)

    @property
    def current_database(self) -> Optional[str]:
        return self.session.get("selected_database")

    @current_database.setter
    def current_database(self, val: Optional[str]):
        update_session(self.session_id, selected_database=val)

    @property
    def current_table(self) -> Optional[str]:
        return self.session.get("selected_table")

    @current_table.setter
    def current_table(self, val: Optional[str]):
        update_session(self.session_id, selected_table=val)

    @property
    def pending_operation(self) -> Optional[dict]:
        return self.session.get("pending_operation")

    @pending_operation.setter
    def pending_operation(self, val: Optional[dict]):
        self.session["pending_operation"] = val

    @property
    def last_visualization(self) -> Optional[dict]:
        return self.session.get("last_visualization")

    @last_visualization.setter
    def last_visualization(self, val: Optional[dict]):
        update_session(self.session_id, last_visualization=val)

    @property
    def last_query(self) -> Optional[str]:
        return self.session.get("last_query")

    @last_query.setter
    def last_query(self, val: Optional[str]):
        self.session["last_query"] = val

    @property
    def entity_resolution(self) -> Optional[dict]:
        return self.session.get("entity_resolution")

    @entity_resolution.setter
    def entity_resolution(self, val: Optional[dict]):
        self.session["entity_resolution"] = val

    @property
    def planner_state(self) -> Optional[dict]:
        return self.session.get("planner_state")

    @planner_state.setter
    def planner_state(self, val: Optional[dict]):
        self.session["planner_state"] = val
