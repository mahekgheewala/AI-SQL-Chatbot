"""
Phase 7: AI Database Routing Layer

A lightweight Gemini wrapper responsible ONLY for selecting the correct
database before SQL generation begins.

Architecture:
- Reuses the existing _model from gemini_service.py (no second API client).
- Makes exactly ONE Gemini call per /chat request.
- Returns a database name string or None.
- Never generates SQL.
- Never validates safety.
- Never executes anything.

Separation of Responsibilities:
  Gemini SQL Generator  → generates SQL
  Phase 4 Validator     → checks safety
  Phase 5 Executor      → runs SQL
  Phase 7 Router        → decides WHICH database (this file)
"""

import json
from typing import Optional

import google.generativeai as genai

from ai.prompts import DATABASE_ROUTER_PROMPT

# ─── Guarantee genai.configure() has been called ─────────────────────────────
# gemini_service calls genai.configure(api_key=...) at module load time.
# Importing it here ensures the API key is registered before we create the
# router model instance below.
from ai import gemini_service  # noqa: F401 — side-effect import only

# Phase 8.1: shared retry helper
from agent.gemini_retry import call_with_retry

# Phase 8.5: Gemini call instrumentation
from agent import gemini_metrics

# ─── Lightweight routing model ────────────────────────────────────────────────
# Separate instance with its own system prompt so routing instructions never
# bleed into SQL generation.
_router_model = None

def reinit_router_model():
    global _router_model
    _router_model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=DATABASE_ROUTER_PROMPT,
        generation_config=genai.types.GenerationConfig(
            temperature=0.0,
            response_mime_type="application/json",
        ),
    )

# Initial initialization
reinit_router_model()


def _format_routing_summary(routing_summaries: dict[str, list[str]]) -> str:
    """Formats the routing summary dict into a readable string for the prompt."""
    if not routing_summaries:
        return "No databases available."
    lines = ["AVAILABLE DATABASES AND THEIR TABLES:"]
    for db_name, tables in routing_summaries.items():
        table_list = ", ".join(tables) if tables else "(no tables yet)"
        lines.append(f"  {db_name}: [{table_list}]")
    return "\n".join(lines)


def route_to_database(
    question: str,
    routing_summaries: dict[str, list[str]],
    session: dict,
    history: list | None = None,
) -> Optional[str]:
    """
    Makes ONE lightweight Gemini call to decide which database to connect to.

    Parameters:
        question          — the current user message
        routing_summaries — { db_name: [table1, table2, ...] } from metadata_store
        session           — Phase 6 session memory dict
        history           — conversation history (optional, for pronoun resolution)

    Returns:
        A valid database name string, or None if undetermined.
    """
    if not routing_summaries:
        print("\n[Phase 7] No routing summaries available — skipping router.")
        return None

    # Build the routing context
    summary_text = _format_routing_summary(routing_summaries)

    session_context = (
        f"SESSION MEMORY:\n"
        f"  Selected Database: {session.get('selected_database') or 'None'}\n"
        f"  Recent Databases: {session.get('recent_databases') or []}\n"
        f"  Last Successful Intent: {session.get('last_successful_intent') or 'None'}\n"
    )

    history_text = ""
    if history:
        history_lines = ["CONVERSATION HISTORY:"]
        for msg in history[-6:]:  # Last 6 messages for routing context
            if hasattr(msg, "role"):
                role = "User" if msg.role == "user" else "Assistant"
                text = msg.text
            else:
                role = "User" if msg.get("role") == "user" else "Assistant"
                text = msg.get("text", "")
            history_lines.append(f"  {role}: {text}")
        history_text = "\n".join(history_lines) + "\n\n"

    prompt = (
        f"{summary_text}\n\n"
        f"{session_context}\n"
        f"{history_text}"
        f"USER QUESTION: {question}"
    )

    try:
        # ── Phase 8.1: rate-limit retry wrapper ──────────────────────────────
        # Phase 8.5: record this Gemini call in the metrics tracker
        gemini_metrics.record_call("router")
        response, success, rate_limit_msg = call_with_retry(
            fn=lambda: _router_model.generate_content(prompt),
            label="Router",
        )

        if not success:
            print(f"\n[Phase 7] Router rate limit exhausted after retry — falling back to None.")
            return None

        if response and hasattr(response, "usage_metadata") and response.usage_metadata:
            gemini_metrics.record_tokens(
                "router",
                response.usage_metadata.prompt_token_count,
                response.usage_metadata.candidates_token_count
            )

        result  = json.loads(response.text)
        decided = result.get("database")

        # Security: validate the returned name is in the known summaries
        if decided and decided not in routing_summaries:
            print(f"\n[Phase 7] Router returned unknown database '{decided}' — discarding.")
            decided = None

        print("\n====================================")
        print("PHASE 7 DATABASE ROUTER")
        print("====================================")
        print(f"User Question:\n  {question}")
        print(f"Session Database:\n  {session.get('selected_database') or 'None'}")
        print(f"Available Databases:\n  {len(routing_summaries)}")
        print(f"Router Decision:\n  {decided or 'None (no confident match)'}")
        print("====================================\n")

        return decided

    except (json.JSONDecodeError, Exception) as e:
        print(f"\n[Phase 7] Router error: {e} — falling back to None.")
        return None

