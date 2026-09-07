"""
Phase 4 — Safety Checker
=========================
Gate 1 of the validation pipeline.

Responsibilities:
  1. Scan the generated SQL for permanently-blocked PostgreSQL system operations.
  2. Promote TRUNCATE SQL to CRITICAL_RISK even when Gemini classifies it as DELETE.
  3. Map all valid intents to their authoritative risk level.

If any blocked pattern is detected, the entire validation pipeline stops here —
no further checkers run, and no user override is possible.

Risk Classification (Authoritative Source of Truth):
  SAFE         : QUERY, SHOW_DATABASES, SHOW_TABLES, DESCRIBE_TABLE,
                 CREATE_DATABASE, CREATE_TABLE, ADD_COLUMN, INSERT
  HIGH_RISK    : UPDATE, DELETE, DROP_COLUMN, MODIFY_COLUMN, RENAME_COLUMN,
                 RENAME_TABLE
  CRITICAL_RISK: DROP_TABLE, DROP_DATABASE, TRUNCATE
  BLOCKED      : ALTER SYSTEM, ALTER ROLE, COPY TO/FROM PROGRAM,
                 COPY TO FILE, pg_read_file, pg_write_file
"""

import re

# ── Risk Classification Sets ─────────────────────────────────────────────────

SAFE_INTENTS: set[str] = {
    "QUERY",
    "SHOW_DATABASES",
    "SHOW_TABLES",
    "DESCRIBE_TABLE",
    "CREATE_DATABASE",
    "CREATE_TABLE",
    "ADD_COLUMN",
    "INSERT",
}

HIGH_RISK_INTENTS: set[str] = {
    "UPDATE",
    "DELETE",
    "DROP_COLUMN",
    "MODIFY_COLUMN",
    "RENAME_COLUMN",   # Renamed column can silently break existing queries/app logic
    "RENAME_TABLE",
}

CRITICAL_RISK_INTENTS: set[str] = {
    "DROP_TABLE",
    "DROP_DATABASE",
    "TRUNCATE",
}

# ── Permanently Blocked SQL Patterns ─────────────────────────────────────────
# Each entry is (compiled_regex, human_readable_reason).
# Patterns are matched against the full SQL string before intent classification.

_BLOCKED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r'\bALTER\s+SYSTEM\b', re.IGNORECASE),
        "ALTER SYSTEM operations can modify the PostgreSQL server configuration and affect all databases.",
    ),
    (
        re.compile(r'\bALTER\s+ROLE\b', re.IGNORECASE),
        "ALTER ROLE operations modify database user privileges and authentication settings.",
    ),
    (
        re.compile(r'\bCOPY\b.+\bTO\b.+\bPROGRAM\b', re.IGNORECASE | re.DOTALL),
        "COPY TO PROGRAM executes arbitrary shell commands on the database server.",
    ),
    (
        re.compile(r'\bCOPY\b.+\bFROM\b.+\bPROGRAM\b', re.IGNORECASE | re.DOTALL),
        "COPY FROM PROGRAM executes arbitrary shell commands on the database server.",
    ),
    (
        re.compile(r'\bCOPY\b.+\bTO\b.+\bFILE\b', re.IGNORECASE | re.DOTALL),
        "COPY TO FILE can read or write arbitrary files on the database server filesystem.",
    ),
    (
        re.compile(r'\bpg_read_file\s*\(', re.IGNORECASE),
        "pg_read_file() can access arbitrary files on the database server filesystem.",
    ),
    (
        re.compile(r'\bpg_write_file\s*\(', re.IGNORECASE),
        "pg_write_file() can modify arbitrary files on the database server filesystem.",
    ),
]

# ── TRUNCATE Promotion Pattern ────────────────────────────────────────────────
# TRUNCATE is not a Gemini-defined intent, so it may arrive under a DELETE intent.
# We detect it in the SQL itself and promote the risk level to CRITICAL_RISK.
_TRUNCATE_PATTERN: re.Pattern = re.compile(r'\bTRUNCATE\b', re.IGNORECASE)

# ── Multi-Statement Detection ─────────────────────────────────────────────────
# Risk classification below (and the HIGH_RISK confirmation flow in chat.py)
# looks only at the leading statement's intent/keyword. A second `;`-separated
# statement would ride along at whatever risk level the first one earned and
# execute without its own confirmation (e.g. a confirmed UPDATE smuggling an
# unconfirmed DROP TABLE). String literals and comments are stripped first so
# a semicolon inside a quoted value or a comment doesn't cause a false positive.


def _strip_strings_and_comments(sql: str) -> str:
    """Single left-to-right pass that blanks out quoted-string contents and
    comments, tracking exactly one state at a time (normal code / inside a
    quoted string / inside a line comment / inside a block comment).

    Replaces a previous two-independent-regex-pass approach (strip quoted
    strings first, then separately strip comments) that could be tricked:
    a stray, unmatched quote character sitting inside one comment could
    pair up with another stray quote sitting inside a LATER, unrelated
    comment on a different line, and everything in between — including a
    real, executable second SQL statement and its semicolon — would get
    swallowed as if it were all one quoted string before comment-stripping
    or statement-counting ever ran, making a genuine second statement
    invisible to this check. A single-pass scanner that always knows which
    state it's in as it reads cannot make that mistake in either direction
    (a quote inside a real comment, or "--"/"/*" inside a real string, are
    both handled correctly, since only one state ever applies at a time).
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]

        if ch == "'":
            # Inside a single-quoted string: only a doubled '' (the SQL
            # escape for a literal quote) or the real closing quote ends
            # this state — a "--" or "/*" encountered in here is just data.
            out.append("'")
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        out.append("''")
                        i += 2
                        continue
                    out.append("'")
                    i += 1
                    break
                out.append(" ")
                i += 1
            continue

        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            i += 2
            while i < n and sql[i] != "\n":
                i += 1
            continue

        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            i += 2
            while i < n and not (sql[i] == "*" and i + 1 < n and sql[i + 1] == "/"):
                i += 1
            i += 2
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _has_multiple_statements(sql: str) -> bool:
    """True if `sql` contains more than one SQL statement."""
    stripped = _strip_strings_and_comments(sql)
    statements = [s.strip() for s in stripped.split(";")]
    return len([s for s in statements if s]) > 1


def check_safety(intent: str, sql: str | None) -> dict:
    """
    Gate 1: Scan for blocked SQL patterns and return the risk classification.

    Args:
        intent: The intent string returned by Gemini (e.g. "UPDATE", "QUERY").
        sql:    The raw SQL string generated by Gemini. May be None for
                conversational intents (SHOW_DATABASES returns SQL, but
                NEEDS_CLARIFICATION does not).

    Returns:
        {
            "risk_level": "SAFE" | "HIGH_RISK" | "CRITICAL_RISK" | "BLOCKED",
            "blocked_reason": str | None   (populated only when BLOCKED)
        }
    """

    # ── 0. Reject multi-statement SQL ──────────────────────────────────────
    #    No legitimate single-turn request needs more than one statement, and
    #    allowing one lets a second statement bypass its own risk/confirmation.
    if sql and _has_multiple_statements(sql):
        return {
            "risk_level": "BLOCKED",
            "blocked_reason": (
                "This request would execute multiple SQL statements in a single "
                "operation, which is not permitted for safety reasons."
            ),
        }

    # ── 1. Scan SQL for permanently-blocked patterns ──────────────────────────
    if sql:
        for pattern, reason in _BLOCKED_PATTERNS:
            if pattern.search(sql):
                return {
                    "risk_level": "BLOCKED",
                    "blocked_reason": reason,
                }

    # ── 2. Promote TRUNCATE SQL to CRITICAL_RISK ──────────────────────────────
    #    Even if Gemini says DELETE, TRUNCATE is always CRITICAL_RISK.
    if sql and _TRUNCATE_PATTERN.search(sql) and intent not in CRITICAL_RISK_INTENTS:
        return {
            "risk_level": "CRITICAL_RISK",
            "blocked_reason": None,
        }

    # ── 3. Classify by intent ─────────────────────────────────────────────────
    if intent in SAFE_INTENTS:
        return {"risk_level": "SAFE", "blocked_reason": None}

    if intent in HIGH_RISK_INTENTS:
        return {"risk_level": "HIGH_RISK", "blocked_reason": None}

    if intent in CRITICAL_RISK_INTENTS:
        return {"risk_level": "CRITICAL_RISK", "blocked_reason": None}

    # ── 4. Unknown/unclassified intents are BLOCKED by default ───────────────
    #    An intent Gemini returns that we have no classification for cannot be
    #    safely allowed. Block it conservatively.
    return {
        "risk_level": "BLOCKED",
        "blocked_reason": (
            f"Intent '{intent}' is not recognised by the validation system "
            "and cannot be safely executed."
        ),
    }
