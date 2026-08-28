"""
Advisory, LLM-generated match explanations.

Strictly downstream of scoring: called only after a MatchResult already exists,
immutable, with final_score/qualified already computed by scoring.py. This module never
computes a score, never re-evaluates the threshold or hard gate, and never writes to
MatchResult -- it only reads one and writes a separate, cached MatchExplanation row.

Input to the LLM is limited to what's already inside the immutable MatchResult:
candidate_snapshot (skills / years_of_experience / job_titles -- never name/email/phone,
which never reach this service or Second Brain in the first place), matched/missing
requirements, and the score breakdown. No PII is ever available to construct the prompt
from, let alone sent.

Generated at most once per MatchResult (match_result_id UNIQUE in match_explanations)
and reused on every subsequent call. A generation failure (Gemini unreachable, API key
missing, malformed output) leaves the explanation unavailable (None) -- it never raises
past this module and never affects the already-persisted MatchResult.
"""
from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db import MatchResult, create_match_explanation, get_match_explanation
from shared.llm import LLMProvider, LLMProviderError

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"


def build_prompt(match: MatchResult) -> str:
    snapshot = match.candidate_snapshot or {}
    return (
        "You are assisting a recruiter by explaining an automated candidate match "
        "result. Write a concise explanation (3-5 sentences) covering: which "
        "requirements were matched, which were missing, a brief breakdown of the "
        "scores below, and why the candidate did or did not qualify. Base your answer "
        "only on the facts given below -- do not invent anything beyond them. Do not "
        "include or guess at any name, contact detail, or other personal identifying "
        "information; none is provided to you and none should appear in your answer.\n\n"
        f"Candidate skills: {snapshot.get('skills', [])}\n"
        f"Candidate years of experience: {snapshot.get('years_of_experience')}\n"
        f"Candidate job titles: {snapshot.get('job_titles', [])}\n"
        f"Matched requirements: {match.matched_requirements}\n"
        f"Missing requirements: {match.missing_requirements}\n"
        f"Semantic score: {match.semantic_score:.3f}\n"
        f"Skill score: {match.skill_score:.3f}\n"
        f"Experience score: {match.experience_score:.3f}\n"
        f"Title score: {match.title_score:.3f}\n"
        f"Deterministic score: {match.deterministic_score:.3f}\n"
        f"Final score: {match.final_score:.3f}\n"
        f"Qualification threshold: {match.threshold_snapshot:.3f}\n"
        f"All required skills present: {match.hard_gate_passed}\n"
        f"Qualified: {match.qualified}\n"
    )


async def get_or_create_explanation(
    db: Session, llm: LLMProvider, match: MatchResult, model: str
) -> str | None:
    """
    Return the cached explanation for match, generating and storing it once if none
    exists yet. Reads match.final_score/qualified/etc. purely as prompt input -- never
    writes to MatchResult, never re-derives or overrides the qualification decision.
    """
    existing = get_match_explanation(db, match.id)
    if existing is not None:
        return existing.explanation

    prompt = build_prompt(match)
    try:
        text = await llm.generate_text(prompt, model=model)
    except LLMProviderError:
        logger.warning(
            "match_explanation_generation_failed", extra={"match_id": str(match.id)}
        )
        return None

    try:
        row = create_match_explanation(
            db,
            match_result_id=match.id,
            explanation=text,
            model=model,
            prompt_version=PROMPT_VERSION,
        )
    except IntegrityError:
        # A concurrent request already generated and stored one for this match --
        # reuse it rather than generating (and storing) a second one.
        db.rollback()
        existing = get_match_explanation(db, match.id)
        return existing.explanation if existing else text
    return row.explanation
