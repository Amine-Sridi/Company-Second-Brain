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
import uuid
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import clients
import resume_intelligence_client
import voicehr_client
from auth import InvalidTokenError, create_access_token, decode_access_token, verify_password
from db import (
    Recruiter,
    create_application,
    create_job_with_description,
    create_match_result,
    create_scoring_config,
    get_application,
    get_db,
    get_job,
    get_latest_scoring_config,
    get_match_explanation,
    get_match_result,
    get_recruiter_by_email,
    get_recruiter_by_id,
    get_scoring_config_by_version,
    insert_webhook_event,
    list_applications_for_job,
    list_match_results_for_job,
    mark_webhook_event_processed,
    record_interview_result,
    record_invite_attempt,
    transition_application,
)
from explanation import get_or_create_explanation
from clients import SecondBrainClientError
from resume_intelligence_client import ResumeIntelligenceClientError
from schemas import (
    ApplicationResponse,
    InviteApplicationRequest,
    JobResponse,
    JobSubmissionRequest,
    LoginRequest,
    LoginResponse,
    MatchResultResponse,
    ScoringConfigRequest,
    ScoringConfigResponse,
    StructuredJobDescription,
)
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

def _application_response(application) -> ApplicationResponse:
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

    try:
        application = create_application(
            db,
            job_id=match.job_id,
            match_result_id=match.id,
            candidate_id=match.candidate_id,
            selected_by=recruiter.id,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="This candidate has already been selected for this job"
        )

    return _application_response(application)


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

    return _application_response(updated)


@app.post("/v1/applications/{application_id}/accept", response_model=ApplicationResponse)
def accept_application(
    application_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """Terminal transition: selected/invited/interviewed -> accepted. Recorded in
    recruiter_decisions, then triggers an async knowledge feedback write-back."""
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


@app.get("/v1/jobs/{job_id}/applications", response_model=list[ApplicationResponse])
def get_job_applications(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(require_recruiter),
):
    """Recruiter-facing: review selections and decisions made so far for a job."""
    if get_job(db, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return [_application_response(a) for a in list_applications_for_job(db, job_id)]


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

    Callable when the application is 'selected' (first invite) or 'invited' (resend --
    e.g. the candidate never received the link). invite_attempt_number is computed as
    current + 1 and is only persisted after VoiceHR responds successfully: if VoiceHR
    is called but its response never reaches RWS, the next call recomputes the same
    attempt number, so the same idempotency key is reused and VoiceHR's own idempotency
    check (not a new duplicate interview) is what protects the retry.
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

    attempt_number = application.invite_attempt_number + 1
    idempotency_key = f"{application_id}:{attempt_number}"

    try:
        await voicehr_client.invite_candidate(
            settings.voicehr_url,
            settings.voicehr_api_key,
            idempotency_key=idempotency_key,
            candidate_name=contact["name"],
            candidate_email=contact["email"],
            language=payload.language,
            external_reference_id=str(application_id),
        )
    except VoiceHRClientError as exc:
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
    return _application_response(updated)


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
        logger.info("webhook_received", extra={**log_fields, "outcome": "unknown_application"})
        return {"status": "unknown_application"}

    updated = record_interview_result(
        db, application_id, payload.interview_status, payload.assessment.model_dump()
    )
    if updated is None:
        # Not an error: the application doesn't exist, or isn't currently 'invited'
        # (already interviewed/decided, or never invited). Nothing more to do.
        logger.info("webhook_received", extra={**log_fields, "outcome": "no_op"})
        return {"status": "no_op"}

    mark_webhook_event_processed(db, event.id, True)
    logger.info(
        "webhook_received",
        extra={**log_fields, "outcome": "processed", "application_id": str(application_id)},
    )
    return {"status": "processed"}
