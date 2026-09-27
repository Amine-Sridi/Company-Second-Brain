"""
tests/test_embedding_space.py
------------------------------
Second Brain's first test file. It exists for the one property most expensive to get
wrong: **retrieval must never compare vectors from two different embedding spaces.**

Cosine similarity is only meaningful within one space. Across two, the query still
returns numbers, still ranks candidates and still looks entirely plausible -- while being
semantically meaningless. That ranking becomes `semantic_score`, which is half of
`final_score`, which is persisted into an immutable `MatchResult` this project presents
as auditable and reproducible. A silent failure there is unrecoverable after the fact.

The trap is that the obvious guard misses it: `nomic-embed-text` emits exactly 768
dimensions, the same as the column, so a dimension check passes. Only a recorded space
catches it, which is what migration 0004 added.

No Postgres needed: the session is a stub that captures the SQL and parameters, so these
assert on the query actually built.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db as db_module  # noqa: E402


class _CapturingSession:
    """Records the last statement and params, and returns no rows."""

    def __init__(self):
        self.sql = None
        self.params = None

    def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = params or {}
        return SimpleNamespace(fetchall=lambda: [])


ACTIVE_MODEL = "gemini-embedding-001"


def _search(**kwargs):
    session = _CapturingSession()
    db_module.search_candidates(session, [0.01] * 8, top_k=5, **kwargs)
    return session


def test_gated_search_filters_on_the_embedding_space():
    s = _search(embedding_model_version=ACTIVE_MODEL, embedding_task_type=None)
    assert "embedding_model_version = :embedding_model_version" in s.sql
    assert s.params["embedding_model_version"] == ACTIVE_MODEL


def test_task_type_is_part_of_the_space_not_just_the_model():
    """Setting a task_type changes the embedding space even though the model is the same
    -- this is exactly what audit M11 proposed doing, and why it cannot be a one-line
    change."""
    s = _search(embedding_model_version=ACTIVE_MODEL, embedding_task_type="RETRIEVAL_DOCUMENT")
    assert "embedding_task_type IS NOT DISTINCT FROM :embedding_task_type" in s.sql
    assert s.params["embedding_task_type"] == "RETRIEVAL_DOCUMENT"


def test_null_task_type_matches_null_not_nothing():
    """Every vector written before migration 0004 has a NULL task_type. Plain `=` would
    never match NULL, so the active space would silently retrieve zero rows -- which is
    safe but useless. IS NOT DISTINCT FROM is what makes 'no task_type' a real space."""
    s = _search(embedding_model_version=ACTIVE_MODEL, embedding_task_type=None)
    assert "IS NOT DISTINCT FROM" in s.sql
    assert s.params["embedding_task_type"] is None
    assert "embedding_task_type = " not in s.sql


def test_the_space_is_a_bound_parameter_not_interpolated():
    """Unlike the vector literal and the UUID list (both documented as safe to format),
    the model version is a plain string with no reason to be concatenated into SQL."""
    s = _search(embedding_model_version="'; DROP TABLE candidate_knowledge; --")
    assert "DROP TABLE" not in s.sql
    assert s.params["embedding_model_version"] == "'; DROP TABLE candidate_knowledge; --"


def test_ungated_search_has_no_space_filter():
    """Maintenance tooling (a re-embed job) legitimately needs to see every generation."""
    s = _search()
    assert "embedding_model_version" not in s.sql


def test_candidate_id_restriction_and_space_gate_combine():
    ids = [uuid.uuid4(), uuid.uuid4()]
    s = _search(candidate_ids=ids, embedding_model_version=ACTIVE_MODEL)
    assert "candidate_id IN (" in s.sql
    assert "embedding_model_version = :embedding_model_version" in s.sql
    assert " AND " in s.sql


def test_empty_candidate_id_list_short_circuits():
    """Unchanged behaviour: an explicit empty subset means 'search nothing', and must not
    degenerate into an unfiltered search."""
    session = _CapturingSession()
    out = db_module.search_candidates(session, [0.01] * 8, top_k=5, candidate_ids=[])
    assert out == []
    assert session.sql is None, "no query should be issued at all"


def test_upsert_records_the_space_it_wrote():
    """A vector whose space is not recorded is the original defect -- there would be no
    way to know later which generation it belonged to."""
    import inspect

    sig = inspect.signature(db_module.upsert_candidate_knowledge)
    assert "embedding_model_version" in sig.parameters
    assert "embedding_task_type" in sig.parameters
    # The model version has no default: writing a vector without declaring its space
    # must be impossible, not merely discouraged.
    assert sig.parameters["embedding_model_version"].default is inspect.Parameter.empty
