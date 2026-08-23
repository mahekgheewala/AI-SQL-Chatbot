"""
Phase 1 — Typo Preprocessing Layer (Python Refactor)
=====================================================

This module is the FIRST preprocessor that receives every user message.
It performs lightweight spelling correction, punctuation normalisation,
and whitespace normalisation. It no longer performs AI inference or
intent detection (these responsibilities have moved to the Planner).

Responsibilities
----------------
1. Spell-check / typo correction via pyspellchecker, RapidFuzz & edit distance
2. Punctuation normalisation
3. Whitespace normalisation
4. Request correlation ID generation and propagation
5. Structured, fully-correlated logging for every preprocessing step

Public API
----------
    from agent.typo_intent_layer import process

    cleaned_message, intent_meta = process(raw_message)

    # cleaned_message : str  — normalised text forwarded to AgentCoordinator
    # intent_meta     : dict — expanded metadata (see IntentMeta structure below)

IntentMeta structure
--------------------
    {
        "request_id"        : str,    # unique per-request UUID4 short hex
        "original_message"  : str,    # raw user message exactly as received
        "cleaned_message"   : str,    # normalised message (same as return value)
        "correction_applied": bool,   # True if any correction was made
        "model_name"        : str,    # "python-preprocessor"
        "processing_time_ms": float,  # wall-clock ms for the whole layer
        "fallback"          : bool,   # False (always runs locally in Python)
        "timestamp"         : str,    # ISO-8601 UTC timestamp at start of call
        "libraries_used"    : list,   # ["pyspellchecker", "rapidfuzz", "regex"]
    }

Request Correlation ID
----------------------
A unique ``request_id`` is generated once per call to ``process()``.
It is stored in the ``request_id_var`` ContextVar from logging_config so
every downstream logger automatically includes it in every log entry.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from spellchecker import SpellChecker
from rapidfuzz import process as fuzzy_process, fuzz

# ---------------------------------------------------------------------------
# Logger (child of app.ai — inherits file handlers from setup_logging())
# ---------------------------------------------------------------------------
logger = logging.getLogger("app.ai.typo_intent")
_PREFIX = "[TYPO_INTENT]"

# Initialize the spell checker locally
_spell = SpellChecker()


def _deelongate(word: str) -> str:
    """Collapse runs of 3+ identical characters to a single one.

    Handles keyboard-repeat typos (e.g. "emploooooyee", "databseeee")
    that fall outside pyspellchecker's edit-distance-2 correction and
    RapidFuzz's ratio-based cutoff. A run length of 3 is the threshold
    because legitimate English words never repeat a letter 3+ times in
    a row, so this never mangles a correctly spelled word.
    """
    return re.sub(r"(.)\1{2,}", r"\1", word)

# Common direct typo mapping for SQL/database grammar words (fast path).
# Language/SQL-syntax constants only — no business vocabulary here. Typos of
# a *connected schema's* real table/column names are corrected separately in
# _correct_text via schema-driven fuzzy matching (see _get_system_identifiers).
_DIRECT_TYPO_MAP = {
    "creat": "create",
    "shw": "show",
    "selct": "select",
    "slect": "select",
    "frm": "from",
    "databse": "database",
    "tabels": "tables",
    "tabel": "table",
    "lis": "list",
    "al": "all",
    "col": "column",
    "cols": "columns",
    "dbase": "database",
    "dbases": "databases",
}

# SQL grammar/type keywords — the language itself, not any particular
# database's business vocabulary. Kept static on the same footing as Python
# keywords would be; real table/column names are pulled live from the
# connected schema instead (see _get_system_identifiers below).
_SQL_KEYWORDS = {
    "select", "insert", "update", "delete", "create", "alter", "drop", "use", "show", "describe", "explain", "list", "add",
    "table", "database", "databases", "tables", "column", "columns",
    "schema", "schemas", "index", "indexes", "view", "views",
    "join", "joins", "inner", "left", "right", "outer", "full", "cross",
    "where", "group", "by", "having", "order", "limit", "offset",
    "primary", "foreign", "key", "keys", "unique", "check", "default",
    "constraint", "constraints", "references", "on", "cascade",
    "null", "not", "values", "into", "set", "from", "as", "and", "or",
    "count", "sum", "avg", "min", "max", "average", "total", "mean",
    "int", "integer", "varchar", "text", "boolean", "bool", "float", "numeric", "decimal", "timestamp", "date", "time", "serial", "bigint", "smallint", "char",
}

# Words already meaningful in the assistant's own grammar (action verbs,
# chart-type nouns, greetings, ...) — pulled from semantic_frame's own
# closed word sets rather than duplicated, so the two never drift apart.
def _assistant_vocabulary() -> frozenset[str]:
    from agent.semantic_frame import ACTION_WORDS, CHART_WORDS, GREETINGS
    return frozenset(ACTION_WORDS | CHART_WORDS | GREETINGS)


_ASSISTANT_VOCABULARY = _assistant_vocabulary()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_request_id() -> str:
    """Generate a short unique request ID (8-character hex) for log correlation."""
    return uuid.uuid4().hex[:8]


def _utc_now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _log_prefix(request_id: str) -> str:
    """Build the consistent console log prefix including the request ID."""
    return f"{_PREFIX}[{request_id}]"


def _get_system_identifiers() -> set[str]:
    """Fetch all registered database, table, and column names from
    metadata_store — the live, schema-driven vocabulary this layer corrects
    typos towards, in place of a fixed per-domain word list."""
    identifiers = set()
    try:
        from state.metadata_store import get_metadata
        meta = get_metadata()
        summaries = meta.get("routing_summaries", {})
        for db_name, tables in summaries.items():
            identifiers.add(db_name.lower())
            for tbl in tables:
                identifiers.add(tbl.lower())
        schema = meta.get("schema", {})
        for tbl, cols in schema.items():
            identifiers.add(tbl.lower())
            for col in cols:
                identifiers.add(col.lower())
    except Exception:
        pass
    return identifiers


def _correct_text(text: str) -> str:
    """Perform whitespace, punctuation normalisation and custom spelling correction."""
    # 1. Whitespace normalization
    cleaned = re.sub(r"\s+", " ", text).strip()
    
    # 2. Punctuation normalization
    cleaned = re.sub(r",(?!\s)", ", ", cleaned)
    cleaned = re.sub(r"([!?.,])\1+", r"\1", cleaned)
    
    # 3. Spelling correction
    words = re.findall(r"\b[a-zA-Z0-9_]+\b", cleaned)
    corrected_map = {}
    system_identifiers = _get_system_identifiers()
    # Fuzzy-correction target pool: the SQL language itself, plus whatever
    # tables/columns actually exist on the connected database — never a
    # fixed business-word list, so this generalizes to any schema.
    fuzzy_pool = _SQL_KEYWORDS | system_identifiers

    for word in words:
        word_lower = word.lower()

        # Absolute Metadata Protection: Skip if word is a known system identifier or SQL keyword
        if word_lower in _SQL_KEYWORDS or word_lower in system_identifiers:
            continue

        # Skip words that are already meaningful in the assistant's own
        # grammar (action verbs, chart-type nouns, ...) — otherwise a
        # legitimate word like "chart" gets "corrected" to the SQL type
        # keyword "char" (single-letter edit distance, well within the
        # fuzzy threshold), silently breaking every visualization request.
        if word_lower in _ASSISTANT_VOCABULARY:
            continue

        # Conservative Guard: For unknown tokens containing '_' or digits, typo correction is conservative
        # and must never replace the token solely because it resembles an English dictionary word.
        if "_" in word_lower or any(char.isdigit() for char in word_lower):
            continue

        # Strategy A: Direct typo mapping (covers common SQL-grammar typos)
        if word_lower in _DIRECT_TYPO_MAP:
            corrected_map[word] = _DIRECT_TYPO_MAP[word_lower]
            continue

        # Strategy B: Find the closest SQL keyword or real schema identifier
        # using RapidFuzz WITH FULL STRING RATIO (fuzz.ratio). Try the raw
        # word first, then a de-elongated form (collapsing repeated-letter
        # typos like "databseeee") since that can push the ratio back
        # above the cutoff.
        candidates = [word_lower]
        deelongated = _deelongate(word_lower)
        if deelongated != word_lower:
            candidates.append(deelongated)

        fuzzy_match = None
        for candidate in candidates:
            fuzzy_match = fuzzy_process.extractOne(candidate, fuzzy_pool, scorer=fuzz.ratio, score_cutoff=85)
            if fuzzy_match:
                break
        if fuzzy_match:
            corrected_map[word] = fuzzy_match[0]
            continue

        # Strategy C: Fallback spell check WITH MANDATORY SIMILARITY RATIO FLOOR (>= 85)
        for candidate in candidates:
            # The de-elongated form may already be a correctly spelled word
            # on its own (e.g. "emploooooyee" -> "employee") — no further
            # spellchecker correction needed in that case.
            if candidate != word_lower and len(candidate) > 1 and not _spell.unknown([candidate]):
                corrected_map[word] = candidate
                break

            is_unknown = len(candidate) > 1 and bool(_spell.unknown([candidate]))
            if not is_unknown:
                continue
            corrected = _spell.correction(candidate)
            if corrected and corrected.lower() != word_lower:
                if fuzz.ratio(candidate, corrected.lower()) >= 85:
                    corrected_map[word] = corrected
                    break
            
    # Apply replacements preserving casing
    result = cleaned
    for orig, corr in corrected_map.items():
        if orig.isupper():
            corr = corr.upper()
        elif orig.istitle():
            corr = corr.title()
        result = re.sub(r"\b" + re.escape(orig) + r"\b", corr, result)
        
    return result


# ---------------------------------------------------------------------------
# Core public function
# ---------------------------------------------------------------------------

def process(raw_message: str) -> tuple[str, dict]:
    """
    Run the Python Typo Preprocessor on a raw user message.

    Parameters
    ----------
    raw_message : str
        The unmodified user message exactly as received by the FastAPI endpoint.

    Returns
    -------
    (cleaned_message, intent_meta)
        cleaned_message : str  — normalised message forwarded to AgentCoordinator
        intent_meta     : dict — full metadata dict (see module docstring)
    """
    request_id = _new_request_id()
    prefix = _log_prefix(request_id)
    timestamp = _utc_now_iso()
    phase_start = time.perf_counter()

    # Bind request_id ContextVar
    try:
        from utils.logging_config import request_id_var
        _rid_token = request_id_var.set(request_id)
    except Exception:
        _rid_token = None

    # Log incoming message
    _log_info(
        prefix=prefix,
        request_id=request_id,
        message=f"Incoming user message received",
        operation_type="TYPO_INCOMING",
        extra={
            "original_message": raw_message,
            "timestamp": timestamp,
        },
        console_lines=[
            "",
            f"{prefix} ===================================",
            f"{prefix} REQUEST ID : {request_id}",
            f"{prefix} TIMESTAMP  : {timestamp}",
            f"{prefix} Incoming Message:",
            f'{prefix}   "{raw_message}"',
            f"{prefix} ===================================",
        ],
    )

    try:
        # Correct message using pure Python preprocessor
        cleaned_message = _correct_text(raw_message)
        correction_applied = cleaned_message != raw_message

        # Log correction results
        if correction_applied:
            _log_info(
                prefix=prefix,
                request_id=request_id,
                message="Typo correction applied",
                operation_type="TYPO_CORRECTION",
                extra={
                    "original_message": raw_message,
                    "cleaned_message": cleaned_message,
                    "correction_applied": True,
                },
                console_lines=[
                    f"{prefix} Typo Correction Applied:",
                    f'{prefix}   Original : "{raw_message}"',
                    f'{prefix}   Corrected: "{cleaned_message}"',
                ],
            )
        else:
            _log_info(
                prefix=prefix,
                request_id=request_id,
                message="No typo corrections applied",
                operation_type="TYPO_NO_CORRECTION",
                extra={
                    "original_message": raw_message,
                    "cleaned_message": raw_message,
                    "correction_applied": False,
                },
                console_lines=[
                    f"{prefix} Typo Correction: No corrections applied.",
                ],
            )

        phase_end = time.perf_counter()
        total_ms = round((phase_end - phase_start) * 1000, 2)

        # Build expanded metadata
        intent_meta = {
            "request_id": request_id,
            "raw_input": raw_message,
            "original_message": raw_message,
            "normalized_input": raw_message.strip(),
            "corrected_input": cleaned_message if correction_applied else None,
            "cleaned_message": cleaned_message,
            "correction_applied": correction_applied,
            "model_name": "python-preprocessor",
            "processing_time_ms": total_ms,
            "fallback": False,
            "timestamp": timestamp,
            "libraries_used": ["pyspellchecker", "rapidfuzz", "regex"],
        }

        # Log final summary
        _log_info(
            prefix=prefix,
            request_id=request_id,
            message="Preprocessing complete — forwarding to AgentCoordinator",
            operation_type="TYPO_COMPLETE",
            extra={
                "original_message": raw_message,
                "cleaned_message": cleaned_message,
                "correction_applied": correction_applied,
                "model_name": "python-preprocessor",
                "processing_time_ms": total_ms,
                "fallback": False,
                "timestamp": timestamp,
                "libraries_used": ["pyspellchecker", "rapidfuzz", "regex"],
            },
            console_lines=[
                f"{prefix} -----------------------------------",
                f"{prefix} Forwarding to AgentCoordinator:",
                f'{prefix}   Message : "{cleaned_message}"',
                f"{prefix}   Corrected: {correction_applied}",
                f"{prefix} Performance:",
                f"{prefix}   Duration : {total_ms:.1f}ms",
                f"{prefix} ===================================",
                "",
            ],
        )

        return cleaned_message, intent_meta

    except Exception as exc:
        phase_end = time.perf_counter()
        total_ms = round((phase_end - phase_start) * 1000, 2)
        
        intent_meta = {
            "request_id": request_id,
            "original_message": raw_message,
            "cleaned_message": raw_message,
            "correction_applied": False,
            "model_name": "python-preprocessor",
            "processing_time_ms": total_ms,
            "fallback": True,
            "timestamp": timestamp,
            "libraries_used": ["regex"],
        }
        
        try:
            logger.warning(
                f"{prefix} Preprocessor exception encountered: {str(exc)}",
                exc_info=True,
                extra={
                    "category": "ai",
                    "operation_type": "TYPO_ERROR",
                    "request_id": request_id,
                    "original_message": raw_message,
                }
            )
        except Exception:
            pass

        return raw_message, intent_meta

    finally:
        if _rid_token is not None:
            try:
                from utils.logging_config import request_id_var
                request_id_var.reset(_rid_token)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Internal structured log helper
# ---------------------------------------------------------------------------

def _log_info(
    prefix: str,
    request_id: str,
    message: str,
    operation_type: str,
    extra: dict,
    console_lines: list[str],
) -> None:
    """Emit one structured INFO log entry and write console lines."""
    for line in console_lines:
        if line:
            try:
                logger.info(line)
            except Exception:
                pass

    try:
        logger.info(
            message,
            extra={
                "category": "ai",
                "operation_type": operation_type,
                "request_id": request_id,
                **extra,
            }
        )
    except Exception:
        pass
