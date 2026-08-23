"""
sql_detector.py — Phase 10.4.x
================================
Distinguishes Raw SQL statements from Natural Language instructions
before the deterministic dispatcher routes the request.

Architecture:
    User Message
         │
         ▼
    Gate 1: Natural Language Indicators (always runs first)
         │
         ├── NL Indicators found?  → Natural Language (no further checks)
         │
         ▼
    Gate 2: Strict SQL Grammar Check
         │
         ├── Valid SQL opening?    → Raw SQL
         │
         └── No match             → Natural Language

Semicolons and multi-line formatting are SUPPORTING SIGNALS ONLY.
They are never the sole deciding factor. A sentence like
"Please create a table called employees;" remains Natural Language
because Gate 1 detects the conversational phrase before Gate 2 runs.
"""

import re
from typing import Tuple

# ─── Gate 1: Natural Language Indicators ────────────────────────────────────
# If ANY of these patterns match, the message is Natural Language regardless
# of what keywords appear at the start.
# Ordered from most-specific to least-specific to keep the first-match reason
# descriptive.

_NL_INDICATORS: list[tuple[re.Pattern, str]] = [
    # Polite / request phrases
    (re.compile(r"\b(please|could you|can you|would you|i want|i need|i'd like)\b", re.IGNORECASE),
     "Contains polite/request phrase"),

    # Question words at the start
    (re.compile(r"^\s*(what|how|why|which|where|when|who|is there|are there|does|do)\b", re.IGNORECASE),
     "Starts with question word"),

    # Conversational verbs ("show me", "tell me", "give me", "let me see")
    (re.compile(r"\b(show me|tell me|give me|let me see|help me)\b", re.IGNORECASE),
     "Contains conversational verb phrase"),

    # Indefinite articles before nouns — strong NL signal
    # e.g. "create a table", "create an employees table", "add a column"
    (re.compile(
        r"\b(create|make|build|add|insert|drop|delete|remove|alter|rename|modify)\s+(a|an|the|new|some|this|my)\b",
        re.IGNORECASE),
     "Contains article after action verb (natural-language DDL)"),

    # "called", "named", "with name" after any word — dead giveaway for NL
    (re.compile(r"\b(called|named|with\s+(?:the\s+)?name)\b", re.IGNORECASE),
     "Contains naming phrase ('called', 'named', 'with name')"),

    # Conversational object references
    # e.g. "new database", "new table", "a salary column"
    (re.compile(r"\bnew\s+(database|table|column|db|index|view)\b", re.IGNORECASE),
     "Contains 'new <object>' phrase"),

    # Instructional verbs not valid as SQL openers
    (re.compile(r"^\s*(generate|plot|chart|graph|report|summarize|analyze|export|describe|explain)\b", re.IGNORECASE),
     "Starts with instructional verb (not a SQL keyword)"),

    # Adverbs / qualifiers that signal prose
    (re.compile(r"\b(quickly|immediately|automatically|without|using|based on|for each|for all)\b", re.IGNORECASE),
     "Contains prose adverb or qualifier"),
]


# ─── Gate 2: Strict SQL Grammar Patterns ─────────────────────────────────────
# These match the precise 2–3 token opening of valid SQL statements.
# A bare "CREATE" or "ALTER" alone never matches — the second token must be
# a valid SQL object type (TABLE, DATABASE, INDEX, VIEW, etc.).

_SQL_GRAMMAR_PATTERNS: list[tuple[re.Pattern, str]] = [
    # SELECT — must be followed by *, column name, expression, or subquery
    (re.compile(r"^\s*SELECT\s+(\*|[\w\"\[`']|DISTINCT\b|TOP\b|ALL\b)", re.IGNORECASE),
     "SELECT statement"),

    # INSERT INTO
    (re.compile(r"^\s*INSERT\s+INTO\s+[\w\"\[`']", re.IGNORECASE),
     "INSERT INTO statement"),

    # UPDATE table SET
    (re.compile(r"^\s*UPDATE\s+[\w\"\[`']+\s+SET\b", re.IGNORECASE),
     "UPDATE ... SET statement"),

    # DELETE FROM
    (re.compile(r"^\s*DELETE\s+FROM\s+[\w\"\[`']", re.IGNORECASE),
     "DELETE FROM statement"),

    # TRUNCATE TABLE
    (re.compile(r"^\s*TRUNCATE\s+(TABLE\s+)?[\w\"\[`']", re.IGNORECASE),
     "TRUNCATE TABLE statement"),

    # CREATE TABLE / DATABASE / INDEX / VIEW / SEQUENCE / TYPE / SCHEMA
    (re.compile(
        r"^\s*CREATE\s+(OR\s+REPLACE\s+)?"
        r"(TABLE|DATABASE|INDEX|UNIQUE\s+INDEX|VIEW|SEQUENCE|SCHEMA|TYPE|EXTENSION)\s+[\w\"\[`']",
        re.IGNORECASE),
     "CREATE <object> statement"),

    # ALTER TABLE / DATABASE / SEQUENCE / TYPE
    (re.compile(
        r"^\s*ALTER\s+(TABLE|DATABASE|SEQUENCE|TYPE|INDEX)\s+[\w\"\[`']",
        re.IGNORECASE),
     "ALTER <object> statement"),

    # DROP TABLE / DATABASE / INDEX / VIEW / SEQUENCE / TYPE / SCHEMA
    (re.compile(
        r"^\s*DROP\s+(TABLE|DATABASE|INDEX|VIEW|SEQUENCE|SCHEMA|TYPE|EXTENSION)"
        r"(\s+IF\s+EXISTS)?\s+[\w\"\[`']",
        re.IGNORECASE),
     "DROP <object> statement"),

    # GRANT / REVOKE
    (re.compile(r"^\s*(GRANT|REVOKE)\s+\w+", re.IGNORECASE),
     "GRANT/REVOKE statement"),

    # WITH ... AS ( — CTE opener
    (re.compile(r"^\s*WITH\s+[\w\"\[`']+\s+AS\s*\(", re.IGNORECASE),
     "CTE (WITH ... AS) statement"),
]


def is_raw_sql(message: str) -> Tuple[bool, str]:
    """
    Classify a user message as Raw SQL or Natural Language.

    Returns:
        (True,  reason_str) — executable SQL; skip the Planner.
        (False, reason_str) — natural language; route through the Planner.

    Gate priority:
        1. Natural Language Indicators — always wins if matched.
        2. Strict SQL Grammar check    — decides if NL indicators not found.
    """
    stripped = message.strip()

    # ── Gate 1: Natural Language Indicators ───────────────────────────────────
    for pattern, reason in _NL_INDICATORS:
        if pattern.search(stripped):
            return False, reason

    # ── Gate 2: Strict SQL Grammar Check ─────────────────────────────────────
    for pattern, reason in _SQL_GRAMMAR_PATTERNS:
        if pattern.match(stripped):
            return True, f"Matches SQL grammar: {reason}"

    # ── Fallback: unrecognised → treat as Natural Language ───────────────────
    return False, "No SQL grammar pattern matched — treated as natural language"


def print_sql_detector_log(message: str, is_sql: bool, reason: str) -> None:
    """
    Print a structured SQL DETECTOR log block to stdout.

    Example output:
        ====================================
        SQL DETECTOR
        ====================================
        Message:
          Create a new database called company_analytics
        Detected Type:
          Natural Language
        Reason:
          Contains naming phrase ('called', 'named', 'with name')
        Planner Required:
          YES
        ====================================
    """
    detected_type = "RAW SQL" if is_sql else "Natural Language"
    planner_required = "NO" if is_sql else "YES"

    print("\n====================================")
    print("SQL DETECTOR")
    print("====================================")
    print(f"Message:\n  {message.strip()}")
    print(f"\nDetected Type:\n  {detected_type}")
    print(f"\nReason:\n  {reason}")
    print(f"\nPlanner Required:\n  {planner_required}")
    print("====================================\n")
