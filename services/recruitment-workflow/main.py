"""
Recruitment Workflow Service (RWS) -- FastAPI application.

Owns JD structuring, the matching engine, and immutable MatchResult persistence.

Architectural invariant: the LLM (structure_job_description) only extracts JD
requirements into a structured shape. It never sees a candidate, never computes a
score, and never decides qualification -- scoring.score_candidate does that,
deterministically, from data already persisted or fetched over HTTP.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from pathlib import Path
from typing import Annotated, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

import clients
import interview_round_mailer
import resume_intelligence_client
import voicehr_client
from auth import InvalidTokenError, create_access_token, decode_access_token, verify_password
from db import (
    Recruiter,
    cancel_interview_round,
    complete_interview_round,
    create_application,
    create_interview_round,
    create_job_with_description,
    create_match_result,
    create_scoring_config,
    get_application,
    get_db,
    get_interview_round,
    get_job,
    get_job_description_for_job,
    get_latest_scoring_config,
    get_match_explanation,
    get_match_result,
    get_recruiter_by_email,
    get_recruiter_by_id,
    get_scoring_config_by_version,
    insert_webhook_event,
    list_all_applications,
    list_all_match_results,
    list_applications_for_job,
    list_interview_rounds_for_application,
    list_jobs_with_match_counts,
    list_match_results_for_job,
    mark_webhook_event_processed,
    record_interview_result,
    record_invite_attempt,
    reschedule_interview_round,
    set_job_language_requirement,
    transition_application,
    update_interview_round_notification,
    update_job_description_extraction_metadata,
)
from explanation import get_or_create_explanation
from clients import SecondBrainClientError
from interview_round_mailer import NotificationDeliveryError
from match_anomaly import MatchOutcome, detect_match_anomaly, merge_anomaly_warning
from resume_intelligence_client import ResumeIntelligenceClientError
from schemas import (
    ApplicationResponse,
    CandidateIdentityResponse,
    CandidateJobGraphResponse,
    CandidateKnowledgeResponse,
    CandidateSummaryResponse,
    GlobalApplicationResponse,
    CompleteInterviewRoundRequest,
    GraphEdge,
    GraphJobNode,
    InterviewRoundRequest,
    InterviewRoundResponse,
    InviteApplicationRequest,
    RescheduleInterviewRoundRequest,
    JobLanguageRequirementRequest,
    JobResponse,
    JobSubmissionRequest,
    JobSummaryResponse,
    LanguageGateInfo,
    LoginRequest,
    LoginResponse,
    MatchResultResponse,
    ScoringConfigRequest,
    ScoringConfigResponse,
    StructuredJobDescription,
)
from jd_validation import ExtractionWarning, validate_and_normalize
from language_gate import BLOCKING_STATUSES, compute_language_gate
from scoring import CandidateScoringInput, JobRequirements, ScoringWeights, score_candidate
from shared.llm import LLMProvider, LLMProviderError
from shared.observability import CorrelationIdMiddleware, configure_json_logging
from shared.schemas import WebhookPayload
from voicehr_client import VoiceHRClientError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_api_key: str = "changeme-recruitment-workflow-key"
    second_brain_api_key: str = "changeme-second-brain-key"
    second_brain_url: str = "http://second-brain:8000"
    resume_intelligence_api_key: str = "changeme-resume-intelligence-key"
    resume_intelligence_url: str = "http://resume-intelligence:8000"
    voicehr_api_key: str = "changeme-voicehr-key"
    voicehr_url: str = "http://voicehr:8000"
    resend_api_key: Optional[str] = None
    resend_from_address: str = "Company Second Brain <onboarding@resend.dev>"
    webhook_hmac_secret: str = "changeme-webhook-secret"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    embedding_model_version: str = "gemini-embedding-001"
    jwt_secret: str = "changeme-jwt-secret"
    jwt_expiry_minutes: int = 45
    log_level: str = "INFO"


settings = Settings()
configure_json_logging("recruitment-workflow", settings.log_level)

app = FastAPI(title="Recruitment Workflow Service")
app.add_middleware(CorrelationIdMiddleware)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def require_service_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if x_api_key != settings.service_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing service API key")


def require_recruiter(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> Recruiter:
    """
    Recruiter-facing auth: a short-lived JWT in the Authorization: Bearer header.

    Deliberately independent of require_service_key -- this reads a different header
    (Authorization vs X-Api-Key) and verifies against a different secret (JWT_SECRET vs
    SERVICE_API_KEY), so a service API key can never satisfy this check and a recruiter
    JWT can never satisfy require_service_key.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token, settings.jwt_secret)
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    try:
        recruiter_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token payload")

    recruiter = get_recruiter_by_id(db, recruiter_id)
    if recruiter is None:
        raise HTTPException(status_code=401, detail="Recruiter not found")
    return recruiter


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


def get_llm_provider_optional() -> LLMProvider | None:
    """
    Best-effort variant of get_llm_provider for the advisory match-explanation path:
    an unavailable/misconfigured Gemini key must never break GET
    /v1/jobs/{job_id}/matches (which still returns valid, already-persisted
    MatchResults either way) the way it legitimately does break JD submission, which
    cannot proceed at all without structuring. Shares the same lazy singleton as
    get_llm_provider (reused once constructed by either entry point) but returns None
    instead of raising 503 on construction failure. Declared as its own top-level
    dependency (not a plain call to get_llm_provider) so tests can override it
    independently via app.dependency_overrides, the same way get_llm_provider is.
    """
    global _llm_provider
    if _llm_provider is None:
        from shared.llm import GeminiProvider
        try:
            _llm_provider = GeminiProvider(api_key=settings.gemini_api_key or None)
        except LLMProviderError:
            return None
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


async def repair_structured_extraction(
    llm: LLMProvider,
    raw_description: str,
    first_extraction: StructuredJobDescription,
    warnings: list[ExtractionWarning],
) -> StructuredJobDescription:
    """
    The one automatic repair attempt for a flagged extraction (see
    jd_validation.py's module docstring for why this lives here, not there).
    Called at most once per submission, only when validate_and_normalize's first
    pass returns "flagged" -- never a retry loop, never called again if this
    itself fails or the repaired result still validates badly (main.py's
    submit_job handles both by falling back to the original extraction).

    The prompt is deliberately a *targeted fix*, not a re-structuring from
    scratch: it gives Gemini the original JD, exactly what it extracted the
    first time, and the exact deterministic warnings that extraction triggered,
    then asks it to correct only those specific problems. This is meant to
    reduce the odds of the second attempt introducing a new, different problem
    that a from-scratch re-extraction risks.
    """
    warning_lines = "\n".join(f"- ({w.field}) {w.message}" for w in warnings)
    prompt = (
        "You previously extracted structured hiring requirements from the job "
        "description below, but automated validation found specific problems "
        "with that extraction. Fix ONLY the problems listed -- do not change "
        "anything else, and do not invent requirements the job description "
        "does not actually support.\n\n"
        f"Job description:\n{raw_description}\n\n"
        "Your first extraction:\n"
        f"required_skills: {first_extraction.required_skills}\n"
        f"preferred_skills: {first_extraction.preferred_skills}\n"
        f"min_years_experience: {first_extraction.min_years_experience}\n"
        f"titles: {first_extraction.titles}\n\n"
        f"Validation problems to fix:\n{warning_lines}\n\n"
        "Return a corrected extraction in the same structure."
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


@app.post("/v1/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """
    Recruiter login. One seeded account, no registration system (per architecture).
    Returns a short-lived JWT (JWT_EXPIRY_MINUTES) -- never a service API key, and this
    endpoint never accepts or checks X-Api-Key.
    """
    recruiter = get_recruiter_by_email(db, payload.email)
    if recruiter is None or not verify_password(payload.password, recruiter.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token, expires_at = create_access_token(
        recruiter.id, recruiter.email, settings.jwt_secret, settings.jwt_expiry_minutes
    )
    return LoginResponse(access_token=token, expires_at=expires_at)


# ---------------------------------------------------------------------------
# Recruiter-facing resume upload -- JWT here, service key onward to Resume Intelligence
#
# Architecture: admin/recruiter uploads always go through RWS, never directly to
# Resume Intelligence. This endpoint is the JWT-protected front door; it forwards the
# file to Resume Intelligence using RWS's own service-to-service API key (never sent
# to or exposed in the recruiter's browser). Resume Intelligence itself never sees or
# validates the recruiter's JWT -- it only checks the service key, exactly as it does
# for every other caller. The same Resume Intelligence endpoint is deliberately left
# suitable for a future trusted caller (e.g. an email-ingestion workflow) to use the
# same way, with its own service-key credential -- no new auth surface needed there.
# ---------------------------------------------------------------------------

@app.post("/v1/resumes", status_code=status.HTTP_201_CREATED)
async def upload_candidate_resume(
    file: UploadFile = File(...),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Recruiter-facing resume upload. Requires a recruiter JWT (Authorization: Bearer);
    a service API key does not satisfy this -- require_recruiter reads a different
    header and secret entirely (see its docstring).

    Resume Intelligence's own response is preserved as-is rather than collapsed into
    one generic outcome: its 2xx body (candidate_id/is_new) and its 4xx status
    (e.g. 422 for an unsupported file type) both pass through unchanged, since those
    are meaningful to the recruiter client. A Resume Intelligence 5xx, or the service
    being unreachable at all, is reported as 502 -- an upstream integration failure,
    not a problem with the upload itself.
    """
    content = await file.read()
    try:
        resp = await resume_intelligence_client.upload_resume(
            settings.resume_intelligence_url,
            settings.resume_intelligence_api_key,
            file.filename or "resume",
            content,
            file.content_type or "application/octet-stream",
        )
    except ResumeIntelligenceClientError as exc:
        raise HTTPException(status_code=502, detail=f"Resume Intelligence unreachable: {exc}")

    if resp.status_code >= 500:
        raise HTTPException(status_code=502, detail="Resume Intelligence upload failed")
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", "Resume upload rejected")
        except ValueError:
            detail = "Resume upload rejected"
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.json()


@app.get("/v1/jobs", response_model=list[JobSummaryResponse])
def get_jobs(
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Recruiter-facing: a lightweight, newest-first list of every job, for the jobs list
    page. Deliberately not paginated/filterable/searchable -- this project's current
    scale doesn't need it (see FRONTEND_HANDOFF.md); revisit only if a real dataset
    size makes the full list impractical. match_count comes from a single grouped
    query (list_jobs_with_match_counts), not an N+1 loop.
    """
    return [
        JobSummaryResponse(
            job_id=job.id,
            title=job.title,
            created_at=job.created_at,
            match_count=match_count,
            required_language=job.required_language,
            min_language_cefr=job.min_language_cefr,
            # Defensive fallback only -- create_job_with_description always creates
            # both rows atomically, so extraction_status is never actually None from
            # the outer join below; matches the DB columns' own defaults if it ever were.
            extraction_status=extraction_status or "clean",
            extraction_warnings=extraction_warnings or [],
        )
        for job, extraction_status, extraction_warnings, match_count in list_jobs_with_match_counts(db)
    ]


@app.post("/v1/jobs", response_model=JobResponse, status_code=200)
async def submit_job(
    payload: JobSubmissionRequest,
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm_provider),
    recruiter: Recruiter = Depends(require_recruiter),
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

    Atomic, all-or-nothing (milestone 40, fixing a partial-write risk milestone
    39's reliability audit found): every DB write above -- create_job_with_
    description, every create_match_result, and the post-match anomaly's own
    update_job_description_extraction_metadata -- only `db.flush()`es, never
    commits, until the single `db.commit()` at the very end of this function,
    once every step has actually succeeded. If anything raises before that
    point (JD structuring, Second Brain retrieval, a candidate lookup mid-loop),
    FastAPI's error response is returned and get_db's own `finally: db.close()`
    discards the whole uncommitted transaction -- nothing is left half-written.
    Before this, a mid-loop Second Brain failure durably persisted a Job with
    only some of its MatchResults, with the client only ever seeing a 502 and
    no job_id to know it happened.
    """
    try:
        structured = await structure_job_description(llm, payload.raw_description)
    except LLMProviderError as exc:
        raise HTTPException(status_code=502, detail=f"JD structuring failed: {exc}")

    # Deterministic validation, immediately after structuring and before anything is
    # persisted or matched -- normalizes for clean storage/display and flags (never
    # blocks) an extraction that looks unreliable. See jd_validation.py's own module
    # docstring for why this never calls Gemini and never touches scoring.
    validation = validate_and_normalize(
        required_skills=structured.required_skills,
        preferred_skills=structured.preferred_skills,
        min_years_experience=structured.min_years_experience,
        titles=structured.titles,
    )

    # One automatic repair attempt if (and only if) the first pass was flagged --
    # never for an already-clean extraction (zero extra Gemini calls on the happy
    # path), and never more than once (no retry loop). A repair failure (LLM
    # unreachable, quota exhausted, malformed response -- generate_structured wraps
    # all of these in LLMProviderError) must never block job creation: the original,
    # already-normalized "flagged" extraction is what gets persisted and matched, the
    # exact same outcome as if no repair pass existed at all. Same fallback if the
    # repair attempt's own output still fails validation -- a repair that doesn't
    # actually fix anything is worth no more trust than the first attempt.
    extraction_status = validation.status
    extraction_warnings = validation.warnings
    normalized = validation.normalized

    if validation.status == "flagged":
        try:
            repaired_structured = await repair_structured_extraction(
                llm, payload.raw_description, structured, validation.warnings
            )
        except LLMProviderError:
            repaired_structured = None

        if repaired_structured is not None:
            repaired_validation = validate_and_normalize(
                required_skills=repaired_structured.required_skills,
                preferred_skills=repaired_structured.preferred_skills,
                min_years_experience=repaired_structured.min_years_experience,
                titles=repaired_structured.titles,
            )
            if repaired_validation.status == "clean":
                extraction_status = "repaired"
                extraction_warnings = repaired_validation.warnings
                normalized = repaired_validation.normalized
            # else: the repair attempt still validates badly -- fall through and
            # persist the original (pre-repair) normalized extraction as "flagged",
            # per this feature's own "persist original/normalized result as
            # flagged" rule. The failed repair attempt itself is discarded, not
            # persisted anywhere.

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
        required_skills=normalized.required_skills,
        preferred_skills=normalized.preferred_skills,
        min_years_experience=normalized.min_years_experience,
        titles=normalized.titles,
        structuring_model=settings.gemini_model,
        required_language=payload.required_language,
        min_language_cefr=payload.min_language_cefr,
        extraction_status=extraction_status,
        extraction_warnings=[w.to_dict() for w in extraction_warnings],
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
    # Scoring uses the same normalized requirements just persisted onto
    # JobDescription -- what was actually stored is exactly what's actually scored,
    # the same reproducibility guarantee every other field on MatchResult already has.
    job_requirements = JobRequirements(
        required_skills=normalized.required_skills,
        preferred_skills=normalized.preferred_skills,
        min_years_experience=normalized.min_years_experience,
        titles=normalized.titles,
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

    # Post-match anomaly detection (see match_anomaly.py) -- runs once, after every
    # MatchResult above is already flushed (not yet committed -- see this function's
    # own docstring). Purely observational: it only reads the outcomes already
    # computed above, never rescales/rescoring, never touches a MatchResult row,
    # never calls Gemini. A detected anomaly can only ever move extraction_status
    # toward "flagged" (never away from it, and never past it into some other
    # status) and only appends a warning that isn't already present -- see
    # merge_anomaly_warning's own idempotency contract.
    anomaly = detect_match_anomaly(
        [
            MatchOutcome(
                semantic_score=m.semantic_score,
                hard_gate_passed=m.hard_gate_passed,
                qualified=m.qualified,
            )
            for m in match_responses
        ]
    )
    if anomaly is not None:
        promoted_status, promoted_warnings = merge_anomaly_warning(
            extraction_status, extraction_warnings, anomaly
        )
        try:
            # A SAVEPOINT (not the outer transaction) around this one advisory
            # write: per this feature's own "must never block job creation" rule,
            # a failure here must not discard the job/MatchResults already
            # pending in the same still-open outer transaction below -- only this
            # one nested block rolls back on failure, via begin_nested()'s own
            # context-manager contract (implicit RELEASE on success, ROLLBACK TO
            # SAVEPOINT on exception).
            with db.begin_nested():
                update_job_description_extraction_metadata(
                    db,
                    job_description.id,
                    promoted_status,
                    [w.to_dict() for w in promoted_warnings],
                )
        except SQLAlchemyError:
            # Log for visibility and keep responding with the PRE-anomaly status/
            # warnings -- the response must always describe what will actually be
            # committed below, never a promotion that failed to write.
            logger.warning(
                "Failed to persist post-match anomaly warning for job_description %s",
                job_description.id,
                exc_info=True,
            )
        else:
            extraction_status, extraction_warnings = promoted_status, promoted_warnings

    # The one commit for the whole pipeline -- see this function's own docstring.
    db.commit()

    return JobResponse(
        job_id=job.id,
        job_description_id=job_description.id,
        version=job_description.version,
        title=job.title,
        required_skills=normalized.required_skills,
        preferred_skills=normalized.preferred_skills,
        min_years_experience=normalized.min_years_experience,
        titles=normalized.titles,
        threshold=job.threshold,
        scoring_config_version=scoring_config.version,
        required_language=job.required_language,
        min_language_cefr=job.min_language_cefr,
        extraction_status=extraction_status,
        extraction_warnings=[w.to_dict() for w in extraction_warnings],
        matches=match_responses,
    )


@app.patch("/v1/jobs/{job_id}/language-requirement", response_model=JobSummaryResponse)
def update_job_language_requirement(
    job_id: uuid.UUID,
    payload: JobLanguageRequirementRequest,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Configure (or clear, by sending both fields null) an existing job's language
    eligibility requirement -- the one write path for jobs created before this
    feature existed, or whose requirement needs correcting. Deliberately does not
    touch any Application already created for this job: each one keeps the
    requirement snapshot it took at its own selection time (see
    db.create_application's docstring and language_gate.py) -- editing a job here
    can never silently change an already-reported gate result.
    """
    job = set_job_language_requirement(
        db, job_id, payload.required_language, payload.min_language_cefr
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    match_count = len(list_match_results_for_job(db, job_id))
    job_description = get_job_description_for_job(db, job_id)
    return JobSummaryResponse(
        job_id=job.id,
        title=job.title,
        created_at=job.created_at,
        match_count=match_count,
        required_language=job.required_language,
        min_language_cefr=job.min_language_cefr,
        extraction_status=job_description.extraction_status if job_description else "clean",
        extraction_warnings=job_description.extraction_warnings if job_description else [],
    )


@app.get("/v1/jobs/{job_id}/matches", response_model=list[MatchResultResponse])
async def get_job_matches(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
    llm: LLMProvider | None = Depends(get_llm_provider_optional),
):
    """
    Recruiter-facing: browse persisted MatchResults for a job before selecting one.

    Each match's advisory explanation is generated lazily, at most once, on the first
    read that needs it -- get_or_create_explanation checks match_explanations first and
    only calls Gemini on a cache miss, so repeated reads never call Gemini again. A
    missing/unreachable Gemini key never fails this endpoint -- the already-persisted
    scores/qualification are always returned regardless; only `explanation` comes back
    null in that case.
    """
    if get_job(db, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    matches = list_match_results_for_job(db, job_id)

    responses = []
    for m in matches:
        if llm is not None:
            explanation = await get_or_create_explanation(db, llm, m, settings.gemini_model)
        else:
            cached = get_match_explanation(db, m.id)
            explanation = cached.explanation if cached else None
        responses.append(
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
                explanation=explanation,
            )
        )
    return responses


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


# ---------------------------------------------------------------------------
# Candidate selection + recruiter decisions
#
# State machine: a MatchResult with qualified=True can be selected once, creating an
# Application in the 'selected' state. From there a recruiter accepts or rejects it --
# both are terminal. Future VoiceHR integration will insert invited/interviewed states
# between selection and decision; not implemented yet, per current scope.
# ---------------------------------------------------------------------------

def _language_gate_info(application) -> LanguageGateInfo:
    """Shared by _application_response (display) and accept_application (enforcement)
    -- see language_gate.py's module docstring for why both must call the same
    function on the same data rather than risk two independently-derived answers."""
    assessment = application.interview_assessment or {}
    status = compute_language_gate(
        required_language=application.required_language,
        required_cefr=application.required_cefr,
        has_interview_result=application.interview_assessment is not None,
        assessed_language=assessment.get("language"),
        assessed_cefr=assessment.get("cefr_level"),
    )
    return LanguageGateInfo(
        status=status,
        required_language=application.required_language,
        required_cefr=application.required_cefr,
        assessed_language=assessment.get("language"),
        assessed_cefr=assessment.get("cefr_level"),
    )


def _application_response(application, db: Session) -> ApplicationResponse:
    """
    One query per call for interview_rounds -- fine at this project's demo scale
    (a handful of applications total, per handoff.md's "no infra ahead of
    demonstrated need" rule), the same tradeoff already accepted elsewhere in this
    file rather than a premature batched-fetch abstraction for a list this small.
    """
    rounds = list_interview_rounds_for_application(db, application.id)
    return ApplicationResponse(
        application_id=application.id,
        job_id=application.job_id,
        match_result_id=application.match_result_id,
        candidate_id=application.candidate_id,
        status=application.status,
        selected_at=application.selected_at,
        invite_attempt_number=application.invite_attempt_number,
        invited_at=application.invited_at,
        interview_status=application.interview_status,
        interview_assessment=application.interview_assessment,
        interview_completed_at=application.interview_completed_at,
        decided_at=application.decided_at,
        language_gate=_language_gate_info(application),
        # Reuses _interview_round_response (defined below) rather than duplicating
        # its field list a second time -- two independently-drifting copies is
        # exactly the class of bug an added field (e.g. milestone 41's
        # notification_status/interviewer_email) could otherwise silently miss here.
        interview_rounds=[_interview_round_response(r) for r in rounds],
    )


@app.post(
    "/v1/matches/{match_id}/select", response_model=ApplicationResponse, status_code=201
)
def select_candidate(
    match_id: uuid.UUID,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Recruiter selects a qualified candidate to move forward, creating an Application.
    Only qualified matches can be selected -- the hard gate and threshold decision made
    at scoring time is not second-guessed here. One application per (job, candidate):
    a duplicate selection (including a concurrent one -- see the applications_job_candidate_unique
    constraint) is rejected as a conflict, not silently merged.
    """
    match = get_match_result(db, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if not match.qualified:
        raise HTTPException(
            status_code=422, detail="Cannot select a candidate that did not qualify"
        )

    # Snapshot the job's language requirement (if any) onto the new Application right
    # now -- not read live from `jobs` later, since a job's requirement is mutable
    # (see the PATCH .../language-requirement endpoint) and this snapshot is what
    # keeps an already-reported gate result from silently changing under a later edit.
    job = get_job(db, match.job_id)

    try:
        application = create_application(
            db,
            job_id=match.job_id,
            match_result_id=match.id,
            candidate_id=match.candidate_id,
            selected_by=recruiter.id,
            required_language=job.required_language if job else None,
            required_cefr=job.min_language_cefr if job else None,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="This candidate has already been selected for this job"
        )

    return _application_response(application, db)


async def _send_candidate_feedback_background(
    candidate_id: uuid.UUID,
    application_id: uuid.UUID,
    decision: str,
    job_title: str,
    matched_requirements: list[str],
    missing_requirements: list[str],
    final_score: float,
    decided_at,
) -> None:
    """
    Fire-and-forget knowledge feedback write-back to Second Brain, run as a FastAPI
    BackgroundTask *after* the recruiter's accept/reject HTTP response has already been
    sent -- the decision itself is already durably persisted in `applications` and
    `recruiter_decisions` by that point, so this can never block or fail the recruiter's
    request. A failure here is logged and otherwise swallowed: Second Brain being
    unreachable is not this request's problem, and there is no caller left to report it
    to. Idempotent on the receiving end (upsert on application_id), so this is safe to
    reason about as "at least once, never causes duplicates."
    """
    try:
        await clients.send_candidate_feedback(
            settings.second_brain_url,
            settings.second_brain_api_key,
            candidate_id=candidate_id,
            application_id=application_id,
            decision=decision,
            job_title=job_title,
            matched_requirements=matched_requirements,
            missing_requirements=missing_requirements,
            final_score=final_score,
            decided_at=decided_at,
        )
    except SecondBrainClientError:
        logger.warning(
            "Failed to deliver candidate feedback to Second Brain for application %s",
            application_id,
            exc_info=True,
        )


def _decide(
    db: Session,
    application_id: uuid.UUID,
    recruiter: Recruiter,
    decision: str,
    background_tasks: BackgroundTasks,
) -> ApplicationResponse:
    existing = get_application(db, application_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Application not found")

    updated = transition_application(db, application_id, recruiter.id, decision)
    if updated is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Application is not in a decidable state "
                f"(current status: {existing.status})"
            ),
        )

    # The state machine transition above is already committed and immutable regardless
    # of what happens next. The knowledge feedback write-back is a side effect of that
    # fact, not a precondition for it -- so it runs after the response, not before.
    match = get_match_result(db, updated.match_result_id)
    job = get_job(db, updated.job_id)
    background_tasks.add_task(
        _send_candidate_feedback_background,
        candidate_id=updated.candidate_id,
        application_id=updated.id,
        decision=updated.status,
        job_title=job.title,
        matched_requirements=match.matched_requirements,
        missing_requirements=match.missing_requirements,
        final_score=match.final_score,
        decided_at=updated.decided_at,
    )

    return _application_response(updated, db)


@app.post("/v1/applications/{application_id}/accept", response_model=ApplicationResponse)
def accept_application(
    application_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Terminal transition: selected/invited/interviewed -> accepted. Recorded in
    recruiter_decisions, then triggers an async knowledge feedback write-back.

    Gated on the VoiceHR language eligibility gate (see language_gate.py) when the
    job this application belongs to has a requirement: "pending" (interview not
    finished), "failed" (assessed CEFR below the requirement), and "needs_review"
    (missing/invalid/mismatched assessment data) all block acceptance with a 422
    naming the reason; only "not_required" (no requirement configured) or "passed"
    proceed to the state-machine transition below. This is deliberately checked only
    here, never in reject_application -- a recruiter can always say no regardless of
    the gate, and this is a resume-scoring-independent second-stage screen, not a
    change to MatchResult/qualified (see CLAUDE.md, this module's own scoring rules).
    """
    application = get_application(db, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")

    gate = _language_gate_info(application)
    if gate.status in BLOCKING_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot accept: language screening is {gate.status} "
                f"(required {gate.required_cefr} {gate.required_language}, "
                f"assessed {gate.assessed_cefr or 'none'} {gate.assessed_language or 'none'})"
            ),
        )

    return _decide(db, application_id, recruiter, "accept", background_tasks)


@app.post("/v1/applications/{application_id}/reject", response_model=ApplicationResponse)
def reject_application(
    application_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """Terminal transition: selected/invited/interviewed -> rejected. Recorded in
    recruiter_decisions, then triggers an async knowledge feedback write-back."""
    return _decide(db, application_id, recruiter, "reject", background_tasks)


# ---------------------------------------------------------------------------
# Interview rounds -- post-VoiceHR technical/managerial interview stage
#
# A parallel, one-to-many signal beside Application's own state machine, the same
# relationship the language gate already has to it (see db.InterviewRound's own
# docstring). Creation is gated on the application/language-gate state (see
# create_interview_round_endpoint); the three lifecycle actions below are gated
# only on the round's own status ('scheduled' -> 'completed' | 'cancelled', a
# terminal choice either way) -- neither touches Application.status, MatchResult,
# or the language gate.
# ---------------------------------------------------------------------------

def _interview_round_response(round_) -> InterviewRoundResponse:
    return InterviewRoundResponse(
        id=round_.id,
        application_id=round_.application_id,
        round_type=round_.round_type,
        status=round_.status,
        outcome=round_.outcome,
        interviewer_name=round_.interviewer_name,
        interviewer_email=round_.interviewer_email,
        location=round_.location,
        scheduled_at=round_.scheduled_at,
        notes=round_.notes,
        notification_status=round_.notification_status,
        notification_error=round_.notification_error,
        notification_sequence=round_.notification_sequence,
        created_by=round_.created_by,
        created_at=round_.created_at,
        updated_at=round_.updated_at,
    )


def _get_own_interview_round(db: Session, application_id: uuid.UUID, round_id: uuid.UUID):
    """
    Fetches a round and confirms it belongs to the given application -- a round
    that exists but under a different application_id in the URL is treated as
    "not found" here, the same as a round_id that doesn't exist at all, rather
    than leaking whether a mismatched round_id exists somewhere else.
    """
    round_ = get_interview_round(db, round_id)
    if round_ is None or round_.application_id != application_id:
        return None
    return round_


async def _notify_interview_round(
    db: Session, application, round_, kind: str, sequence: int
) -> tuple[str, Optional[str]]:
    """
    Attempts the candidate+interviewer email+ICS notification for one interview
    round -- shared by create/reschedule/cancel (all best-effort, never block their
    own request) and resend_interview_round_notification_endpoint (its entire
    purpose, re-raised as the request's own failure on error -- see that endpoint's
    own docstring for why it treats the same outcome differently). Returns
    (notification_status, notification_error) without persisting either -- callers
    decide when to call update_interview_round_notification, since they persist and
    respond in a different order.

    `kind` ("scheduled"/"rescheduled"/"cancelled") controls both the email wording
    and the ICS's own METHOD/STATUS (interview_round_mailer.py); `sequence` is the
    ICS SEQUENCE to render -- the caller (create/reschedule/cancel) is responsible
    for deciding whether it needs to increment relative to the round's current
    notification_sequence (see db.InterviewRound's own docstring: reschedule/cancel
    do, a plain resend of unchanged content does not).

    The candidate's contact is fetched fresh from Resume Intelligence for this one
    call and never persisted anywhere in RWS -- the same transient-lookup pattern
    invite_application already uses. A missing contact is treated the same as any
    other notification failure (recorded, never raised as this function's own
    exception), since "we don't know how to reach the candidate" is just one more
    reason a best-effort notification can fail.
    """
    job = get_job(db, application.job_id)
    try:
        contact = await resume_intelligence_client.get_contact(
            settings.resume_intelligence_url,
            settings.resume_intelligence_api_key,
            application.candidate_id,
        )
        if contact is None:
            raise ResumeIntelligenceClientError("Candidate contact not found")
        if not contact.get("email"):
            # Same missing-data case invite_application reports as a 422 -- here it
            # is just one more reason a best-effort notification can't be delivered.
            raise ResumeIntelligenceClientError("Candidate has no email address on file")
        await interview_round_mailer.send_interview_round_notification(
            api_key=settings.resend_api_key,
            from_address=settings.resend_from_address,
            kind=kind,
            round_id=str(round_.id),
            sequence=sequence,
            candidate_email=contact["email"],
            candidate_name=contact["name"],
            interviewer_email=round_.interviewer_email,
            interviewer_name=round_.interviewer_name,
            job_title=job.title if job else "the role",
            round_type=round_.round_type,
            scheduled_at=round_.scheduled_at,
            location=round_.location,
        )
    except (ResumeIntelligenceClientError, NotificationDeliveryError) as exc:
        return "failed", str(exc)
    return "sent", None


@app.post(
    "/v1/applications/{application_id}/interview-rounds",
    response_model=InterviewRoundResponse,
    status_code=201,
)
async def create_interview_round_endpoint(
    application_id: uuid.UUID,
    payload: InterviewRoundRequest,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Schedule a new technical/managerial interview round. Two independent gates,
    both reusing checks that already exist rather than duplicating logic:

    1. The application must not already be accepted/rejected -- a terminal
       decision, after which nothing further should be scheduled.
    2. The language gate must not be blocking (same BLOCKING_STATUSES/
       _language_gate_info accept_application already uses) -- this stage is
       explicitly "for candidates who pass the language gate" (handoff.md), not a
       parallel path that could let a still-screening candidate through.

    Application.status itself is never touched here -- only a new interview_rounds
    row is created; see db.InterviewRound's own docstring for why.

    Phase 1 of automating human interview scheduling (handoff.md): once the round
    is created (and only once -- see create_interview_round's own docstring for why
    that commit happens first), best-effort notifies the candidate and interviewer
    by email via interview_round_mailer. The candidate's contact is fetched fresh
    from Resume Intelligence for this one call and never persisted anywhere in RWS,
    the same transient-lookup pattern invite_application already uses. A failure at
    either step (contact lookup or email delivery) is recorded as
    notification_status='failed' with the real error and does NOT roll back or fail
    this request -- the round itself is the durable, valuable result; notification
    is advisory on top of it, the same "observational, never blocking" relationship
    jd_validation/match_anomaly already have to job submission.
    """
    application = get_application(db, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")

    if application.status in ("accepted", "rejected"):
        raise HTTPException(
            status_code=422,
            detail=f"Cannot schedule an interview round: application is already {application.status}",
        )

    gate = _language_gate_info(application)
    if gate.status in BLOCKING_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot schedule an interview round: language screening is {gate.status} "
                f"(required {gate.required_cefr} {gate.required_language}, "
                f"assessed {gate.assessed_cefr or 'none'} {gate.assessed_language or 'none'})"
            ),
        )

    round_ = create_interview_round(
        db,
        application_id=application_id,
        round_type=payload.round_type,
        interviewer_name=payload.interviewer_name,
        interviewer_email=payload.interviewer_email,
        location=payload.location,
        scheduled_at=payload.scheduled_at,
        notes=payload.notes,
        created_by=recruiter.id,
    )

    # A freshly created round's first send is never itself a "change" to render
    # as an update -- ICS SEQUENCE starts at the column's own default (0).
    notification_status, notification_error = await _notify_interview_round(
        db, application, round_, kind="scheduled", sequence=round_.notification_sequence
    )
    round_ = update_interview_round_notification(db, round_.id, notification_status, notification_error)
    return _interview_round_response(round_)


@app.post(
    "/v1/applications/{application_id}/interview-rounds/{round_id}/reschedule",
    response_model=InterviewRoundResponse,
)
async def reschedule_interview_round_endpoint(
    application_id: uuid.UUID,
    round_id: uuid.UUID,
    payload: RescheduleInterviewRoundRequest,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Update a round's scheduled_at. Only from 'scheduled' -- a completed or
    cancelled round is a closed record, not something to move around.

    Phase 2 (handoff.md): a real reschedule genuinely changes the event, so this
    best-effort notifies both parties with an updated ICS -- same UID, SEQUENCE
    incremented by one (RFC 5545's own mechanism for "this replaces the last one
    I sent you", not a duplicate). Same "never blocks the underlying action"
    contract creation already has: a notification failure here never reverts the
    new scheduled_at or fails this request, it only shows up in the response's
    own notification_status/notification_error.
    """
    existing = _get_own_interview_round(db, application_id, round_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Interview round not found")

    updated = reschedule_interview_round(db, round_id, payload.scheduled_at)
    if updated is None:
        raise HTTPException(
            status_code=409,
            detail=f"Interview round is not reschedulable (current status: {existing.status})",
        )

    application = get_application(db, application_id)
    new_sequence = updated.notification_sequence + 1
    notification_status, notification_error = await _notify_interview_round(
        db, application, updated, kind="rescheduled", sequence=new_sequence
    )
    updated = update_interview_round_notification(
        db, round_id, notification_status, notification_error, notification_sequence=new_sequence
    )
    return _interview_round_response(updated)


@app.post(
    "/v1/applications/{application_id}/interview-rounds/{round_id}/complete",
    response_model=InterviewRoundResponse,
)
def complete_interview_round_endpoint(
    application_id: uuid.UUID,
    round_id: uuid.UUID,
    payload: CompleteInterviewRoundRequest,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Record a round's outcome, only from 'scheduled'. `outcome` is required here
    (Literal["pass", "fail", "needs_review"]) -- the request schema itself rejects
    a missing/invalid value with a 422 before this ever reaches the DB, matching
    (not just relying on) migration 0007's paired outcome/status CHECK constraint.
    `notes`, if omitted, leaves whatever was set at scheduling time untouched
    rather than clobbering it with a null.
    """
    existing = _get_own_interview_round(db, application_id, round_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Interview round not found")

    updated = complete_interview_round(db, round_id, payload.outcome, payload.notes)
    if updated is None:
        raise HTTPException(
            status_code=409,
            detail=f"Interview round is not completable (current status: {existing.status})",
        )
    return _interview_round_response(updated)


@app.post(
    "/v1/applications/{application_id}/interview-rounds/{round_id}/cancel",
    response_model=InterviewRoundResponse,
)
async def cancel_interview_round_endpoint(
    application_id: uuid.UUID,
    round_id: uuid.UUID,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Cancel a round, only from 'scheduled'. Terminal, like 'completed' -- no
    un-cancel; schedule a new round instead if one is still needed.

    Phase 2 (handoff.md): best-effort notifies both parties with a `METHOD:CANCEL`
    ICS on the same UID, SEQUENCE incremented once more -- the standard mechanism
    a real calendar client uses to remove an event it already added, rather than
    leaving a stale entry behind. Same "never blocks" contract as create/
    reschedule: a notification failure here never un-cancels the round.
    """
    existing = _get_own_interview_round(db, application_id, round_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Interview round not found")

    updated = cancel_interview_round(db, round_id)
    if updated is None:
        raise HTTPException(
            status_code=409,
            detail=f"Interview round is not cancellable (current status: {existing.status})",
        )

    application = get_application(db, application_id)
    new_sequence = updated.notification_sequence + 1
    notification_status, notification_error = await _notify_interview_round(
        db, application, updated, kind="cancelled", sequence=new_sequence
    )
    updated = update_interview_round_notification(
        db, round_id, notification_status, notification_error, notification_sequence=new_sequence
    )
    return _interview_round_response(updated)


# In-memory per-round cooldown for explicit interview-round notification resends --
# a single-instance deployment need (no Redis, per CLAUDE.md's infra constraints),
# the same pattern _resend_cooldowns already uses for POST .../resend-invite. Guards
# against a double-click triggering more than one real Resend send in quick
# succession; the 'scheduled'-only status check below is the real authority on
# *whether* a resend is ever allowed at all, this only throttles *how often*.
_INTERVIEW_ROUND_RESEND_COOLDOWN_SECONDS = 10.0
_interview_round_resend_cooldowns: dict[uuid.UUID, float] = {}


@app.post(
    "/v1/applications/{application_id}/interview-rounds/{round_id}/resend-notification",
    response_model=InterviewRoundResponse,
)
async def resend_interview_round_notification_endpoint(
    application_id: uuid.UUID,
    round_id: uuid.UUID,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Explicit resend of an interview round's scheduling notification -- always
    attempts delivery (no "already sent" check, unlike create's first attempt).
    Never creates a new round or a new interview_rounds row: reuses this exact
    round's current interviewer_email/location/scheduled_at, the same way
    resend-invite reuses VoiceHR's existing interview rather than creating a new
    one. Only callable while 'scheduled' -- the same status the other lifecycle
    actions (reschedule/complete/cancel) already require, since resending a
    scheduling notification for an already-completed or cancelled round would
    describe a meeting that either already happened or no longer exists.

    Unlike create_interview_round_endpoint (where notification failure never blocks
    the request, because the round's own existence is the durable, valuable
    result), this endpoint's entire purpose *is* the notification -- so a failure
    here is raised as this request's own 502, the same relationship
    POST .../resend-invite already has to a VoiceHR delivery failure. The round's
    notification_status/notification_error are still updated to reflect the real
    outcome either way, so a later read (e.g. the Applications list) never shows a
    stale status from before this attempt.
    """
    round_ = _get_own_interview_round(db, application_id, round_id)
    if round_ is None:
        raise HTTPException(status_code=404, detail="Interview round not found")
    if round_.status != "scheduled":
        raise HTTPException(
            status_code=409,
            detail=f"Interview round is not resendable (current status: {round_.status})",
        )

    now = time.monotonic()
    last_attempt = _interview_round_resend_cooldowns.get(round_id)
    if last_attempt is not None and (now - last_attempt) < _INTERVIEW_ROUND_RESEND_COOLDOWN_SECONDS:
        raise HTTPException(
            status_code=429,
            detail="Resend already requested; please wait a moment before trying again",
        )
    # Recorded before the notification attempt (not after), the same reasoning
    # resend-invite's own cooldown already uses -- so two near-simultaneous clicks
    # can't both pass the check while the first is still in flight.
    _interview_round_resend_cooldowns[round_id] = now

    application = get_application(db, application_id)
    # A plain resend re-delivers the round's *current* confirmed state -- never
    # itself a change, so it always uses "scheduled" wording and the round's
    # existing SEQUENCE unchanged (see interview_round_mailer.py's own docstring).
    notification_status, notification_error = await _notify_interview_round(
        db, application, round_, kind="scheduled", sequence=round_.notification_sequence
    )
    updated = update_interview_round_notification(db, round_id, notification_status, notification_error)

    if notification_status == "failed":
        raise HTTPException(
            status_code=502, detail=f"Interview round notification failed: {notification_error}"
        )
    return _interview_round_response(updated)


@app.get("/v1/jobs/{job_id}/applications", response_model=list[ApplicationResponse])
def get_job_applications(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """Recruiter-facing: review selections and decisions made so far for a job."""
    if get_job(db, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return [_application_response(a, db) for a in list_applications_for_job(db, job_id)]


@app.get("/v1/applications", response_model=list[GlobalApplicationResponse])
def get_all_applications(
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Recruiter-facing: every application across every job, newest-selection-first --
    the global counterpart to GET /v1/jobs/{job_id}/applications, for a VoiceHR/
    interview-list view that isn't scoped to one job (see FRONTEND_HANDOFF.md's
    Candidates-page backend prerequisites). Reuses _application_response for every
    ApplicationResponse field and adds only job_title, via list_all_applications'
    single JOIN query -- no N+1 per-application job lookup.
    """
    return [
        GlobalApplicationResponse(
            **_application_response(application, db).model_dump(),
            job_title=job_title,
        )
        for application, job_title in list_all_applications(db)
    ]


# ---------------------------------------------------------------------------
# Candidate identity -- recruiter-facing, transient proxy to Resume Intelligence
#
# RWS's own data is PII-free (candidate_id only); this is the one endpoint that hands
# the recruiter UI a display name, so a ranked-candidates table isn't just a list of
# UUIDs. It reuses the exact same resume_intelligence_client.get_contact() call
# invite_application already makes server-side -- fetched fresh per request, never
# written to any RWS table. Only name/email are returned: no phone, no raw resume
# text, no candidate_json (see schemas.CandidateIdentityResponse).
# ---------------------------------------------------------------------------

@app.get(
    "/v1/candidates/{candidate_id}/identity",
    response_model=CandidateIdentityResponse,
)
async def get_candidate_identity(
    candidate_id: uuid.UUID,
    recruiter: Recruiter = Depends(require_recruiter),
):
    """Recruiter-facing candidate display identity. Requires a recruiter JWT -- a
    service API key does not satisfy this (require_recruiter reads a different header
    and secret than require_service_key)."""
    try:
        contact = await resume_intelligence_client.get_contact(
            settings.resume_intelligence_url,
            settings.resume_intelligence_api_key,
            candidate_id,
        )
    except ResumeIntelligenceClientError as exc:
        # Only the exception type and the (non-PII) candidate_id are logged -- never
        # the exception message, which can in principle wrap response text from
        # upstream. The client-facing detail is generic for the same reason: this is a
        # dependency failure, not something about the request itself worth detailing.
        logger.error(
            "candidate_identity_lookup_failed",
            extra={"error_type": type(exc).__name__, "candidate_id": str(candidate_id)},
        )
        raise HTTPException(status_code=502, detail="Resume Intelligence unavailable")

    if contact is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    return CandidateIdentityResponse(
        candidate_id=candidate_id,
        name=contact["name"],
        email=contact.get("email"),
    )


@app.get("/v1/candidates", response_model=list[CandidateSummaryResponse])
async def get_candidates(
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Recruiter-facing: every candidate ever ingested, newest first -- the Candidates-
    page backend prerequisite (see FRONTEND_HANDOFF.md). Transiently proxies Resume
    Intelligence's own GET /v1/candidates (service-key auth, never exposed to the
    browser) and returns it as-is, reshaped into this service's own response model.
    Declares no db dependency at all, the same structural proof of "RWS persists
    nothing from this call" already used by get_candidate_identity above.
    """
    try:
        candidates = await resume_intelligence_client.list_candidates(
            settings.resume_intelligence_url,
            settings.resume_intelligence_api_key,
        )
    except ResumeIntelligenceClientError as exc:
        # Only the exception type is logged, matching get_candidate_identity's own
        # PII-safe logging bar -- no candidate data, no upstream exception message.
        logger.error("candidates_list_lookup_failed", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=502, detail="Resume Intelligence unavailable")

    return [
        CandidateSummaryResponse(
            candidate_id=c["candidate_id"],
            name=c["name"],
            email=c.get("email"),
            created_at=c["created_at"],
        )
        for c in candidates
    ]


@app.get("/v1/candidates/{candidate_id}/knowledge", response_model=CandidateKnowledgeResponse)
async def get_candidate_knowledge(
    candidate_id: uuid.UUID,
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Recruiter-facing: a candidate's professional knowledge (skills/job titles/years
    of experience), for the Candidate Detail page. Transiently proxies Second
    Brain's own GET /v1/knowledge/candidates/{candidate_id} -- the exact same call
    POST /v1/jobs already makes per retrieved candidate during scoring, via
    clients.get_candidate(). Declares no db dependency at all, the same structural
    proof of "RWS persists nothing from this call" already used by
    get_candidate_identity/get_candidates above. Deliberately narrow: no
    `raw_text`, no `source_version`, no embedding -- see
    schemas.CandidateKnowledgeResponse's own docstring for why.
    """
    try:
        candidate = await clients.get_candidate(
            settings.second_brain_url, settings.second_brain_api_key, candidate_id
        )
    except SecondBrainClientError as exc:
        # Only the exception type and the (non-PII) candidate_id are logged --
        # never the exception message, matching get_candidate_identity's own bar.
        logger.error(
            "candidate_knowledge_lookup_failed",
            extra={"error_type": type(exc).__name__, "candidate_id": str(candidate_id)},
        )
        raise HTTPException(status_code=502, detail="Second Brain unavailable")

    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    return CandidateKnowledgeResponse(
        candidate_id=candidate_id,
        skills=candidate["skills"],
        job_titles=candidate["job_titles"],
        years_of_experience=candidate["years_of_experience"],
    )


# ---------------------------------------------------------------------------
# Candidate/job knowledge graph -- recruiter-facing, RWS-native (see the
# knowledge-graph design audit tracked in handoff.md). Unlike the identity/
# knowledge/candidates-list endpoints above, this one genuinely reads RWS's own
# Postgres (hence the `db` dependency) rather than proxying another service --
# there is nothing to proxy, since every field it returns already lives in the
# immutable MatchResult/Job rows scoring itself already persisted. No new
# persistence, no Second Brain call, no scoring recomputation: this is a pure
# read of the existing audit trail.
# ---------------------------------------------------------------------------

@app.get("/v1/graph/candidates-jobs", response_model=CandidateJobGraphResponse)
def get_candidates_jobs_graph(
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Every persisted MatchResult across every job, shaped as a candidate<->job graph:
    one job node per job that has at least one match (with its title), one edge per
    MatchResult (candidate_id, job_id, final_score, qualified, hard_gate_passed,
    matched/missing_requirements). Candidate nodes are deliberately not enumerated
    here -- RWS has no display-worthy candidate data of its own; the frontend
    derives the candidate-node set from edges[].candidate_id and resolves names via
    the already-existing GET /v1/candidates in a second call, not a proxy call this
    endpoint would otherwise need to make. Qualified and disqualified matches are
    both included, unfiltered -- see list_all_match_results's own docstring for why.
    One JOIN query, not an N+1 per-match job lookup.
    """
    rows = list_all_match_results(db)

    seen_job_ids: set[uuid.UUID] = set()
    jobs: list[GraphJobNode] = []
    edges: list[GraphEdge] = []
    for match, job_title in rows:
        if match.job_id not in seen_job_ids:
            seen_job_ids.add(match.job_id)
            jobs.append(GraphJobNode(job_id=match.job_id, title=job_title))
        edges.append(
            GraphEdge(
                match_id=match.id,
                candidate_id=match.candidate_id,
                job_id=match.job_id,
                final_score=match.final_score,
                qualified=match.qualified,
                hard_gate_passed=match.hard_gate_passed,
                matched_requirements=match.matched_requirements,
                missing_requirements=match.missing_requirements,
            )
        )

    return CandidateJobGraphResponse(jobs=jobs, edges=edges)


# ---------------------------------------------------------------------------
# Interview invitation -- RWS -> Resume Intelligence (contact lookup) -> VoiceHR
#
# The contact lookup is a hard pre-flight gate: VoiceHR is never called if it fails,
# since VoiceHR needs a real name and email to create an interview. See
# resume_intelligence_client.get_contact and voicehr_client.invite_candidate.
# ---------------------------------------------------------------------------

@app.post("/v1/applications/{application_id}/invite", response_model=ApplicationResponse)
async def invite_application(
    application_id: uuid.UUID,
    payload: InviteApplicationRequest,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Invite (or re-invite) an application's candidate to a VoiceHR interview.

    VoiceHR now owns emailing the candidate their secure interview link automatically,
    as part of the same call this makes -- RWS never sees or stores that link, and
    never sends the email itself (see voicehr_client.invite_candidate's own docstring
    and, on the VoiceHR side, backend/api/routes/invite.py's create_invite). A failed
    delivery is indistinguishable from any other VoiceHR failure from here: both raise
    VoiceHRClientError, both result in a 502 below, and both leave this application
    exactly as it was (never advanced to 'invited') since record_invite_attempt is
    never reached.

    Callable when the application is 'selected' (first invite) or 'invited' (resend --
    e.g. the candidate never received the link). invite_attempt_number is computed as
    current + 1 and is only persisted after VoiceHR responds successfully: if VoiceHR
    is called but its response never reaches RWS, the next call recomputes the same
    attempt number, so the same idempotency key is reused and VoiceHR's own idempotency
    check (not a new duplicate interview, not a duplicate email once delivery already
    succeeded once) is what protects the retry.
    """
    application = get_application(db, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if application.status not in ("selected", "invited"):
        raise HTTPException(
            status_code=409,
            detail=f"Application is not invitable (current status: {application.status})",
        )

    try:
        contact = await resume_intelligence_client.get_contact(
            settings.resume_intelligence_url,
            settings.resume_intelligence_api_key,
            application.candidate_id,
        )
    except ResumeIntelligenceClientError as exc:
        raise HTTPException(status_code=502, detail=f"Contact lookup failed: {exc}")
    if contact is None:
        raise HTTPException(
            status_code=422,
            detail="Candidate contact information not found; cannot invite to interview",
        )
    # A stored candidate can legitimately have no email (see
    # shared.schemas.ContactLookupResponse.email) -- that is a missing-data
    # condition the recruiter can act on, not an upstream failure, so it gets the
    # same explicit 422 as "no contact record at all" rather than being passed to
    # VoiceHR as a null address and coming back as an opaque 502.
    if not contact.get("email"):
        raise HTTPException(
            status_code=422,
            detail="Candidate has no email address on file; cannot invite to interview",
        )

    attempt_number = application.invite_attempt_number + 1
    idempotency_key = f"{application_id}:{attempt_number}"

    # If this job has a language requirement, VoiceHR conducts the interview in that
    # exact (snapshotted) language -- not whatever the request body says -- so the
    # eventual assessed_language can only ever match or genuinely mismatch the
    # requirement, never diverge because of an unrelated manual selection here. Jobs
    # with no requirement keep today's behavior: the recruiter picks the language.
    interview_language = application.required_language or payload.language

    # Cosmetic only (the candidate email's greeting, composed entirely by VoiceHR) --
    # never used for matching/scoring/eligibility, and no other field of Job is sent.
    job = get_job(db, application.job_id)

    try:
        await voicehr_client.invite_candidate(
            settings.voicehr_url,
            settings.voicehr_api_key,
            idempotency_key=idempotency_key,
            candidate_name=contact["name"],
            candidate_email=contact["email"],
            language=interview_language,
            external_reference_id=str(application_id),
            job_title=job.title if job else None,
        )
    except VoiceHRClientError as exc:
        # Covers both "VoiceHR unreachable" and "VoiceHR reached but could not deliver
        # the invitation email" (see voicehr_client.invite_candidate's own docstring)
        # -- either way, record_invite_attempt below is never reached, so the
        # application is correctly left exactly as it was (never marked invited).
        raise HTTPException(status_code=502, detail=f"VoiceHR invitation failed: {exc}")

    updated = record_invite_attempt(db, application_id, attempt_number)
    if updated is None:
        # The application's state changed between our check above and now (e.g. a
        # concurrent decision landed while the VoiceHR call was in flight). VoiceHR did
        # create the interview; RWS's own state just couldn't advance to reflect it.
        raise HTTPException(
            status_code=409,
            detail="Application state changed during invitation and could not be updated",
        )
    return _application_response(updated, db)


# In-memory per-application cooldown for explicit resend requests -- a single-instance
# deployment need (no Redis, per CLAUDE.md's infra constraints), not a persisted field
# or new workflow status. Guards against a double-click (or an impatient repeated
# click) triggering more than one real provider send in quick succession; RWS's own
# 'invited'-only status check above is the real authority on *whether* a resend is
# ever allowed at all, this only throttles *how often*.
_RESEND_COOLDOWN_SECONDS = 10.0
_resend_cooldowns: dict[uuid.UUID, float] = {}


@app.post("/v1/applications/{application_id}/resend-invite", response_model=ApplicationResponse)
async def resend_invite(
    application_id: uuid.UUID,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Explicit resend: intentionally re-sends the candidate's existing interview
    invitation email without creating a new interview and without changing this
    application's own state -- unlike POST /v1/applications/{application_id}/invite,
    which (from 'selected') creates a new attempt/interview, or (after a failed
    attempt, still 'selected') retries that same attempt via its own idempotency key.
    Only callable once already 'invited': the initial invite/retry path above is what
    gets an application to 'invited' in the first place, and is the only recovery
    path while the first delivery hasn't yet succeeded.

    Calls VoiceHR's own service-key-gated resend-by-external_reference_id endpoint
    (voicehr_client.resend_invite), which reuses the exact interview/token VoiceHR
    created at invite time -- RWS never sees or stores that token/link, and never
    looks up or persists the candidate's contact info here (VoiceHR already has it
    from the initial invite; see resume_intelligence_client.get_contact, only used by
    the initial invite path above).

    On failure, the application is left exactly as it was ('invited'): a resend
    failure is not a reason to lose the fact that the initial invitation already
    succeeded, and this task deliberately does not add a workflow status for a
    transient email failure (see language_gate.py / handoff.md for the project's
    existing "don't add a status for a derived/transient signal" precedent).
    """
    application = get_application(db, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if application.status != "invited":
        raise HTTPException(
            status_code=409,
            detail=f"Application is not in a resendable state (current status: {application.status})",
        )

    now = time.monotonic()
    last_attempt = _resend_cooldowns.get(application_id)
    if last_attempt is not None and (now - last_attempt) < _RESEND_COOLDOWN_SECONDS:
        raise HTTPException(
            status_code=429,
            detail="Resend already requested; please wait a moment before trying again",
        )
    # Recorded before the VoiceHR call (not after) so two near-simultaneous clicks --
    # the exact double-click case this guards against -- can't both pass the check
    # while the first is still in flight.
    _resend_cooldowns[application_id] = now

    try:
        await voicehr_client.resend_invite(
            settings.voicehr_url, settings.voicehr_api_key, str(application_id)
        )
    except VoiceHRClientError as exc:
        raise HTTPException(status_code=502, detail=f"VoiceHR resend failed: {exc}")

    return _application_response(application, db)


# ---------------------------------------------------------------------------
# Interview report / recording -- recruiter-facing, transient proxy to VoiceHR
#
# Neither route declares a response_model: the report body is VoiceHR's own rich,
# evolving shape (dimensions/strengths/concerns/evidence/stats/transcript), and
# reshaping it into a narrower RWS-owned contract would contradict the task this
# was built for ("show the actual available report fields from VoiceHR") -- the
# same reasoning already applied to POST /v1/resumes' passthrough of Resume
# Intelligence's response. Both routes call VoiceHR's new service-key-gated
# RWS-facing endpoints (never the Basic-auth admin surface), and persist nothing:
# neither declares any write to `db`, only the read needed to resolve
# application_id -> the interview VoiceHR knows by that same id
# (external_reference_id, set at invite time -- see invite_application above).
# ---------------------------------------------------------------------------

@app.get("/v1/interviews/{application_id}/report")
async def get_interview_report(
    application_id: uuid.UUID,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Full interview report for one application, fetched fresh from VoiceHR on every
    call -- never cached or persisted here. The recording field (if any) is
    rewritten to a same-origin RWS URL before returning; VoiceHR's own host, port,
    or credentials never reach the browser (see get_interview_recording below).
    """
    application = get_application(db, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")

    try:
        report = await voicehr_client.get_interview_report(
            settings.voicehr_url, settings.voicehr_api_key, str(application_id)
        )
    except VoiceHRClientError as exc:
        # Only the exception type and the (non-PII) application_id are logged --
        # never the exception message, which can in principle wrap upstream
        # response text (matches get_candidate_identity's own logging bar).
        logger.error(
            "interview_report_lookup_failed",
            extra={"error_type": type(exc).__name__, "application_id": str(application_id)},
        )
        raise HTTPException(status_code=502, detail="VoiceHR unavailable")

    if report is None:
        raise HTTPException(status_code=404, detail="Report not available")

    if report.get("recording", {}).get("available"):
        report["recording"] = {"available": True, "url": f"/v1/interviews/{application_id}/recording"}
    else:
        report["recording"] = {"available": False, "url": None}
    return report


@app.get("/v1/interviews/{application_id}/recording")
async def get_interview_recording(
    application_id: uuid.UUID,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """
    Streams one interview's recording audio through RWS, so the browser never
    needs to know VoiceHR's own origin or send it any credential. Fetched fresh
    from VoiceHR on every call and relayed directly -- never written to disk or a
    database on RWS's side.
    """
    application = get_application(db, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")

    try:
        recording = await voicehr_client.get_interview_recording(
            settings.voicehr_url, settings.voicehr_api_key, str(application_id)
        )
    except VoiceHRClientError as exc:
        logger.error(
            "interview_recording_lookup_failed",
            extra={"error_type": type(exc).__name__, "application_id": str(application_id)},
        )
        raise HTTPException(status_code=502, detail="VoiceHR unavailable")

    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not available")

    return Response(
        content=recording.content,
        media_type=recording.headers.get("content-type", "application/octet-stream"),
    )


# ---------------------------------------------------------------------------
# VoiceHR -> RWS interview-result webhook
#
# Authenticated by HMAC-SHA256 signature only (X-Signature) -- deliberately not
# require_service_key or require_recruiter, per the architecture's three distinct auth
# mechanisms (recruiter JWT, service API keys, webhook signatures). X-Delivery-Id is the
# dedup key: a duplicate delivery is rejected by interview_webhook_events' UNIQUE
# constraint before any application state is touched, so a duplicate can never be
# double-applied even under a true race between two deliveries of the same event.
# ---------------------------------------------------------------------------

@app.post("/v1/webhooks/voicehr")
async def voicehr_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_signature: Annotated[str | None, Header(alias="X-Signature")] = None,
    x_delivery_id: Annotated[str | None, Header(alias="X-Delivery-Id")] = None,
):
    body = await request.body()

    if not x_signature:
        raise HTTPException(status_code=401, detail="Missing signature")
    expected_signature = hmac.new(
        settings.webhook_hmac_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(x_signature, expected_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    if not x_delivery_id:
        raise HTTPException(status_code=422, detail="Missing X-Delivery-Id header")

    try:
        payload = WebhookPayload.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid webhook payload: {exc}")

    try:
        application_id = uuid.UUID(payload.external_reference_id)
    except ValueError:
        application_id = None

    # Log only identifiers and the outcome -- never payload.assessment (scores/CEFR
    # level are treated as interview content here and must never be logged).
    log_fields = {"delivery_id": x_delivery_id, "interview_status": payload.interview_status}

    # The receipt row and the application state change it authorizes are ONE
    # transaction, committed once at each return below. Committing the receipt
    # separately (as this used to) meant a crash between the two left the delivery
    # permanently recorded but never applied -- and since the receipt's UNIQUE
    # delivery_id IS the dedup mechanism, every retry of that delivery then
    # short-circuited to "duplicate_ignored", silently discarding a real interview
    # result forever. See db.insert_webhook_event's own docstring.
    try:
        event = insert_webhook_event(
            db,
            delivery_id=x_delivery_id,
            application_id=application_id,
            external_reference_id=payload.external_reference_id,
            interview_status=payload.interview_status,
            assessment=payload.assessment.model_dump(),
            processed=False,
        )
    except IntegrityError:
        # Same X-Delivery-Id as a previously recorded event: this is a retried delivery
        # of something already handled. Stop here -- no application state is touched.
        db.rollback()
        logger.info("webhook_received", extra={**log_fields, "outcome": "duplicate_ignored"})
        return {"status": "duplicate_ignored"}

    if application_id is None:
        # The receipt is still worth keeping -- recording an unresolvable reference is
        # exactly what this table exists for (see db.InterviewWebhookEvent).
        db.commit()
        logger.info("webhook_received", extra={**log_fields, "outcome": "unknown_application"})
        return {"status": "unknown_application"}

    updated = record_interview_result(
        db, application_id, payload.interview_status, payload.assessment.model_dump()
    )
    if updated is None:
        # Not an error: the application doesn't exist, or isn't currently 'invited'
        # (already interviewed/decided, or never invited). Nothing more to do -- keep
        # the receipt (processed stays False) so the delivery is still auditable.
        db.commit()
        logger.info("webhook_received", extra={**log_fields, "outcome": "no_op"})
        return {"status": "no_op"}

    mark_webhook_event_processed(db, event.id, True)
    db.commit()
    logger.info(
        "webhook_received",
        extra={**log_fields, "outcome": "processed", "application_id": str(application_id)},
    )
    return {"status": "processed"}


# ---------------------------------------------------------------------------
# React frontend -- static asset + SPA-fallback serving (production only)
#
# Registered last, after every /v1/* and /health route above -- FastAPI/Starlette
# matches routes in registration order, so a catch-all path route defined earlier
# would shadow the real API routes. As long as this block stays physically last in
# the file, that can't happen (see FRONTEND_HANDOFF.md §17).
#
# frontend/dist/ only exists after `npm run build` -- produced by the Docker image's
# Node build stage (Dockerfile) and .gitignored, never committed. It deliberately does
# NOT exist in a plain host checkout or in the test/dev environment this project's own
# test suite and `npm run dev` (Vite, port 5173, proxying to this service) run in. If
# it's missing, these routes are simply never registered -- every /v1/* and /health
# route above is completely unaffected either way, and this must never be the thing
# that makes `import main` (or the test suite, which does exactly that) fail on a
# machine that has never run a frontend build. This conditional guard is the one
# deliberate addition beyond §17's original sketch, added for exactly that reason.
# ---------------------------------------------------------------------------

_FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"

if _FRONTEND_DIST.is_dir():
    app.mount(
        "/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="frontend-assets"
    )

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_react_app(full_path: str = "") -> FileResponse:
        """
        Every non-API path (including a client-side route like /jobs/:id on a hard
        browser refresh) falls through to the SPA shell; BrowserRouter then takes over
        client-side and renders the right page from the URL. full_path is accepted but
        unused -- index.html is always the same file regardless of which route asked
        for it.
        """
        return FileResponse(_FRONTEND_DIST / "index.html")
else:
    logger.info(
        "frontend_dist_not_found",
        extra={"path": str(_FRONTEND_DIST)},
    )
