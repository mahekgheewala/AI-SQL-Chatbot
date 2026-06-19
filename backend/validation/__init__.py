"""
Phase 4 — Validation Package
==============================
Contains the SQL validation pipeline:
  - safety_checker.py      : Detects BLOCKED system-level operations.
  - schema_checker.py      : Validates table/column existence against metadata cache.
  - schema_creator_validator.py : Validates DDL identifiers and column types.
  - sql_validator.py       : Coordinator — runs all checkers and produces the final result.
"""
