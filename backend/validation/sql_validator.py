"""
Phase 4 — SQL Validator (Coordinator)
======================================
The single public interface to the Phase 4 validation pipeline.

Orchestration sequence:
  1. NEEDS_CLARIFICATION / UNKNOWN intent → skip validation entirely.
  2. No SQL present → pass through (some intents don't produce SQL).
  3. safety_checker.check_safety()       → may BLOCK; pipeline stops if so.
  4. schema_checker.check_schema()       → may FAIL; pipeline stops if so.
  5. schema_creator_validator.check_creator_sql() → may FAIL; pipeline stops if so.
  6. Assemble the final ValidationResult dict.
  7. Emit the Phase 4 debug log block to stdout (visible in Uvicorn console).

Return shape:
  {
    "valid":                bool,
    "risk_level":           "SAFE" | "HIGH_RISK" | "CRITICAL_RISK" | "BLOCKED" | None,
    "requires_confirmation": bool,
    "blocked_reason":       str | None,
    "failure_reason":       str | None,
  }

Phase 3.5 Compatibility Rule:
  NEEDS_CLARIFICATION is NOT a validation result — it is a conversational state.
  The validator returns risk_level=None for it, preserving Phase 3.5 behavior
  completely. The question field from Gemini is used unchanged as the reply.
"""

from typing import Optional
from validation.safety_checker import check_safety
from validation.schema_checker import check_schema
from validation.schema_creator_validator import check_creator_sql

# Intents that bypass validation entirely
_SKIP_INTENTS: set[str] = {"NEEDS_CLARIFICATION", "UNKNOWN"}

# Risk levels that require user confirmation before Phase 5 executes
_CONFIRMATION_REQUIRED: set[str] = {"HIGH_RISK", "CRITICAL_RISK"}


def validate(intent: str, sql: Optional[str], schema: dict) -> dict:
    """
    Run the full Phase 4 validation pipeline for a given intent + SQL pair.

    Args:
        intent: The Gemini-classified intent string.
        sql:    The generated SQL (may be None).
        schema: The in-memory schema dict from metadata_store.
                Maps table_name → [column_names].

    Returns:
        A validation result dict with keys:
          valid, risk_level, requires_confirmation, blocked_reason, failure_reason
    """
    _log_header()

    # ── Gate 0: Skip validation for non-actionable intents ────────────────────
    if intent in _SKIP_INTENTS:
        _log_skip(intent)
        return {
            "valid": True,
            "risk_level": None,
            "requires_confirmation": False,
            "blocked_reason": None,
            "failure_reason": None,
        }

    # ── Gate 0b: Pass through if no SQL was generated ─────────────────────────
    # Some intents (e.g. informational responses) may not produce SQL.
    if sql is None:
        _log_no_sql(intent)
        return {
            "valid": True,
            "risk_level": "SAFE",
            "requires_confirmation": False,
            "blocked_reason": None,
            "failure_reason": None,
        }

    _log_intent_and_sql(intent, sql)

    # ── Gate 1: Safety Checker ────────────────────────────────────────────────
    print("Running Safety Checker...")
    safety_result = check_safety(intent, sql)
    risk_level: str = safety_result["risk_level"]
    blocked_reason: Optional[str] = safety_result.get("blocked_reason")

    if risk_level == "BLOCKED":
        print(f"  [X] BLOCKED PATTERN DETECTED")
        print(f"  Reason: {blocked_reason}\n")
        _log_footer_blocked(blocked_reason)
        return {
            "valid": False,
            "risk_level": "BLOCKED",
            "requires_confirmation": False,
            "blocked_reason": blocked_reason,
            "failure_reason": None,
        }

    print("  [OK] No blocked patterns found.")
    print(f"  Risk Level: {risk_level}\n")

    # ── Gate 2: Schema Checker ────────────────────────────────────────────────
    print("Running Schema Checker...")
    schema_result = check_schema(intent, sql, schema)

    if not schema_result["valid"]:
        reason = schema_result["reason"]
        print(f"  [X] FAILED: {reason}\n")
        _log_footer_failed(risk_level, reason)
        return {
            "valid": False,
            "risk_level": risk_level,
            "requires_confirmation": False,
            "blocked_reason": None,
            "failure_reason": reason,
        }

    print("  [OK] Schema check PASSED.\n")

    # ── Gate 3: Schema Creator Validator ──────────────────────────────────────
    print("Running Creator Validator...")
    creator_result = check_creator_sql(intent, sql)

    if not creator_result["valid"]:
        reason = creator_result["reason"]
        print(f"  [X] FAILED: {reason}\n")
        _log_footer_failed(risk_level, reason)
        return {
            "valid": False,
            "risk_level": risk_level,
            "requires_confirmation": False,
            "blocked_reason": None,
            "failure_reason": reason,
        }

    print("  [OK] Creator validation PASSED.\n")

    # ── Assemble final passing result ─────────────────────────────────────────
    requires_confirmation = risk_level in _CONFIRMATION_REQUIRED
    _log_footer_pass(risk_level, requires_confirmation)

    return {
        "valid": True,
        "risk_level": risk_level,
        "requires_confirmation": requires_confirmation,
        "blocked_reason": None,
        "failure_reason": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 Debug Log Helpers
# Logs appear in the Uvicorn console for real-time debugging and learning.
# ─────────────────────────────────────────────────────────────────────────────

def _log_header() -> None:
    print("\n====================================")
    print("PHASE 4 VALIDATION")
    print("====================================\n")


def _log_intent_and_sql(intent: str, sql: str) -> None:
    print(f"Intent:\n{intent}\n")
    print(f"Generated SQL:")
    print(f"{sql}\n")


def _log_skip(intent: str) -> None:
    print(f"Intent:\n{intent}\n")
    print("  -> Validation skipped. Returning clarification question.")
    print("  Risk Level: null")
    print("\n====================================\n")


def _log_no_sql(intent: str) -> None:
    print(f"Intent:\n{intent}\n")
    print("  → No SQL generated. Passing through as SAFE.")
    print("\n====================================\n")


def _log_footer_pass(risk_level: str, requires_confirmation: bool) -> None:
    print("------------------------------------")
    print("Final Validation Result:")
    print(f"  Valid:                  TRUE")
    print(f"  Risk Level:             {risk_level}")
    print(f"  Requires Confirmation:  {str(requires_confirmation).upper()}")
    print(f"  Blocked Reason:         None")
    print("====================================\n")


def _log_footer_blocked(reason: Optional[str]) -> None:
    print("------------------------------------")
    print("Final Validation Result:")
    print(f"  Valid:                  FALSE")
    print(f"  Risk Level:             BLOCKED")
    print(f"  Requires Confirmation:  FALSE")
    print(f"  Blocked Reason:         {reason}")
    print("====================================\n")


def _log_footer_failed(risk_level: str, reason: str) -> None:
    print("------------------------------------")
    print("Final Validation Result:")
    print(f"  Valid:                  FALSE")
    print(f"  Risk Level:             {risk_level}")
    print(f"  Requires Confirmation:  FALSE")
    print(f"  Blocked Reason:         None")
    print(f"  Failure Reason:         {reason}")
    print("====================================\n")
