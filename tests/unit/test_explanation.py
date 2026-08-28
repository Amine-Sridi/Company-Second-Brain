"""
Unit tests for the advisory match-explanation prompt builder (services/recruitment-
workflow/explanation.py). Pure function -- no DB, no HTTP, no LLM call. Run on the host
with plain pytest.
"""
import sys
from pathlib import Path

_SERVICE_DIR = Path(__file__).resolve().parents[2] / "services" / "recruitment-workflow"
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))

from db import MatchResult  # noqa: E402
from explanation import build_prompt  # noqa: E402


def _make_match(**overrides) -> MatchResult:
    defaults = dict(
        candidate_snapshot={
            "skills": ["Python", "Docker"],
            "years_of_experience": 4.0,
            "job_titles": ["Backend Engineer"],
        },
        matched_requirements=["Python"],
        missing_requirements=[],
        semantic_score=0.8,
        skill_score=1.0,
        experience_score=1.0,
        title_score=1.0,
        deterministic_score=1.0,
        final_score=0.9,
        threshold_snapshot=0.6,
        hard_gate_passed=True,
        qualified=True,
    )
    defaults.update(overrides)
    return MatchResult(**defaults)


def test_build_prompt_includes_score_breakdown_and_requirements():
    match = _make_match()
    prompt = build_prompt(match)

    assert "Python" in prompt
    assert "Docker" in prompt
    assert "Backend Engineer" in prompt
    assert "0.800" in prompt  # semantic_score
    assert "0.900" in prompt  # final_score
    assert "Qualified: True" in prompt


def test_build_prompt_reflects_disqualification():
    match = _make_match(
        matched_requirements=[],
        missing_requirements=["Python"],
        hard_gate_passed=False,
        qualified=False,
        final_score=0.2,
    )
    prompt = build_prompt(match)

    assert "Missing requirements: ['Python']" in prompt
    assert "All required skills present: False" in prompt
    assert "Qualified: False" in prompt


def test_build_prompt_ignores_unexpected_keys_in_candidate_snapshot():
    """candidate_snapshot is only ever populated with skills/years_of_experience/
    job_titles by main.py -- but build_prompt is also defensively whitelisted: it reads
    only those three known keys, so even a hypothetical future extra key (e.g. an
    accidental PII field) would never reach the prompt."""
    match = _make_match(
        candidate_snapshot={
            "skills": ["Python"],
            "years_of_experience": 3.0,
            "job_titles": ["Engineer"],
            "email": "should-never-appear@example.com",
            "name": "Should Never Appear",
        }
    )
    prompt = build_prompt(match)

    assert "should-never-appear" not in prompt
    assert "Should Never Appear" not in prompt


def test_build_prompt_instructs_model_not_to_include_pii():
    match = _make_match()
    prompt = build_prompt(match)
    assert "personal identifying information" in prompt
