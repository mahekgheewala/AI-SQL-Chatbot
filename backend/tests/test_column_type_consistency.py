"""Regression tests guarding against the column-type whitelist drifting
apart again. db.column_types.ALLOWED_COLUMN_TYPES is the documented single
source of truth (its own docstring records this exact class of bug already
happened once, between db/table_manager.py and validation/schema_creator_
validator.py, before being consolidated there).

Two more independent copies were found and fixed in this pass:
  - agent/capability_check.py's _type_allowed() had its own hand-copied
    set that had drifted (accepted "SMALLINT"/"REAL"/"DOUBLE PRECISION",
    none of which are actually in ALLOWED_COLUMN_TYPES) — a column typed
    that way looked accepted at the semantic-frame stage and was only
    rejected several steps later, at real validation.
  - agent/pending_resolution.py's _VALID_DATATYPES (used to decide whether
    a clarification reply "looks like" a valid column definition, before
    turning it straight into ready-to-execute CREATE TABLE SQL) had the
    same drift.

These tests assert all the type-checking entry points agree with the
authoritative list for the same input, so a future hand-edit to any one of
them that reintroduces a mismatch fails immediately instead of silently
shipping a "looks fine, then fails at confirmation" bug.

Run with:
    pytest backend/tests/test_column_type_consistency.py -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from db.column_types import ALLOWED_COLUMN_TYPES, is_allowed_column_type
from agent.capability_check import _type_allowed
from agent.pending_resolution import _VALID_DATATYPES

# Types that are valid PostgreSQL syntax but deliberately NOT in this app's
# restricted whitelist — every gate must agree they're rejected.
NOT_ALLOWED = ["SMALLINT", "REAL", "DOUBLE PRECISION", "TIME", "BIGSERIAL", "BYTEA", "TIMESTAMPTZ"]


@pytest.mark.parametrize("type_name", sorted(ALLOWED_COLUMN_TYPES))
def test_capability_check_accepts_every_allowed_type(type_name):
    assert _type_allowed(type_name) is True


@pytest.mark.parametrize("type_name", NOT_ALLOWED)
def test_capability_check_rejects_every_disallowed_type(type_name):
    assert _type_allowed(type_name) is False
    # Must agree with the real, final validation gate too — this is the
    # actual bug that was found: these used to disagree.
    assert is_allowed_column_type(type_name) is False


@pytest.mark.parametrize("type_name", sorted(ALLOWED_COLUMN_TYPES))
def test_pending_resolution_datatype_set_matches_authoritative_list(type_name):
    assert type_name.lower() in _VALID_DATATYPES


@pytest.mark.parametrize("type_name", NOT_ALLOWED)
def test_pending_resolution_datatype_set_excludes_disallowed_type(type_name):
    assert type_name.lower() not in _VALID_DATATYPES
