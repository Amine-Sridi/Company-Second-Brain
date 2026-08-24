"""
Deterministic + semantic hybrid scoring engine.

Pure functions only — no DB, no HTTP, no LLM calls. This module is the one place
the qualification decision is made, per the architectural rule that the LLM never
determines final_score, qualified, or threshold comparisons.

Component scores are each normalised to [0.0, 1.0]. The two weighted sums
(sub-component weights inside the deterministic score, and semantic/deterministic
weights in the final score) are each validated to sum to 1.0 at the ScoringConfig
level (see db.py), not here — this module trusts the weights it is given.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


def _normalize(values: list[str]) -> set[str]:
    return {v.strip().lower() for v in values if v and v.strip()}


def compute_skill_score(
    candidate_skills: list[str],
    required_skills: list[str],
    preferred_skills: list[str],
) -> float:
    """
    Fraction of (required ∪ preferred) skills the candidate has, case-insensitive.

    If the JD specifies no skills at all, the requirement is trivially satisfied.
    """
    target = _normalize(required_skills) | _normalize(preferred_skills)
    if not target:
        return 1.0
    have = _normalize(candidate_skills)
    matched = target & have
    return len(matched) / len(target)


def compute_experience_score(
    candidate_years: Optional[float], min_years_experience: Optional[float]
) -> float:
    """
    1.0 if the JD has no minimum, or the candidate meets/exceeds it.
    0.0 if the JD requires experience and the candidate's is unknown.
    Otherwise partial credit: candidate_years / min_years_experience.
    """
    if min_years_experience is None or min_years_experience <= 0:
        return 1.0
    if candidate_years is None:
        return 0.0
    if candidate_years >= min_years_experience:
        return 1.0
    return max(0.0, candidate_years / min_years_experience)


def compute_title_score(candidate_titles: list[str], jd_titles: list[str]) -> float:
    """
    1.0 if the JD specifies no target titles, or any candidate title matches
    (case-insensitive, exact or substring either direction) any JD title.
    Otherwise 0.0 — title match is binary, unlike skills/experience.
    """
    targets = _normalize(jd_titles)
    if not targets:
        return 1.0
    for candidate_title in _normalize(candidate_titles):
        for target in targets:
            if candidate_title == target or candidate_title in target or target in candidate_title:
                return 1.0
    return 0.0


def compute_deterministic_score(
    skill_score: float,
    experience_score: float,
    title_score: float,
    skill_weight: float,
    experience_weight: float,
    title_weight: float,
) -> float:
    return (
        skill_weight * skill_score
        + experience_weight * experience_score
        + title_weight * title_score
    )


def compute_final_score(
    semantic_score: float,
    deterministic_score: float,
    weight_semantic: float,
    weight_deterministic: float,
) -> float:
    return weight_semantic * semantic_score + weight_deterministic * deterministic_score


def check_required_skills_gate(
    candidate_skills: list[str], required_skills: list[str]
) -> tuple[bool, list[str], list[str]]:
    """
    Hard gate: every required_skill must be present in the candidate's skills
    (case-insensitive). Returns (passed, matched_requirements, missing_requirements)
    using the original (non-lowercased) required_skills strings for readability.
    """
    have = _normalize(candidate_skills)
    matched = [s for s in required_skills if s.strip().lower() in have]
    missing = [s for s in required_skills if s.strip().lower() not in have]
    return (len(missing) == 0, matched, missing)


@dataclass
class ScoringWeights:
    weight_semantic: float
    weight_deterministic: float
    skill_weight: float
    experience_weight: float
    title_weight: float


@dataclass
class CandidateScoringInput:
    candidate_id: str
    skills: list[str]
    years_of_experience: Optional[float]
    job_titles: list[str]
    semantic_score: float


@dataclass
class JobRequirements:
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    min_years_experience: Optional[float] = None
    titles: list[str] = field(default_factory=list)


@dataclass
class ScoringResult:
    candidate_id: str
    semantic_score: float
    skill_score: float
    experience_score: float
    title_score: float
    deterministic_score: float
    final_score: float
    hard_gate_passed: bool
    matched_requirements: list[str]
    missing_requirements: list[str]
    qualified: bool


def score_candidate(
    candidate: CandidateScoringInput,
    job: JobRequirements,
    weights: ScoringWeights,
    threshold: float,
) -> ScoringResult:
    """
    Compute every score component for one candidate against one job, deterministically.

    qualified = hard_gate_passed AND final_score >= threshold. The hard gate is
    evaluated independently of final_score so a high semantic score can never paper
    over a missing required skill.
    """
    skill_score = compute_skill_score(
        candidate.skills, job.required_skills, job.preferred_skills
    )
    experience_score = compute_experience_score(
        candidate.years_of_experience, job.min_years_experience
    )
    title_score = compute_title_score(candidate.job_titles, job.titles)
    deterministic_score = compute_deterministic_score(
        skill_score,
        experience_score,
        title_score,
        weights.skill_weight,
        weights.experience_weight,
        weights.title_weight,
    )
    final_score = compute_final_score(
        candidate.semantic_score,
        deterministic_score,
        weights.weight_semantic,
        weights.weight_deterministic,
    )
    hard_gate_passed, matched_requirements, missing_requirements = check_required_skills_gate(
        candidate.skills, job.required_skills
    )
    qualified = hard_gate_passed and final_score >= threshold

    return ScoringResult(
        candidate_id=candidate.candidate_id,
        semantic_score=candidate.semantic_score,
        skill_score=skill_score,
        experience_score=experience_score,
        title_score=title_score,
        deterministic_score=deterministic_score,
        final_score=final_score,
        hard_gate_passed=hard_gate_passed,
        matched_requirements=matched_requirements,
        missing_requirements=missing_requirements,
        qualified=qualified,
    )
