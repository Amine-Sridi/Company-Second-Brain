"""
Unit tests for the RWS deterministic + semantic hybrid scoring engine.

Pure functions -- no DB, no HTTP, no LLM. Run on the host with plain pytest.
"""
import sys
from pathlib import Path

import pytest

_SERVICE_DIR = Path(__file__).resolve().parents[2] / "services" / "recruitment-workflow"
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))

from scoring import (  # noqa: E402
    CandidateScoringInput,
    JobRequirements,
    ScoringWeights,
    check_required_skills_gate,
    compute_deterministic_score,
    compute_experience_score,
    compute_final_score,
    compute_skill_score,
    compute_title_score,
    score_candidate,
)


# ---------------------------------------------------------------------------
# compute_skill_score
# ---------------------------------------------------------------------------

def test_skill_score_full_match():
    assert compute_skill_score(["Python", "SQL"], ["python", "sql"], []) == 1.0


def test_skill_score_partial_match():
    score = compute_skill_score(["Python"], ["Python", "SQL"], [])
    assert score == 0.5


def test_skill_score_no_match():
    assert compute_skill_score(["Java"], ["Python"], []) == 0.0


def test_skill_score_case_insensitive():
    assert compute_skill_score(["PYTHON"], ["python"], []) == 1.0


def test_skill_score_includes_preferred_skills_in_denominator():
    # 1 of 2 (required + preferred) present
    score = compute_skill_score(["Python"], ["Python"], ["Docker"])
    assert score == 0.5


def test_skill_score_no_requirements_is_trivially_satisfied():
    assert compute_skill_score([], [], []) == 1.0


def test_skill_score_ignores_whitespace_only_entries():
    assert compute_skill_score(["Python"], ["Python", "  "], []) == 1.0


# ---------------------------------------------------------------------------
# compute_experience_score
# ---------------------------------------------------------------------------

def test_experience_score_no_requirement():
    assert compute_experience_score(0.0, None) == 1.0


def test_experience_score_meets_requirement():
    assert compute_experience_score(5.0, 3.0) == 1.0


def test_experience_score_exceeds_requirement():
    assert compute_experience_score(10.0, 3.0) == 1.0


def test_experience_score_unknown_candidate_experience():
    assert compute_experience_score(None, 3.0) == 0.0


def test_experience_score_partial_credit():
    assert compute_experience_score(1.5, 3.0) == pytest.approx(0.5)


def test_experience_score_zero_minimum_is_no_requirement():
    assert compute_experience_score(None, 0.0) == 1.0


# ---------------------------------------------------------------------------
# compute_title_score
# ---------------------------------------------------------------------------

def test_title_score_no_target_titles():
    assert compute_title_score(["Anything"], []) == 1.0


def test_title_score_exact_match():
    assert compute_title_score(["Software Engineer"], ["Software Engineer"]) == 1.0


def test_title_score_case_insensitive_substring_match():
    assert compute_title_score(["Senior Backend Engineer"], ["Backend Engineer"]) == 1.0


def test_title_score_no_match():
    assert compute_title_score(["Sales Manager"], ["Software Engineer"]) == 0.0


# ---------------------------------------------------------------------------
# compute_deterministic_score / compute_final_score
# ---------------------------------------------------------------------------

def test_deterministic_score_weighted_sum():
    score = compute_deterministic_score(
        skill_score=1.0,
        experience_score=0.5,
        title_score=0.0,
        skill_weight=0.6,
        experience_weight=0.2,
        title_weight=0.2,
    )
    assert score == pytest.approx(0.6 * 1.0 + 0.2 * 0.5 + 0.2 * 0.0)


def test_final_score_weighted_sum():
    score = compute_final_score(
        semantic_score=0.8,
        deterministic_score=0.4,
        weight_semantic=0.5,
        weight_deterministic=0.5,
    )
    assert score == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# check_required_skills_gate
# ---------------------------------------------------------------------------

def test_gate_passes_when_all_required_skills_present():
    passed, matched, missing = check_required_skills_gate(
        ["Python", "SQL", "Docker"], ["Python", "SQL"]
    )
    assert passed is True
    assert matched == ["Python", "SQL"]
    assert missing == []


def test_gate_fails_when_a_required_skill_is_missing():
    passed, matched, missing = check_required_skills_gate(["Python"], ["Python", "SQL"])
    assert passed is False
    assert matched == ["Python"]
    assert missing == ["SQL"]


def test_gate_passes_trivially_with_no_required_skills():
    passed, matched, missing = check_required_skills_gate(["Python"], [])
    assert passed is True
    assert matched == []
    assert missing == []


def test_gate_is_case_insensitive():
    passed, _, missing = check_required_skills_gate(["python"], ["Python"])
    assert passed is True
    assert missing == []


# ---------------------------------------------------------------------------
# score_candidate (end-to-end pure scoring)
# ---------------------------------------------------------------------------

def _weights(**overrides) -> ScoringWeights:
    base = dict(
        weight_semantic=0.5,
        weight_deterministic=0.5,
        skill_weight=0.6,
        experience_weight=0.2,
        title_weight=0.2,
    )
    base.update(overrides)
    return ScoringWeights(**base)


def test_score_candidate_qualified_when_gate_passes_and_score_above_threshold():
    candidate = CandidateScoringInput(
        candidate_id="c1",
        skills=["Python", "SQL"],
        years_of_experience=5.0,
        job_titles=["Software Engineer"],
        semantic_score=0.9,
    )
    job = JobRequirements(
        required_skills=["Python"],
        preferred_skills=["SQL"],
        min_years_experience=2.0,
        titles=["Software Engineer"],
    )
    result = score_candidate(candidate, job, _weights(), threshold=0.6)
    assert result.hard_gate_passed is True
    assert result.qualified is True
    assert result.final_score > 0.6


def test_score_candidate_disqualified_by_hard_gate_despite_high_semantic_score():
    """A missing required skill must disqualify the candidate even with a near-perfect
    semantic score -- the LLM/embedding similarity can never override the hard gate."""
    candidate = CandidateScoringInput(
        candidate_id="c1",
        skills=["Java"],
        years_of_experience=10.0,
        job_titles=["Software Engineer"],
        semantic_score=0.99,
    )
    job = JobRequirements(
        required_skills=["Python"],
        preferred_skills=[],
        min_years_experience=None,
        titles=[],
    )
    result = score_candidate(candidate, job, _weights(), threshold=0.1)
    assert result.hard_gate_passed is False
    assert result.missing_requirements == ["Python"]
    assert result.qualified is False


def test_score_candidate_disqualified_when_below_threshold_even_if_gate_passes():
    candidate = CandidateScoringInput(
        candidate_id="c1",
        skills=["Python"],
        years_of_experience=0.0,
        job_titles=[],
        semantic_score=0.1,
    )
    job = JobRequirements(
        required_skills=["Python"],
        preferred_skills=[],
        min_years_experience=10.0,
        titles=[],
    )
    result = score_candidate(candidate, job, _weights(), threshold=0.9)
    assert result.hard_gate_passed is True
    assert result.final_score < 0.9
    assert result.qualified is False


def test_score_candidate_result_contains_all_components():
    candidate = CandidateScoringInput(
        candidate_id="c1",
        skills=["Python"],
        years_of_experience=3.0,
        job_titles=["Engineer"],
        semantic_score=0.5,
    )
    job = JobRequirements(required_skills=["Python"])
    result = score_candidate(candidate, job, _weights(), threshold=0.5)

    assert result.candidate_id == "c1"
    assert 0.0 <= result.semantic_score <= 1.0
    assert 0.0 <= result.skill_score <= 1.0
    assert 0.0 <= result.experience_score <= 1.0
    assert 0.0 <= result.title_score <= 1.0
    assert 0.0 <= result.deterministic_score <= 1.0
    assert 0.0 <= result.final_score <= 1.0
    assert isinstance(result.matched_requirements, list)
    assert isinstance(result.missing_requirements, list)
