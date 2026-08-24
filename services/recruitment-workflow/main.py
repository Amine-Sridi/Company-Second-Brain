"""
Recruitment Workflow Service (RWS) -- FastAPI application.

Owns JD structuring, the matching engine, and immutable MatchResult persistence.

Architectural invariant: the LLM (structure_job_description) only extracts JD
requirements into a structured shape. It never sees a candidate, never computes a
score, and never decides qualification -- scoring.score_candidate does that,
deterministically, from data already persisted or fetched over HTTP.
"""
from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.orm import Session

import clients
from db import (
    create_job_with_description,
    create_match_result,
    create_scoring_config,
    get_db,
    get_job,
    get_latest_scoring_config,
    get_scoring_config_by_version,
    list_match_results_for_job,
)
from clients import SecondBrainClientError
from schemas import (
    JobResponse,
    JobSubmissionRequest,
    MatchResultResponse,
    ScoringConfigRequest,
    ScoringConfigResponse,
    StructuredJobDescription,
)
from scoring import CandidateScoringInput, JobRequirements, ScoringWeights, score_candidate
from shared.llm import LLMProvider, LLMProviderError

logging.basicConfig(level="INFO")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_api_key: str = "changeme-recruitment-workflow-key"
    second_brain_api_key: str = "changeme-second-brain-key"
    second_brain_url: str = "http://second-brain:8000"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    embedding_model_version: str = "gemini-embedding-001"
    log_level: str = "INFO"


settings = Settings()

app = FastAPI(title="Recruitment Workflow Service")


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def require_service_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if x_api_key != settings.service_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing service API key")


_llm_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    """Lazy singleton -- only instantiated on first JD structuring request."""
    global _llm_provider
    if _llm_provider is None:
        from shared.llm import GeminiProvider
        try:
            _llm_provider = GeminiProvider(api_key=settings.gemini_api_key or None)
        except LLMProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
    return _llm_provider


# ---------------------------------------------------------------------------
# JD structuring (LLM boundary -- structure only, never score/qualify)
# ---------------------------------------------------------------------------

async def structure_job_description(
    llm: LLMProvider, raw_description: str
) -> StructuredJobDescription:
    prompt = (
        "Extract structured hiring requirements from this job description. "
        "required_skills: must-have technical/soft skills, short strings. "
        "preferred_skills: nice-to-have skills, short strings. "
        "min_years_experience: minimum years of relevant experience required, "
        "or null if not specified. "
        "titles: job title variants this role matches (e.g. ['Backend Engineer', "
        "'Software Engineer']).\n\n"
        f"Job description:\n{raw_description}"
    )
    return await llm.generate_structured(
        prompt, StructuredJobDescription, model=settings.gemini_model
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "service": "recruitment-workflow"}


@app.post("/v1/jobs", response_model=JobResponse, status_code=200)
async def submit_job(
    payload: JobSubmissionRequest,
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm_provider),
    _auth: None = Depends(require_service_key),
):
    """
    Full JD-to-matches pipeline:
      1. Gemini structures the JD (skills/experience/titles) -- advisory extraction only.
      2. Job + JobDescription (version 1) persisted.
      3. Second Brain retrieval (recall only) narrows the candidate pool by semantic
         similarity to the raw JD text.
      4. For each retrieved candidate, RWS fetches skills/experience/titles from Second
         Brain and computes the deterministic + semantic hybrid score itself.
      5. Every scored candidate gets an immutable MatchResult row, regardless of
         qualification outcome, so the full match set is auditable.
    """
    try:
        structured = await structure_job_description(llm, payload.raw_description)
    except LLMProviderError as exc:
        raise HTTPException(status_code=502, detail=f"JD structuring failed: {exc}")

    if payload.scoring_config_version is not None:
        scoring_config = get_scoring_config_by_version(db, payload.scoring_config_version)
        if scoring_config is None:
            raise HTTPException(
                status_code=422,
                detail=f"scoring_config_version {payload.scoring_config_version} does not exist",
            )
    else:
        scoring_config = get_latest_scoring_config(db)
        if scoring_config is None:
            raise HTTPException(status_code=500, detail="No scoring config exists")

    job, job_description = create_job_with_description(
        db,
        title=payload.title,
        threshold=payload.threshold,
        raw_text=payload.raw_description,
        required_skills=structured.required_skills,
        preferred_skills=structured.preferred_skills,
        min_years_experience=structured.min_years_experience,
        titles=structured.titles,
        structuring_model=settings.gemini_model,
    )

    try:
        retrieved = await clients.retrieve_candidates(
            settings.second_brain_url,
            settings.second_brain_api_key,
            query_text=payload.raw_description,
            top_k=payload.top_k,
        )
    except SecondBrainClientError as exc:
        raise HTTPException(status_code=502, detail=f"Second Brain retrieval failed: {exc}")

    weights = ScoringWeights(
        weight_semantic=scoring_config.weight_semantic,
        weight_deterministic=scoring_config.weight_deterministic,
        skill_weight=scoring_config.skill_weight,
        experience_weight=scoring_config.experience_weight,
        title_weight=scoring_config.title_weight,
    )
    job_requirements = JobRequirements(
        required_skills=structured.required_skills,
        preferred_skills=structured.preferred_skills,
        min_years_experience=structured.min_years_experience,
        titles=structured.titles,
    )

    match_responses: list[MatchResultResponse] = []
    for entry in retrieved:
        candidate_id = uuid.UUID(entry["candidate_id"])
        try:
            candidate = await clients.get_candidate(
                settings.second_brain_url, settings.second_brain_api_key, candidate_id
            )
        except SecondBrainClientError as exc:
            raise HTTPException(
                status_code=502, detail=f"Second Brain candidate lookup failed: {exc}"
            )
        if candidate is None:
            # Retrieved by the index but deleted before we could fetch details; skip.
            continue

        scoring_input = CandidateScoringInput(
            candidate_id=str(candidate_id),
            skills=candidate["skills"],
            years_of_experience=candidate["years_of_experience"],
            job_titles=candidate["job_titles"],
            semantic_score=entry["similarity_score"],
        )
        result = score_candidate(scoring_input, job_requirements, weights, payload.threshold)

        candidate_snapshot = {
            "skills": candidate["skills"],
            "years_of_experience": candidate["years_of_experience"],
            "job_titles": candidate["job_titles"],
        }
        match = create_match_result(
            db,
            job_id=job.id,
            job_description_id=job_description.id,
            job_description_version=job_description.version,
            scoring_config_id=scoring_config.id,
            scoring_config_version=scoring_config.version,
            embedding_model_version=settings.embedding_model_version,
            candidate_id=candidate_id,
            candidate_snapshot=candidate_snapshot,
            threshold_snapshot=payload.threshold,
            semantic_score=result.semantic_score,
            skill_score=result.skill_score,
            experience_score=result.experience_score,
            title_score=result.title_score,
            deterministic_score=result.deterministic_score,
            final_score=result.final_score,
            hard_gate_passed=result.hard_gate_passed,
            matched_requirements=result.matched_requirements,
            missing_requirements=result.missing_requirements,
            qualified=result.qualified,
        )
        match_responses.append(
            MatchResultResponse(
                match_id=match.id,
                candidate_id=candidate_id,
                semantic_score=result.semantic_score,
                skill_score=result.skill_score,
                experience_score=result.experience_score,
                title_score=result.title_score,
                deterministic_score=result.deterministic_score,
                final_score=result.final_score,
                hard_gate_passed=result.hard_gate_passed,
                matched_requirements=result.matched_requirements,
                missing_requirements=result.missing_requirements,
                qualified=result.qualified,
            )
        )

    match_responses.sort(key=lambda m: m.final_score, reverse=True)

    return JobResponse(
        job_id=job.id,
        job_description_id=job_description.id,
        version=job_description.version,
        title=job.title,
        required_skills=structured.required_skills,
        preferred_skills=structured.preferred_skills,
        min_years_experience=structured.min_years_experience,
        titles=structured.titles,
        threshold=job.threshold,
        scoring_config_version=scoring_config.version,
        matches=match_responses,
    )


@app.get("/v1/jobs/{job_id}/matches", response_model=list[MatchResultResponse])
def get_job_matches(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_service_key),
):
    """Read-only inspection of previously persisted MatchResults for a job."""
    if get_job(db, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    matches = list_match_results_for_job(db, job_id)
    return [
        MatchResultResponse(
            match_id=m.id,
            candidate_id=m.candidate_id,
            semantic_score=m.semantic_score,
            skill_score=m.skill_score,
            experience_score=m.experience_score,
            title_score=m.title_score,
            deterministic_score=m.deterministic_score,
            final_score=m.final_score,
            hard_gate_passed=m.hard_gate_passed,
            matched_requirements=m.matched_requirements,
            missing_requirements=m.missing_requirements,
            qualified=m.qualified,
        )
        for m in matches
    ]


@app.post("/v1/scoring-configs", response_model=ScoringConfigResponse, status_code=201)
def submit_scoring_config(
    payload: ScoringConfigRequest,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_service_key),
):
    """
    Create a new versioned ScoringConfig. Weight-sum validation (both weight groups
    must each sum to 1.0) happens in ScoringConfigRequest; the DB also enforces it via
    CHECK constraints as defense in depth.
    """
    config = create_scoring_config(
        db,
        weight_semantic=payload.weight_semantic,
        weight_deterministic=payload.weight_deterministic,
        skill_weight=payload.skill_weight,
        experience_weight=payload.experience_weight,
        title_weight=payload.title_weight,
    )
    return ScoringConfigResponse(
        id=config.id,
        version=config.version,
        weight_semantic=config.weight_semantic,
        weight_deterministic=config.weight_deterministic,
        skill_weight=config.skill_weight,
        experience_weight=config.experience_weight,
        title_weight=config.title_weight,
        created_at=config.created_at,
    )
