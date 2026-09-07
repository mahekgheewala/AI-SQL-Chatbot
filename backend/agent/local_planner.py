"""
Phase 2 — Local Planner (Qwen 3) — Planner v1.0.0
==================================================

This module implements the Local Planner using Qwen 3 via Ollama.

Execution Flow:
  The Planning Document is the single source of planning truth.

  Planner (this module)
    ↓
  Planning Document (versioned JSON)
    ↓
  Gemini Executor  ←  interprets ONLY the Planning Document, never re-plans
    ↓                  from the raw user request.
  Tool Dispatcher
    ↓
  Execution

Design Principles (from approved architecture):
  - The Planner is strictly decoupled from execution.
  - It outputs a versioned Planning Document (JSON) and NOTHING else.
  - It NEVER calls tools, generates SQL, or makes execution decisions.
  - Each planning run goes through three sequential reasoning stages:
      1. Repository Analysis  — which codebase modules are relevant
      2. Database Context     — which schema objects / tables are involved
      3. Plan Generation      — abstract, technology-independent steps

  - Planning Mode is selected from exactly 6 options:
      Database Planning | Codebase Planning | General Reasoning
      Conversation | Clarification | Explanation

  - Domain separation:
      Database Planning  -> populate `database_context`,  leave `repository_context` null
      Codebase Planning  -> populate `repository_context`, leave `database_context` null
      All others         -> both null

  - PLANNER_VERSION tracks the evolution of the Planner itself, independently
    of the project migration phase.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
import urllib.request
import urllib.error
from typing import Optional
from utils.logging_config import request_id_var
from agent import gemini_metrics

logger = logging.getLogger("app.ai.local_planner")
_PREFIX = "[LOCAL_PLANNER]"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

# Refinement 1: Planner model defaults to openai/gpt-oss-20b via Groq API.
# Configure via GROQ_MODEL or LOCAL_PLANNER_MODEL.
_MODEL_NAME: str = os.getenv("GROQ_MODEL", os.getenv("LOCAL_PLANNER_MODEL", "openai/gpt-oss-20b"))

_REQUEST_TIMEOUT: int = int(os.getenv("LOCAL_PLANNER_TIMEOUT", "30"))  # seconds

# Schema version identifier
SCHEMA_VERSION = "1.0"
# Refinement 2: PLANNER_VERSION tracks Planner evolution independently of migration phases.
PLANNER_VERSION = "1.0.0"

# Valid planning modes (authoritative list)
VALID_PLANNING_MODES = {
    "Database Planning",
    "Codebase Planning",
    "General Reasoning",
    "Conversation",
    "Clarification",
    "Explanation",
}

# Required keys in the planning_document block
# Refinements 4 & 5: planner_confidence and estimated_execution_type are required.
REQUIRED_PLANNING_DOC_KEYS = {
    "planning_mode",
    "user_intent",
    "planning_required",
    "complexity",
    "goal",
    "repository_context",
    "database_context",
    "execution_steps",
    "warnings",
    "assumptions",
    "clarification_required",
    "clarification_question",
    "planner_confidence",
    "estimated_execution_type",
}

# Valid execution types for estimated_execution_type (Refinement 5)
VALID_EXECUTION_TYPES = {
    "sql_query",
    "database_management",
    "clarification",
    "explanation",
    "codebase_analysis",
    "general_reasoning",
}

# Valid user intents detected by the Planner
VALID_USER_INTENTS = {
    "Database Query",
    "Schema Inspection",
    "DDL Operation",
    "DML Operation",
    "Analytical Query",
    "Visualization Request",
    "Bug Fix",
    "Feature Request",
    "Explanation",
    "Greeting",
    "General Question",
    "Unknown",
}


# ---------------------------------------------------------------------------
# Prompt Builder
# ---------------------------------------------------------------------------

def _build_planner_prompt(
    user_message: str,
    intent_meta: dict,
    schema_context: str,
    history: list,
) -> str:
    """
    Build the prompt for the Qwen 3 Local Planner.

    The prompt guides the model through three sequential reasoning stages:
      Stage 1: Repository Analysis
      Stage 2: Database Context Analysis
      Stage 3: Plan Generation

    The prompt explicitly enforces domain separation and the clarification matrix.
    """
    history_lines = []
    for msg in history[-6:]:
        if isinstance(msg, dict):
            role = "User" if msg.get("role") == "user" else "Assistant"
            text = msg.get("text", "")
        else:
            role = "User" if getattr(msg, "role", "user") == "user" else "Assistant"
            text = getattr(msg, "text", "")
        history_lines.append(f"  {role}: {text}")
    history_str = "\n".join(history_lines) if history_lines else "None"

    meta_str = json.dumps(intent_meta, indent=2)

    return f"""/no_think
You are the Local Planner for an AI SQL Assistant.

YOUR ROLE IS PLANNING ONLY.
You MUST NOT:
  - Generate SQL queries or code.
  - Suggest tool names (do NOT mention execute_sql, get_schema_info, list_databases, create_table, etc.).
  - Generate tool arguments or execution parameters.
  - Make execution decisions or write final responses.
  - Perform any action — your job ends once you output the Planning Document.

Your output must be a SINGLE, VALID JSON object. Output JSON only — no commentary, no markdown, no explanation.

---
STAGE 1: REPOSITORY ANALYSIS
Determine which parts of the codebase are relevant to this request.
  - Only relevant for Codebase Planning mode.
  - For all other modes, set repository_context = null.

Repository modules available in this project (kept in sync with the
actual backend/ folder — do not assume a module exists beyond this list):
  - backend/agent/universal_gateway.py     (mandatory understanding + routing entry point)
  - backend/agent/semantic_frame.py        (NL understanding — builds the SemanticFrame)
  - backend/agent/agent_coordinator.py     (orchestration loop — tool dispatch)
  - backend/agent/tools.py                 (tool registry and tool functions)
  - backend/agent/deterministic_sql_builder.py (no-AI SQL construction for simple requests)
  - backend/agent/local_planner.py         (Local Planner — this module)
  - backend/agent/clarification_resolver.py (LLM-backed clarification-reply resolution)
  - backend/agent/pending_resolution.py    (pending clarification tracking/resolution)
  - backend/agent/handlers.py              (intent -> handler dispatch registry)
  - backend/agent/sql_detector.py          (raw SQL detection)
  - backend/agent/ddl_parser.py            (DDL statement parser)
  - backend/agent/typo_intent_layer.py     (Typo & Intent preprocessing)
  - backend/db/executor.py                 (database executor)
  - backend/routers/chat.py                (FastAPI chat router)
  - backend/state/metadata_store.py        (metadata cache)
  - backend/validation/safety_checker.py   (SQL safety/risk validation gate)
  - backend/validation/schema_checker.py   (SQL schema validation gate)
  - backend/visualization/engine.py        (chart generation)
  - backend/monitoring/metrics_store.py    (metrics collection)

---
STAGE 2: DATABASE CONTEXT ANALYSIS
Analyze which schema objects, tables, and columns are involved.
  - Only relevant for Database Planning mode.
  - For all other modes, set database_context = null.
  - Use the Active Database and Schema Info below to identify required_schema_objects and required_metadata_elements.
  - CRITICAL: You MUST ONLY select tables and columns that are EXPLICITLY listed in the Active Database and Schema Info below. NEVER invent or assume columns (e.g., do not assume employees.department exists if department is only in salaries).

---
STAGE 3: PLAN GENERATION
Produce abstract, technology-independent execution steps.
  - Steps must describe WHAT needs to be accomplished, not HOW to code or execute it.
  - Do NOT include tool names, SQL commands, database calls, or code snippets in steps.
  - GOOD:  "Locate salary and department fields in the active schema."
  - BAD:   "Call get_schema_info(), then execute SELECT AVG(salary) FROM employees."

---
PLANNING MODE SELECTION
Select exactly one planning_mode from this list:

  Database Planning   -> User wants to query, insert, update, delete, visualize, analyze, or modify databases/tables.
  Codebase Planning   -> User asks about project modules, refactoring, or internal architecture.
  Conversation        -> Greetings, casual chat, help requests, or questions about capabilities.
  Clarification       -> User is responding to a previous question or clarifying an earlier request.
  Explanation         -> User wants an explanation of a previous query result, SQL concept, or database behavior.
  General Reasoning   -> Open-ended analytical questions, logical reasoning, or knowledge questions.

---
DOMAIN SEPARATION RULE (MANDATORY):
  If planning_mode = "Database Planning":
    -> Set database_context with target_database, required_schema_objects, required_metadata_elements.
    -> Set repository_context = null.

  If planning_mode = "Codebase Planning":
    -> Set repository_context with involved_modules, architectural_layers.
    -> Set database_context = null.

  If planning_mode is anything else (Conversation, Clarification, Explanation, General Reasoning):
    -> Set BOTH repository_context = null AND database_context = null.

---
CLARIFICATION DECISION MATRIX
Set clarification_required = true AND provide a clarification_question ONLY in these situations:
  1. Incomplete CREATE TABLE: The user specified a table name but provided NO column names or data types.
  2. Ambiguous database target: A table operation is requested but no active database is set and the message does not name one.
  3. Missing table: The requested table does not exist in any available schema (does NOT apply when user is explicitly requesting to create a new database or new table via CREATE statements).
  4. Ambiguous table name: The same table name exists in multiple databases and it is unclear which to use.
  5. Destructive operation (DROP TABLE / DROP DATABASE) requested without prior confirmation state.
  6. Materially ambiguous metric/dimension/timeframe: the request has multiple reasonable, MATERIALLY DIFFERENT interpretations that would change which rows or columns the answer is based on (e.g. "top customers" — by total revenue? order count? average order value? "recent orders" — last day, week, month?). Ask which one is meant rather than silently picking one. This does NOT apply to minor phrasing choices that don't change the actual result (e.g. "customers" vs "clients" when only one table exists) — only when the choice would materially change the data returned.

FOREIGN-KEY JOIN GROUNDING & SINGLE-TABLE PREFERENCE:
- Multi-table JOIN operations may ONLY be planned if a verified Foreign Key relationship exists between those tables in ACTIVE DATABASE FOREIGN KEYS below.
- NEVER plan or suggest JOIN operations on non-foreign-key columns (such as coincidental column matches on salary, name, date, etc.).
- When a user query requests fields across multiple tables that lack a foreign key relationship (e.g. employee names in employees and department in salaries):
  1. DO NOT set clarification_required = true.
  2. Set clarification_required = false, planning_mode = "Database Planning", user_intent = "Database Query", and estimated_execution_type = "sql_query".
  3. Include ONLY the single table containing the primary filter criteria in database_context (e.g. salaries for department queries).
  4. Plan a single-table SELECT query on that table, and include an execution step noting that employee names cannot be linked to departments in this schema due to missing foreign key relationships.

When clarification_required = true:
  - Set clarification_question to a clear, specific question for the user.
  - Execution steps may be empty or minimal.
  - planning_required should still be true.
  - planning_mode should still reflect the request type.

---
COMPLEXITY LEVELS:
  Low       -> Single, straightforward action on known objects.
  Medium    -> 2-3 steps, multiple tables or joins.
  High      -> Complex multi-step operations, aggregations, schema changes.
  Very High -> Multiple databases, cross-schema operations, or significant schema modifications.

---
ESTIMATED EXECUTION TYPE (Refinement 5):
Set estimated_execution_type to the best match from this list:
  sql_query           -> User is querying, filtering, or aggregating data.
  database_management -> User is creating, dropping, or altering databases/tables.
  clarification       -> Clarification is required before any action.
  explanation         -> User wants an explanation of results or SQL concepts.
  codebase_analysis   -> User is asking about the codebase or architecture.
  general_reasoning   -> Any other reasoning or conversational response.

This is NOT tool selection. This is a high-level classification for the Gemini Executor.

---
USER INTENT DETECTION:
Classify the user message into EXACTLY ONE of the following intent labels under the "user_intent" field:
  - Database Query
  - Schema Inspection
  - DDL Operation
  - DML Operation
  - Analytical Query
  - Visualization Request
  - Bug Fix
  - Feature Request
  - Explanation
  - Greeting
  - General Question
  - Unknown

TABLE PHRASE DISAMBIGUATION RULES:
- Bare Table Requests (e.g. "show me the salaries table", "show salaries table", "view salaries table", "get salaries table", "show table salaries"):
  -> MUST be classified as user_intent = "Database Query" and estimated_execution_type = "sql_query".
  -> Plan a SELECT * FROM [table] query to retrieve the data rows.
- Explicit Structure Requests (e.g. "schema of salaries", "structure of salaries", "columns of salaries", "describe salaries table", "definition of salaries"):
  -> MUST be classified as user_intent = "Schema Inspection" and estimated_execution_type = "explanation".
- Low Confidence Ambiguity:
  -> If a bare request is ambiguous and planner_confidence < 0.6, set clarification_required = true with question: "Would you like to view the schema/columns of [table] or retrieve its data rows?"

---
PLANNER CONFIDENCE (Refinement 4):
Set planner_confidence to a float between 0.0 and 1.0 representing your confidence in
the correctness of this Planning Document given the available context.
  1.0 -> All required context is available and the plan is unambiguous.
  0.7 -> Minor assumptions were made but the plan is likely correct.
  0.5 -> Significant assumptions or missing context — clarification may help.
  0.3 -> Very uncertain — fallback reasoning applied.

---
OUTPUT JSON SCHEMA:
{{
  "schema_version": "1.0",
  "planner_metadata": {{
    "planner_version": "{PLANNER_VERSION}",
    "planner_model": "{_MODEL_NAME}",
    "planning_timestamp": "<UTC ISO timestamp>",
    "planning_duration_ms": 0.0
  }},
  "planning_document": {{
    "planning_mode": "<one of the 6 modes above>",
    "user_intent": "<one of the valid user intents above>",
    "planning_required": true or false,
    "complexity": "Low" | "Medium" | "High" | "Very High",
    "goal": "<A clear one-sentence description of the user objective>",
    "planner_confidence": <float 0.0 to 1.0>,
    "estimated_execution_type": "<one of the 6 execution types above>",
    "clarification_required": true or false,
    "clarification_question": "<specific question for user, or null>",
    "repository_context": {{
      "involved_modules": ["<module_path_1>", "<module_path_2>"],
      "architectural_layers": ["<layer_name>"]
    }} or null,
    "database_context": {{
      "target_database": "<database_name or null>",
      "required_schema_objects": ["<table_1>", "<table_2>"],
      "required_metadata_elements": ["<table.column_1>", "<table.column_2>"]
    }} or null,
    "execution_steps": [
      "<Abstract step 1 — technology-independent description of the goal>",
      "<Abstract step 2>"
    ],
    "warnings": [
      "<Any potential issues, missing objects, or dangerous operations>"
    ],
    "assumptions": [
      "<Any assumptions made>"
    ]
  }}
}}

---
ACTIVE DATABASE AND SCHEMA INFO:
{schema_context}

CONVERSATION HISTORY:
{history_str}

INTENT METADATA (from Typo & Intent Preprocessor):
{meta_str}

USER MESSAGE:
{user_message}
"""


# ---------------------------------------------------------------------------
# Groq API Call
# ---------------------------------------------------------------------------

def _call_groq_planner(prompt: str) -> tuple[str, float, dict]:
    """
    POST request to Groq API chat completions endpoint.
    Returns (response_text, elapsed_seconds, usage) where usage is
    {"prompt_tokens": int, "completion_tokens": int} (0s if the API
    response didn't include a usage block).
    """
    api_key = os.getenv("GROQ_API_KEY", _GROQ_API_KEY)
    if not api_key:
        raise ValueError("GROQ_API_KEY is missing or empty in environment.")

    model_name = os.getenv("GROQ_MODEL", os.getenv("LOCAL_PLANNER_MODEL", _MODEL_NAME))

    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = json.dumps({
        "model": model_name,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 1024
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "ai-sql-assistant/1.0"
        },
        method="POST",
    )

    t0 = time.perf_counter()
    raw = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                print(f"[LOCAL_PLANNER] Groq 429 Rate Limit encountered. Retrying in {2 ** (attempt + 1)}s...")
                time.sleep(2 ** (attempt + 1))
            else:
                raise
    elapsed = time.perf_counter() - t0

    data = json.loads(raw)
    choices = data.get("choices", [])
    if choices and isinstance(choices, list):
        message = choices[0].get("message", {})
        response_text = message.get("content", "")
    else:
        response_text = ""

    usage_raw = data.get("usage") or {}
    usage = {
        "prompt_tokens": int(usage_raw.get("prompt_tokens") or 0),
        "completion_tokens": int(usage_raw.get("completion_tokens") or 0),
    }
    return response_text, elapsed, usage


# ---------------------------------------------------------------------------
# Response Parser
# ---------------------------------------------------------------------------

def extract_json_object(raw_response: str) -> dict:
    """
    Extract a JSON object from a raw LLM response, tolerating the ways
    models commonly wrap or mangle it.

    Handles:
      - <think>...</think> reasoning blocks some models emit
      - Markdown code fences (```json ... ```)
      - Bare JSON objects within surrounding commentary text
      - JSON truncated before its final closing brace (auto-repair retry)

    Raises ValueError if no JSON object could be extracted/parsed.

    Shared by both AI call sites in this pipeline that expect a JSON
    response (the Local Planner's own response, parsed below, and the
    Gemini Executor's response in agent_coordinator.py) — previously only
    the Local Planner's parsing used this level of care; the Executor used
    a bare json.loads() with no tolerance for the same kinds of stray
    output, and would fail outright (generic "internal planning error")
    on exactly the input shapes this function already knows how to handle.
    """
    text = raw_response.strip()

    # Strip <think>...</think> reasoning blocks if present
    if "<think>" in text and "</think>" in text:
        think_end = text.find("</think>")
        if think_end != -1:
            text = text[think_end + len("</think>"):].strip()

    # Strip markdown code fences
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1].strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()

    # Extract JSON object by outermost braces
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in model response.")

    json_str = text[start:end]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        # Retry with auto-appended closing braces if LLM output was truncated before final brace
        fixed_str = json_str
        for _ in range(3):
            fixed_str += "}"
            try:
                return json.loads(fixed_str)
            except json.JSONDecodeError:
                continue
        raise


def _parse_planner_response(raw_response: str) -> dict:
    """
    Extract and validate the JSON planning document from the model's raw output.

    Enforces (on top of extract_json_object()'s tolerant extraction):
      - Required schema wrapper keys
      - Required planning_document keys
      - Valid planning_mode value (Refinement 3: invalid mode raises, does NOT silently normalize)
      - Domain separation (null context blocks for non-matching modes)
      - planner_confidence clamped to [0.0, 1.0] (Refinement 4)
      - estimated_execution_type normalized to valid value (Refinement 5)
    """
    wrapper = extract_json_object(raw_response)

    # Validate top-level wrapper keys
    if "schema_version" not in wrapper or "planning_document" not in wrapper:
        raise ValueError("Missing 'schema_version' or 'planning_document' wrapper keys.")

    plan_doc = wrapper["planning_document"]

    # Validate required planning_document keys
    missing_keys = REQUIRED_PLANNING_DOC_KEYS - set(plan_doc.keys())
    if missing_keys:
        raise ValueError(f"Missing required keys in planning_document: {missing_keys}")

    # Refinement 3: Invalid planning_mode is NOT silently normalized.
    # It raises ValueError so plan() triggers the fallback path with a visible warning.
    mode = plan_doc.get("planning_mode", "")
    if mode not in VALID_PLANNING_MODES:
        logger.warning(
            f"{_PREFIX} Planner returned unexpected planning_mode: '{mode}'. "
            f"Raw output preserved in logs. Triggering fallback plan.",
            extra={
                "category": "ai",
                "operation_type": "PLANNER_INVALID_MODE",
                "invalid_mode": mode,
                "raw_response_preview": raw_response[:500],
            }
        )
        raise ValueError(
            f"Invalid planning_mode '{mode}' returned by planner model. "
            f"Expected one of: {sorted(VALID_PLANNING_MODES)}"
        )

    # Refinement 4: Clamp planner_confidence to [0.0, 1.0]
    confidence = plan_doc.get("planner_confidence", 0.5)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    plan_doc["planner_confidence"] = round(max(0.0, min(1.0, confidence)), 4)

    # Refinement 5: Normalize estimated_execution_type
    exec_type = plan_doc.get("estimated_execution_type", "general_reasoning")
    if exec_type not in VALID_EXECUTION_TYPES:
        logger.warning(
            f"{_PREFIX} Unknown estimated_execution_type '{exec_type}' — defaulting to 'general_reasoning'",
            extra={"category": "ai", "operation_type": "PLANNER_VALIDATION"}
        )
        plan_doc["estimated_execution_type"] = "general_reasoning"

    # Validate and normalize user_intent
    u_intent = plan_doc.get("user_intent", "Unknown")
    if u_intent not in VALID_USER_INTENTS:
        logger.warning(
            f"{_PREFIX} Unknown user_intent '{u_intent}' — defaulting to 'Unknown'",
            extra={"category": "ai", "operation_type": "PLANNER_VALIDATION"}
        )
        plan_doc["user_intent"] = "Unknown"

    # Enforce domain separation post-parse
    if plan_doc["planning_mode"] != "Database Planning":
        plan_doc["database_context"] = None
    if plan_doc["planning_mode"] != "Codebase Planning":
        plan_doc["repository_context"] = None

    return wrapper


# ---------------------------------------------------------------------------
# Fallback Plan
# ---------------------------------------------------------------------------

def _get_fallback_plan(reason: str, request_id: str, start_time_ts: str, elapsed_ms: float) -> dict:
    """
    Build a safe fallback planning document when the Ollama call or parsing fails.

    The fallback signals to the Gemini Executor that it should proceed
    using safe defaults without a structured plan. The Planning Document
    remains the single source of truth even in fallback — the Gemini Executor
    must not re-plan from the raw user message.

    Refinement 3: Also used when an invalid planning_mode is detected — the
    fallback sets clarification_required = True so the user is informed.
    Refinement 4: planner_confidence = 0.0 in fallback (no confidence).
    Refinement 5: estimated_execution_type = 'clarification' in fallback.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "planner_metadata": {
            "planner_version": PLANNER_VERSION,
            "planner_model": _MODEL_NAME,
            "planning_timestamp": start_time_ts,
            "planning_duration_ms": round(elapsed_ms, 2)
        },
        "planning_document": {
            "planning_mode": "General Reasoning",
            "user_intent": "Unknown",
            "planning_required": False,
            "complexity": "Low",
            "goal": "Unresolved — Local Planner fallback activated",
            "planner_confidence": 0.0,
            "estimated_execution_type": "clarification",
            "repository_context": None,
            "database_context": None,
            "execution_steps": [
                "Graceful fallback activated — Gemini Executor should proceed using raw input and safety rules."
            ],
            "warnings": [
                f"Local Planner fallback triggered: {reason}."
            ],
            "assumptions": [],
            "clarification_required": True,
            "clarification_question": "I encountered an issue preparing your request. Could you rephrase or provide more details?"
        }
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def plan(
    user_message: str,
    intent_meta: dict,
    schema_context: str,
    history: list,
) -> dict:
    """
    Generate the Planning Document for a user message.

    Returns a versioned wrapper dict:
      {
        "schema_version": "1.0",
        "planner_metadata": { ... },
        "planning_document": { ... }
      }

    On any failure, returns a safe fallback plan (never raises).
    """
    request_id = request_id_var.get("unknown")
    start_time = time.perf_counter()
    start_time_ts = datetime.now(timezone.utc).isoformat()

    logger.info(
        f"{_PREFIX}[{request_id}] Planning phase starting",
        extra={
            "category": "ai",
            "operation_type": "PLANNER_START",
            "request_id": request_id,
            "user_message": user_message,
            "planner_version": PLANNER_VERSION,
            "planner_model": _MODEL_NAME,
        }
    )

    try:
        # Check session-level cache before calling Groq API.
        prompt = _build_planner_prompt(user_message, intent_meta, schema_context, history)
        raw_response, _elapsed, usage = _call_groq_planner(prompt)
        total_time_ms = (time.perf_counter() - start_time) * 1000

        gemini_metrics.record_call("local_planner")
        gemini_metrics.record_tokens(
            "local_planner",
            usage["prompt_tokens"],
            usage["completion_tokens"],
        )

        wrapper = _parse_planner_response(raw_response)

        # Inject authoritative metadata - overrides model-generated placeholders
        wrapper["planner_metadata"] = {
            "planner_version": PLANNER_VERSION,
            "planner_model": _MODEL_NAME,
            "planning_timestamp": start_time_ts,
            "planning_duration_ms": round(total_time_ms, 2)
        }

        planning_doc = wrapper["planning_document"]

        logger.info(
            f"{_PREFIX}[{request_id}] Planning phase complete",
            extra={
                "category": "ai",
                "operation_type": "PLANNER_COMPLETE",
                "request_id": request_id,
                "planning_mode": planning_doc.get("planning_mode"),
                "complexity": planning_doc.get("complexity"),
                "goal": planning_doc.get("goal"),
                "planner_confidence": planning_doc.get("planner_confidence"),
                "estimated_execution_type": planning_doc.get("estimated_execution_type"),
                "clarification_required": planning_doc.get("clarification_required"),
                "planning_duration_ms": round(total_time_ms, 2),
                "planning_doc": wrapper,
            }
        )

        return wrapper

    except Exception as e:
        total_time_ms = (time.perf_counter() - start_time) * 1000
        logger.warning(
            f"{_PREFIX}[{request_id}] Groq planner exception ({e}) — trying Gemini fallback planner...",
            extra={
                "category": "ai",
                "operation_type": "PLANNER_FALLBACK_ATTEMPT",
                "request_id": request_id,
                "reason": str(e),
                "planning_duration_ms": round(total_time_ms, 2),
            }
        )
        try:
            from ai.model_manager import PLANNER_MODEL, initialize_models
            from agent.gemini_retry import call_with_retry
            initialize_models()
            model_to_use = PLANNER_MODEL
            response = call_with_retry(lambda: model_to_use.generate_content(prompt), label="Planner Fallback")[0] if model_to_use else None
            raw_response = response.text if response else ""
            wrapper = _parse_planner_response(raw_response)
            wrapper["planner_metadata"] = {
                "planner_version": PLANNER_VERSION,
                "planner_model": "gemini-fallback",
                "planning_timestamp": start_time_ts,
                "planning_duration_ms": round(total_time_ms, 2)
            }
            return wrapper
        except Exception as fallback_err:
            logger.warning(f"{_PREFIX}[{request_id}] Gemini fallback failed: {fallback_err}")
            return _get_fallback_plan(str(e), request_id, start_time_ts, total_time_ms)

    except TimeoutError:
        total_time_ms = (time.perf_counter() - start_time) * 1000
        reason = "Groq API request timed out"
        logger.warning(
            f"{_PREFIX}[{request_id}] Fallback activated: {reason}",
            exc_info=True,
            extra={
                "category": "ai",
                "operation_type": "PLANNER_FALLBACK",
                "request_id": request_id,
                "reason": reason,
                "planning_duration_ms": round(total_time_ms, 2),
            }
        )
        return _get_fallback_plan(reason, request_id, start_time_ts, total_time_ms)

    except Exception as e:
        total_time_ms = (time.perf_counter() - start_time) * 1000
        reason = f"Unexpected error: {type(e).__name__} — {str(e)}"
        logger.warning(
            f"{_PREFIX}[{request_id}] Fallback activated: {reason}",
            exc_info=True,
            extra={
                "category": "ai",
                "operation_type": "PLANNER_FALLBACK",
                "request_id": request_id,
                "reason": reason,
                "planning_duration_ms": round(total_time_ms, 2),
            }
        )
        return _get_fallback_plan(reason, request_id, start_time_ts, total_time_ms)


local_plan = plan

