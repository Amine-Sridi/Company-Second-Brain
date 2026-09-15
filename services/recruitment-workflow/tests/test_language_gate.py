"""
tests/test_language_gate.py
------------------------------
Pure unit tests for language_gate.compute_language_gate -- no DB, no HTTP, no
mocking: every case is just five plain arguments in, one status out. This is the
one function that decides accept/advance eligibility (see main.py:
accept_application), so every branch is covered directly rather than only through
the route-level enforcement tests in test_accept_language_gate.py.
"""
from __future__ import annotations

import pytest

from language_gate import BLOCKING_STATUSES, compute_language_gate


def test_no_requirement_is_not_required_regardless_of_assessment():
    assert compute_language_gate(None, None, False, None, None) == "not_required"
    assert compute_language_gate(None, None, True, "en", "C1") == "not_required"


def test_requirement_with_no_interview_result_yet_is_pending():
    assert compute_language_gate("en", "B2", False, None, None) == "pending"


def test_exact_match_cefr_passes():
    assert compute_language_gate("en", "B2", True, "en", "B2") == "passed"


def test_higher_assessed_cefr_passes():
    assert compute_language_gate("en", "B2", True, "en", "C1") == "passed"


def test_lower_assessed_cefr_fails():
    assert compute_language_gate("en", "C1", True, "en", "B2") == "failed"


def test_language_mismatch_needs_review_even_if_cefr_would_pass():
    # Assessed CEFR (C1) is higher than required (B2), but the language differs --
    # this is a data/process error (wrong interview language configured), not a
    # genuine ability shortfall, so it must not silently read as "passed" or "failed".
    assert compute_language_gate("en", "B2", True, "fr", "C1") == "needs_review"


def test_missing_assessed_language_needs_review():
    assert compute_language_gate("en", "B2", True, None, "B2") == "needs_review"


def test_missing_assessed_cefr_needs_review():
    # e.g. VoiceHR's own evaluation failed (evaluation_status == "failed") -- an
    # interview result exists, but there is no score to compare.
    assert compute_language_gate("en", "B2", True, "en", None) == "needs_review"


def test_insufficient_evidence_needs_review_not_pass_or_fail():
    """VoiceHR's interview rubric/quality pass (see handoff.md) added a distinct
    evaluation_status, "insufficient_evidence" (shared/schemas.InterviewAssessment),
    for a transcript too short/thin to support a defensible CEFR verdict -- VoiceHR
    always sends assessed_cefr/assessed_language as null in that case (evaluator.py
    never fabricates a grade from too little evidence). This function needed no
    change at all to handle it correctly: a null assessed_cefr already falls into
    this exact branch, identical to test_missing_assessed_cefr_needs_review above --
    named separately here so the connection to that specific outcome is explicit and
    searchable, not just an incidental consequence of the missing-CEFR case."""
    assert compute_language_gate("en", "B2", True, None, None) == "needs_review"


def test_invalid_assessed_cefr_needs_review():
    assert compute_language_gate("en", "B2", True, "en", "Z9") == "needs_review"


def test_invalid_required_cefr_needs_review():
    # Defensive: required_cefr should always be one of the six known levels (enforced
    # at the API/DB boundary), but the pure function itself never trusts that blindly.
    assert compute_language_gate("en", "not-a-level", True, "en", "B2") == "needs_review"


@pytest.mark.parametrize(
    "level_pair",
    [("A1", "A1"), ("A1", "A2"), ("B1", "C2"), ("C2", "C2")],
)
def test_cefr_ordering_is_respected_across_the_full_scale(level_pair):
    required, assessed = level_pair
    assert compute_language_gate("en", required, True, "en", assessed) == "passed"


def test_cefr_ordering_below_requirement_fails_across_the_full_scale():
    assert compute_language_gate("en", "C2", True, "en", "C1") == "failed"


def test_blocking_statuses_are_exactly_pending_failed_needs_review():
    assert BLOCKING_STATUSES == {"pending", "failed", "needs_review"}
    assert "passed" not in BLOCKING_STATUSES
    assert "not_required" not in BLOCKING_STATUSES
