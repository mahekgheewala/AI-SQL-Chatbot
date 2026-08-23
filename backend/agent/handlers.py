"""
Phase 3 — Intent Handlers (Refined)
===================================

Dynamic registry and modular handler implementation utilizing structured intent metadata.
Supports confidence bands and adaptive visualization routing.
"""

from __future__ import annotations

import logging
from typing import Optional, Dict, Any
from ai.model_manager import FORMATTER_MODEL, initialize_models
from agent.agent_coordinator import run as coordinator_run
from agent.tools import list_databases, list_tables, get_schema_info
from agent.pipeline_registry import get_pipeline

logger = logging.getLogger("app.ai.handlers")

class BaseHandler:
    def handle(
        self,
        message: str,
        metadata: Dict[str, Any],
        target_db: Optional[str],
        router_db: Optional[str],
        session: dict,
        session_id: Optional[str],
        history: list,
        decision: Optional[Any] = None,
    ) -> dict:
        raise NotImplementedError()


class ConversationHandler(BaseHandler):
    """Handles casual greetings, farewells, thanks, and health pings. Bypasses DB completely."""
    def handle(self, message: str, metadata: Dict[str, Any], *args, **kwargs) -> dict:
        initialize_models()
        prompt = (
            f"You are a friendly and professional AI database assistant. "
            f"Respond to this greeting, thanks, farewell, or casual chitchat politely: {message}"
        )
        response = FORMATTER_MODEL.generate_content(prompt)
        return {
            "reply": response.text.strip(),
            "intent": "GENERAL_CONVERSATION",
            "valid": True,
            "database": kwargs.get("target_db")
        }


class KnowledgeHandler(BaseHandler):
    """Answers database/SQL conceptual questions without executing queries."""
    def handle(self, message: str, metadata: Dict[str, Any], *args, **kwargs) -> dict:
        initialize_models()
        prompt = (
            f"You are an expert database educator. "
            f"Provide a clear, educational explanation of the SQL or database concept asked: {message}"
        )
        response = FORMATTER_MODEL.generate_content(prompt)
        return {
            "reply": response.text.strip(),
            "intent": "KNOWLEDGE",
            "valid": True,
            "database": kwargs.get("target_db")
        }


class MetadataHandler(BaseHandler):
    """Directly lists databases, tables, or switches active database without using SQL generation or Planner."""
    def handle(self, message: str, metadata: Dict[str, Any], target_db: Optional[str], router_db: Optional[str], session: dict, *args, **kwargs) -> dict:
        intent = metadata.get("intent", "").upper()
        
        if intent == "DATABASE_SWITCH":
            target_db_name = metadata.get("database") or target_db
            if target_db_name and session:
                session["selected_database"] = target_db_name
            return {
                "reply": f"Switched active database to **{target_db_name}**.",
                "intent": "DATABASE_SWITCH",
                "valid": True,
                "database": target_db_name,
                "refresh_databases": True
            }
        elif intent == "LIST_DATABASES" or "database" in message.lower() or "db" in message.lower():
            res = list_databases()
            dbs = res.get("databases", [])
            reply = f"Available databases: {', '.join(dbs)}." if dbs else "No databases found."
            return {
                "reply": reply,
                "intent": "LIST_DATABASES",
                "valid": True,
                "database": target_db,
                "refresh_databases": False
            }
        else:
            res = list_tables(target_db=target_db, session=session)
            tbls = res.get("tables", [])
            active = res.get("active_db", target_db or "selected database")
            reply = f"Tables in {active}: {', '.join(tbls)}." if tbls else f"No tables found in {active}."
            return {
                "reply": reply,
                "intent": "LIST_TABLES",
                "valid": True,
                "database": target_db,
                "refresh_tables": False
            }


class SchemaHandler(BaseHandler):
    """Retrieves schema columns and primary keys directly from cache."""
    def handle(self, message: str, metadata: Dict[str, Any], target_db: Optional[str], router_db: Optional[str], session: dict, *args, **kwargs) -> dict:
        res = get_schema_info(target_db=target_db, session=session)
        schema = res.get("schema", {})
        table_schemas = res.get("table_schemas", {})
        active_db = res.get("active_db", target_db or "")

        if schema:
            # Check if user specified a particular table name in the message
            matched_tbl = None
            for tbl in schema.keys():
                if tbl.lower() in message.lower():
                    matched_tbl = tbl
                    break

            if matched_tbl:
                cols = schema[matched_tbl]
                lines = [f"Schema for table **{matched_tbl}**:"]
                for col in cols:
                    col_type = table_schemas.get(matched_tbl, {}).get(col, "unknown")
                    lines.append(f"* {col} ({col_type})")
                reply = "\n".join(lines)
            else:
                lines = [f"Database schema for **{active_db}**:"]
                for tbl, cols in schema.items():
                    col_details = []
                    for col in cols:
                        col_type = table_schemas.get(tbl, {}).get(col)
                        if col_type:
                            col_details.append(f"{col} ({col_type})")
                        else:
                            col_details.append(col)
                    lines.append(f"- **{tbl}**: {', '.join(col_details)}")
                reply = "\n".join(lines)
        else:
            reply = "No schema information available."

        return {
            "reply": reply,
            "intent": "DESCRIBE_TABLE",
            "valid": True,
            "database": target_db
        }


class SQLRetrievalHandler(BaseHandler):
    """Executes query retrieval through mechanical SQL construction for DIRECT routes or LocalPlanner for AI_PLANNER routes."""
    def handle(self, message: str, metadata: Dict[str, Any], target_db: Optional[str], router_db: Optional[str], session: dict, session_id: Optional[str], history: list, decision: Optional[Any] = None) -> dict:
        route = metadata.get("route", "")
        
        if route == "DIRECT":
            from agent.deterministic_sql_builder import build_sql
            query_intent_data = metadata.get("query_intent")
            from models.schemas import QueryIntent
            
            query_intent = None
            if isinstance(query_intent_data, dict):
                query_intent = QueryIntent(**query_intent_data)
            elif isinstance(query_intent_data, QueryIntent):
                query_intent = query_intent_data
                
            target_table = metadata.get("target_table")
            active_db = target_db or router_db
            
            if query_intent:
                try:
                    mechanic_sql = build_sql(query_intent, target_table=target_table)
                    logger.info(f"[SQLRetrievalHandler] DIRECT route matched. Constructed SQL: '{mechanic_sql}' without AI calls.")
                    from agent.tools import execute_sql
                    return execute_sql(
                        instruction=mechanic_sql,
                        target_db=active_db,
                        session=session,
                        session_id=session_id,
                        history=history
                    )
                except Exception as err:
                    logger.error(f"[SQLRetrievalHandler] Deterministic SQL construction failed: {err}")
        
        logger.info(f"[SQLRetrievalHandler] AI_PLANNER route matched. Routing through Agent Coordinator and Groq Local Planner.")
        return coordinator_run(
            user_message=message,
            router_db=router_db,
            session=session,
            session_id=session_id,
            history=history,
            target_db=target_db,
            bypass_planner=False,
            decision=decision,
        )


class DatabaseModificationHandler(BaseHandler):
    """Executes schema creation/updates/deletions."""
    def handle(self, message: str, metadata: Dict[str, Any], target_db: Optional[str], router_db: Optional[str], session: dict, session_id: Optional[str], history: list, decision: Optional[Any] = None) -> dict:
        # Bypasses simple routing; always goes through the Planner for DDL safety checks
        return coordinator_run(
            user_message=message,
            router_db=router_db,
            session=session,
            session_id=session_id,
            history=history,
            target_db=target_db,
            bypass_planner=False,
            decision=decision,
        )


class DataVisualizationHandler(BaseHandler):
    """Routes queries to the adaptive visualization pipeline."""
    def handle(self, message: str, metadata: Dict[str, Any], target_db: Optional[str], router_db: Optional[str], session: dict, session_id: Optional[str], history: list, decision: Optional[Any] = None) -> dict:
        # Delegation to Visualization Engine (which automatically bypasses Planner for simple queries via deterministic raw select caching)
        pipeline = get_pipeline("VISUALIZATION")
        return pipeline.run(
            user_message=message,
            router_db=router_db,
            session=session,
            session_id=session_id,
            history=history,
            target_db=target_db,
            decision=decision,
        )


class DataAnalysisHandler(BaseHandler):
    """Routes queries to the analytics engine pipeline."""
    def handle(self, message: str, metadata: Dict[str, Any], target_db: Optional[str], router_db: Optional[str], session: dict, session_id: Optional[str], history: list, decision: Optional[Any] = None) -> dict:
        pipeline = get_pipeline("ANALYTICS_ENGINE")
        return pipeline.run(
            user_message=message,
            router_db=router_db,
            session=session,
            session_id=session_id,
            history=history,
            target_db=target_db,
            decision=decision,
        )


class MultiStepTaskHandler(BaseHandler):
    """Passes task directly to the local Planner reasoning loop."""
    def handle(self, message: str, metadata: Dict[str, Any], target_db: Optional[str], router_db: Optional[str], session: dict, session_id: Optional[str], history: list, decision: Optional[Any] = None) -> dict:
        return coordinator_run(
            user_message=message,
            router_db=router_db,
            session=session,
            session_id=session_id,
            history=history,
            target_db=target_db,
            bypass_planner=False,
            decision=decision,
        )


class AmbiguousHandler(BaseHandler):
    """Triggers prompt clarification."""
    def handle(self, message: str, metadata: Dict[str, Any], target_db: Optional[str], *args, **kwargs) -> dict:
        reply = "I'm not sure what database table or columns you are referring to. Could you provide more details?"
        return {
            "reply": reply,
            "intent": "NEEDS_CLARIFICATION",
            "question": reply,
            "valid": True,
            "database": target_db
        }


class UnknownHandler(BaseHandler):
    """Graceful fallback for unknown intents using the Planner's conversational recovery."""
    def handle(self, message: str, metadata: Dict[str, Any], target_db: Optional[str], router_db: Optional[str], session: dict, session_id: Optional[str], history: list, decision: Optional[Any] = None) -> dict:
        print("[UnknownHandler] Unknown intent matched. Falling back to Planner for recovery.")
        return coordinator_run(
            user_message=message,
            router_db=router_db,
            session=session,
            session_id=session_id,
            history=history,
            target_db=target_db,
            bypass_planner=False,
            decision=decision,
        )


class FindTableLocationHandler(BaseHandler):
    """Finds which database(s) contain a requested table or entity name using in-memory metadata."""
    def handle(self, message: str, metadata: Dict[str, Any], target_db: Optional[str], router_db: Optional[str], session: dict, *args, **kwargs) -> dict:
        import re
        from state.metadata_store import get_metadata, set_databases, refresh_routing_summaries
        from db.schema_fetcher import fetch_all_databases

        meta = get_metadata()
        routing = meta.get("routing_summaries", {})
        if not routing:
            try:
                set_databases(fetch_all_databases())
                refresh_routing_summaries()
                meta = get_metadata()
                routing = meta.get("routing_summaries", {})
            except Exception:
                pass

        msg_clean = message.lower()
        words_to_strip = {
            "which", "database", "databases", "contain", "contains", "containing",
            "have", "has", "does", "any", "where", "is", "are", "the", "a", "an", "find",
            "show", "data", "from", "table", "tables", "what", "tell", "me", "list", "do", "how", "many"
        }
        
        # 1. Look for word immediately preceding 'table' or 'tables'
        match_table_prec = re.search(r"\b([a-z0-9_]+)\s+tables?\b", msg_clean)
        if match_table_prec and match_table_prec.group(1) not in words_to_strip:
            target_token = match_table_prec.group(1)
        else:
            # 2. Fallback to first non-stripped token
            tokens = [w for w in re.findall(r"\b[a-z0-9_]+\b", msg_clean) if w not in words_to_strip]
            target_token = tokens[0] if tokens else ""

        if not target_token:
            return {
                "reply": "Which table are you looking for?",
                "intent": "FIND_TABLE_LOCATION",
                "valid": True,
                "database": target_db
            }

        target_singular = target_token[:-1] if target_token.endswith('s') and len(target_token) > 3 else target_token
        
        matches = []
        matching_tables_map = {}
        for db_name, tbl_list in routing.items():
            db_matches = []
            for t in tbl_list:
                t_lower = t.lower()
                t_singular = t_lower[:-1] if t_lower.endswith('s') and len(t_lower) > 3 else t_lower
                if target_token == t_lower or target_singular == t_singular or (len(target_token) >= 4 and target_token in t_lower):
                    db_matches.append(t)
            if db_matches:
                matches.append(db_name)
                matching_tables_map[db_name] = db_matches

        if matches:
            lines = [f"The table matching '{target_token}' was found in the following database(s):"]
            for db in matches:
                tbls_str = ", ".join(f"'{t}'" for t in matching_tables_map[db])
                lines.append(f"- **{db}** (table: {tbls_str})")
            reply = "\n".join(lines)
        else:
            reply = f"No database currently contains a table matching '{target_token}'."

        return {
            "reply": reply,
            "intent": "FIND_TABLE_LOCATION",
            "valid": True,
            "database": matches[0] if len(matches) == 1 else target_db
        }


# Dynamic Intent Registry
_HANDLERS_REGISTRY: Dict[str, BaseHandler] = {
    "GENERAL_CONVERSATION": ConversationHandler(),
    "KNOWLEDGE": KnowledgeHandler(),
    "DATABASE_ADMIN": MetadataHandler(),
    "LIST_DATABASES": MetadataHandler(),
    "LIST_TABLES": MetadataHandler(),
    "DATABASE_SWITCH": MetadataHandler(),
    "SCHEMA_EXPLORATION": SchemaHandler(),
    "DESCRIBE_TABLE": SchemaHandler(),
    "FIND_TABLE_LOCATION": FindTableLocationHandler(),
    "SQL_RETRIEVAL": SQLRetrievalHandler(),
    "RAW_SQL": SQLRetrievalHandler(),
    "DATABASE_MODIFICATION": DatabaseModificationHandler(),
    "SIMPLE_DDL": DatabaseModificationHandler(),
    "DATA_VISUALIZATION": DataVisualizationHandler(),
    "DATA_ANALYSIS": DataAnalysisHandler(),
    "MULTI_STEP_TASK": MultiStepTaskHandler(),
    "AMBIGUOUS": AmbiguousHandler(),
    "UNKNOWN": UnknownHandler()
}

def get_handler(intent: str) -> BaseHandler:
    """
    Returns the registered handler instance based on the intent category.
    """
    intent_upper = intent.upper()
    return _HANDLERS_REGISTRY.get(intent_upper, _HANDLERS_REGISTRY["UNKNOWN"])
