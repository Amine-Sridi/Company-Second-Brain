"""
Database layer for Recruitment Workflow Service.

Owns jobs, job_descriptions (versioned), scoring_configs (versioned), and match_results
(immutable). candidate_id is opaque here -- no FK into another schema, no PII, matching
the "services reach each other only through HTTP APIs" rule.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Generator, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    create_engine,
    func,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

import os


# ---------------------------------------------------------------------------
# Engine (lazy -- avoids crashing at import if DATABASE_URL is not yet set)
# ---------------------------------------------------------------------------

_engine = None
_SessionLocal = None


def _get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Copy .env.example to .env and fill in the recruitment_role credentials."
        )
    return url


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_get_database_url(), pool_pre_ping=True)
    return _engine


def _get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=_get_engine(), autocommit=False, autoflush=False)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency -- yields a DB session and closes it after the request."""
    db = _get_session_factory()()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = {"schema": "recruitment"}

    id: uuid.UUID = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    title: str = Column(Text, nullable=False)
    threshold: float = Column(Float, nullable=False)
    # Optional, recruiter-configured second-stage screening criterion (see
    # language_gate.py) -- both NULL means no requirement (current/legacy behavior).
    # Paired by a DB CHECK constraint: never one set without the other.
    required_language: Optional[str] = Column(Text, nullable=True)
    min_language_cefr: Optional[str] = Column(Text, nullable=True)
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class JobDescription(Base):
    __tablename__ = "job_descriptions"
    __table_args__ = {"schema": "recruitment"}

    id: uuid.UUID = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: uuid.UUID = Column(
        UUID(as_uuid=True), ForeignKey("recruitment.jobs.id"), nullable=False
    )
    version: int = Column(Integer, nullable=False)
    raw_text: str = Column(Text, nullable=False)
    required_skills: list = Column(JSONB, nullable=False)
    preferred_skills: list = Column(JSONB, nullable=False)
    min_years_experience: Optional[float] = Column(Float, nullable=True)
    titles: list = Column(JSONB, nullable=False)
    structuring_model: str = Column(Text, nullable=False)
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class ScoringConfig(Base):
    __tablename__ = "scoring_configs"
    __table_args__ = {"schema": "recruitment"}

    id: uuid.UUID = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    version: int = Column(Integer, nullable=False, unique=True)
    weight_semantic: float = Column(Float, nullable=False)
    weight_deterministic: float = Column(Float, nullable=False)
    skill_weight: float = Column(Float, nullable=False)
    experience_weight: float = Column(Float, nullable=False)
    title_weight: float = Column(Float, nullable=False)
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class MatchResult(Base):
    """Immutable -- never updated after insert. See CLAUDE.md: a later candidate
    enrichment must never change an existing MatchResult."""

    __tablename__ = "match_results"
    __table_args__ = {"schema": "recruitment"}

    id: uuid.UUID = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: uuid.UUID = Column(
        UUID(as_uuid=True), ForeignKey("recruitment.jobs.id"), nullable=False
    )
    job_description_id: uuid.UUID = Column(
        UUID(as_uuid=True), ForeignKey("recruitment.job_descriptions.id"), nullable=False
    )
    job_description_version: int = Column(Integer, nullable=False)
    scoring_config_id: uuid.UUID = Column(
        UUID(as_uuid=True), ForeignKey("recruitment.scoring_configs.id"), nullable=False
    )
    scoring_config_version: int = Column(Integer, nullable=False)
    embedding_model_version: str = Column(Text, nullable=False)
    candidate_id: uuid.UUID = Column(UUID(as_uuid=True), nullable=False)
    candidate_snapshot: dict = Column(JSONB, nullable=False)
    threshold_snapshot: float = Column(Float, nullable=False)
    semantic_score: float = Column(Float, nullable=False)
    skill_score: float = Column(Float, nullable=False)
    experience_score: float = Column(Float, nullable=False)
    title_score: float = Column(Float, nullable=False)
    deterministic_score: float = Column(Float, nullable=False)
    final_score: float = Column(Float, nullable=False)
    hard_gate_passed: bool = Column(Boolean, nullable=False)
    matched_requirements: list = Column(JSONB, nullable=False)
    missing_requirements: list = Column(JSONB, nullable=False)
    qualified: bool = Column(Boolean, nullable=False)
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class MatchExplanation(Base):
    """
    Advisory, LLM-generated explanation for one MatchResult -- 1:1 (match_result_id
    UNIQUE), generated at most once and reused on every subsequent read. Purely
    read-after-write: nothing here ever feeds back into scoring or qualification, both
    of which are already computed and persisted on MatchResult before a row here can
    exist. Unlike interview_webhook_events, a real foreign key is correct here -- every
    row is created by this service itself for a match_result_id it already knows exists.
    """

    __tablename__ = "match_explanations"
    __table_args__ = {"schema": "recruitment"}

    id: uuid.UUID = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    match_result_id: uuid.UUID = Column(
        UUID(as_uuid=True),
        ForeignKey("recruitment.match_results.id"),
        nullable=False,
        unique=True,
    )
    explanation: str = Column(Text, nullable=False)
    model: str = Column(Text, nullable=False)
    prompt_version: str = Column(Text, nullable=False)
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class Recruiter(Base):
    __tablename__ = "recruiters"
    __table_args__ = {"schema": "recruitment"}

    id: uuid.UUID = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: str = Column(Text, nullable=False, unique=True)
    password_hash: str = Column(Text, nullable=False)
    name: str = Column(Text, nullable=False)
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class Application(Base):
    """
    Tracks one candidate's pipeline for one job, starting from a recruiter's selection
    of a qualified MatchResult.

    State machine: selected -> invited -> interviewed -> accepted | rejected (terminal).
    A recruiter may accept/reject from selected, invited, or interviewed -- the interview
    is informative, not a mandatory gate. invite_attempt_number and invited_at track the
    RWS -> VoiceHR invitation; interview_status/interview_assessment/interview_completed_at
    are populated by the VoiceHR -> RWS webhook once the interview reaches a terminal
    evaluation outcome.
    """

    __tablename__ = "applications"
    __table_args__ = {"schema": "recruitment"}

    id: uuid.UUID = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: uuid.UUID = Column(
        UUID(as_uuid=True), ForeignKey("recruitment.jobs.id"), nullable=False
    )
    match_result_id: uuid.UUID = Column(
        UUID(as_uuid=True),
        ForeignKey("recruitment.match_results.id"),
        nullable=False,
        unique=True,
    )
    candidate_id: uuid.UUID = Column(UUID(as_uuid=True), nullable=False)
    status: str = Column(Text, nullable=False, server_default=text("'selected'"))
    # Snapshot of the job's language requirement at *selection* time (see
    # language_gate.py's module docstring for why this must be a snapshot, not a live
    # read of `jobs`, which is mutable). NULL/NULL means the job had no requirement
    # when this application was created -- the gate is then `not_required` forever,
    # regardless of any language requirement added to the job afterwards.
    required_language: Optional[str] = Column(Text, nullable=True)
    required_cefr: Optional[str] = Column(Text, nullable=True)
    selected_by: uuid.UUID = Column(
        UUID(as_uuid=True), ForeignKey("recruitment.recruiters.id"), nullable=False
    )
    selected_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    invite_attempt_number: int = Column(Integer, nullable=False, server_default=text("0"))
    invited_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    interview_status: Optional[str] = Column(Text, nullable=True)
    interview_assessment: Optional[dict] = Column(JSONB, nullable=True)
    interview_completed_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    decided_by: Optional[uuid.UUID] = Column(
        UUID(as_uuid=True), ForeignKey("recruitment.recruiters.id"), nullable=True
    )
    decided_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class RecruiterDecision(Base):
    """Append-only audit log of every successful accept/reject transition."""

    __tablename__ = "recruiter_decisions"
    __table_args__ = {"schema": "recruitment"}

    id: uuid.UUID = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: uuid.UUID = Column(
        UUID(as_uuid=True), ForeignKey("recruitment.applications.id"), nullable=False
    )
    recruiter_id: uuid.UUID = Column(
        UUID(as_uuid=True), ForeignKey("recruitment.recruiters.id"), nullable=False
    )
    decision: str = Column(Text, nullable=False)
    previous_status: str = Column(Text, nullable=False)
    new_status: str = Column(Text, nullable=False)
    decided_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class InterviewWebhookEvent(Base):
    """
    Append-only receipt log for every VoiceHR -> RWS webhook delivery, keyed by
    delivery_id (UNIQUE). A duplicate delivery_id fails to insert (IntegrityError) --
    that IS the dedup mechanism, enforced by the database rather than a check-then-act
    read. application_id is a plain UUID column (not a foreign key): a webhook can
    legitimately reference an external_reference_id that doesn't correspond to a real
    application (unknown/malformed/stale), and this table must still be able to log
    that fact -- a FK here would make the insert itself fail for exactly the case this
    table exists to record.
    """

    __tablename__ = "interview_webhook_events"
    __table_args__ = {"schema": "recruitment"}

    id: uuid.UUID = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    delivery_id: str = Column(Text, nullable=False, unique=True)
    application_id: Optional[uuid.UUID] = Column(UUID(as_uuid=True), nullable=True)
    external_reference_id: str = Column(Text, nullable=False)
    interview_status: str = Column(Text, nullable=False)
    assessment: dict = Column(JSONB, nullable=False)
    processed: bool = Column(Boolean, nullable=False)
    received_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def create_job_with_description(
    db: Session,
    title: str,
    threshold: float,
    raw_text: str,
    required_skills: list[str],
    preferred_skills: list[str],
    min_years_experience: Optional[float],
    titles: list[str],
    structuring_model: str,
    required_language: Optional[str] = None,
    min_language_cefr: Optional[str] = None,
) -> tuple[Job, JobDescription]:
    """Create a new Job and its first (version 1) JobDescription in one transaction."""
    job = Job(
        title=title,
        threshold=threshold,
        required_language=required_language,
        min_language_cefr=min_language_cefr,
    )
    db.add(job)
    db.flush()  # assign job.id without committing

    job_description = JobDescription(
        job_id=job.id,
        version=1,
        raw_text=raw_text,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        min_years_experience=min_years_experience,
        titles=titles,
        structuring_model=structuring_model,
    )
    db.add(job_description)
    db.commit()
    db.refresh(job)
    db.refresh(job_description)
    return job, job_description


def get_job(db: Session, job_id: uuid.UUID) -> Optional[Job]:
    return db.query(Job).filter(Job.id == job_id).first()


def set_job_language_requirement(
    db: Session,
    job_id: uuid.UUID,
    required_language: Optional[str],
    min_language_cefr: Optional[str],
) -> Optional[Job]:
    """
    Configure (or clear, by passing both as None) an existing job's language
    requirement. Does not touch any already-created Application's own snapshot --
    see the Application.required_language/required_cefr columns' docstring for why
    that's deliberate, not an oversight.
    """
    result = db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(required_language=required_language, min_language_cefr=min_language_cefr)
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    db.commit()
    return get_job(db, job_id)


def list_jobs_with_match_counts(db: Session) -> list[tuple[Job, int]]:
    """
    Every job, newest first, paired with how many MatchResults exist for it -- one
    query (LEFT JOIN + GROUP BY), not N+1 and not a per-job subquery loop. No
    pagination/filtering/search: recruiter-facing GET /v1/jobs is intentionally a
    lightweight full list, not a browsing API (see FRONTEND_HANDOFF.md).

    Grouping by Job.id (the primary key) and ordering by Job.created_at is valid
    Postgres SQL without adding created_at to GROUP BY -- every other column of a
    table is functionally dependent once grouped by its primary key.
    """
    rows = (
        db.query(Job, func.count(MatchResult.id).label("match_count"))
        .outerjoin(MatchResult, MatchResult.job_id == Job.id)
        .group_by(Job.id)
        .order_by(Job.created_at.desc())
        .all()
    )
    return [(job, match_count) for job, match_count in rows]


def get_latest_scoring_config(db: Session) -> Optional[ScoringConfig]:
    return db.query(ScoringConfig).order_by(ScoringConfig.version.desc()).first()


def get_scoring_config_by_version(db: Session, version: int) -> Optional[ScoringConfig]:
    return db.query(ScoringConfig).filter(ScoringConfig.version == version).first()


def create_scoring_config(
    db: Session,
    weight_semantic: float,
    weight_deterministic: float,
    skill_weight: float,
    experience_weight: float,
    title_weight: float,
) -> ScoringConfig:
    """Create a new versioned ScoringConfig. Weight-sum validation happens at the
    API/schema boundary (ScoringConfigRequest), not here."""
    latest = get_latest_scoring_config(db)
    next_version = (latest.version + 1) if latest else 1
    config = ScoringConfig(
        version=next_version,
        weight_semantic=weight_semantic,
        weight_deterministic=weight_deterministic,
        skill_weight=skill_weight,
        experience_weight=experience_weight,
        title_weight=title_weight,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def create_match_result(
    db: Session,
    job_id: uuid.UUID,
    job_description_id: uuid.UUID,
    job_description_version: int,
    scoring_config_id: uuid.UUID,
    scoring_config_version: int,
    embedding_model_version: str,
    candidate_id: uuid.UUID,
    candidate_snapshot: dict,
    threshold_snapshot: float,
    semantic_score: float,
    skill_score: float,
    experience_score: float,
    title_score: float,
    deterministic_score: float,
    final_score: float,
    hard_gate_passed: bool,
    matched_requirements: list[str],
    missing_requirements: list[str],
    qualified: bool,
) -> MatchResult:
    match = MatchResult(
        job_id=job_id,
        job_description_id=job_description_id,
        job_description_version=job_description_version,
        scoring_config_id=scoring_config_id,
        scoring_config_version=scoring_config_version,
        embedding_model_version=embedding_model_version,
        candidate_id=candidate_id,
        candidate_snapshot=candidate_snapshot,
        threshold_snapshot=threshold_snapshot,
        semantic_score=semantic_score,
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
    db.add(match)
    db.commit()
    db.refresh(match)
    return match


def list_match_results_for_job(db: Session, job_id: uuid.UUID) -> list[MatchResult]:
    return (
        db.query(MatchResult)
        .filter(MatchResult.job_id == job_id)
        .order_by(MatchResult.final_score.desc())
        .all()
    )


def list_all_match_results(db: Session) -> list[tuple[MatchResult, str]]:
    """
    Every persisted MatchResult across every job, each paired with its job's title --
    the global counterpart to list_match_results_for_job, for the candidate/job
    knowledge-graph endpoint (see FRONTEND_HANDOFF.md's knowledge-graph design audit
    and main.py: get_candidates_jobs_graph). One JOIN, not an N+1 per-match job
    lookup -- the exact same shape as list_all_applications. Qualified and
    disqualified matches are both returned, unfiltered: the graph is a read of the
    full audit trail already implied by "every scored candidate gets an immutable
    MatchResult row, regardless of qualification outcome" (CLAUDE.md), not a
    second-guessing of it.
    """
    rows = (
        db.query(MatchResult, Job.title)
        .join(Job, Job.id == MatchResult.job_id)
        .all()
    )
    return [(match, job_title) for match, job_title in rows]


def get_match_result(db: Session, match_id: uuid.UUID) -> Optional[MatchResult]:
    return db.query(MatchResult).filter(MatchResult.id == match_id).first()


# ---------------------------------------------------------------------------
# Match explanations (advisory, LLM-generated, cached -- see MatchExplanation)
# ---------------------------------------------------------------------------

def get_match_explanation(
    db: Session, match_result_id: uuid.UUID
) -> Optional[MatchExplanation]:
    return (
        db.query(MatchExplanation)
        .filter(MatchExplanation.match_result_id == match_result_id)
        .first()
    )


def create_match_explanation(
    db: Session,
    match_result_id: uuid.UUID,
    explanation: str,
    model: str,
    prompt_version: str,
) -> MatchExplanation:
    """
    Raises sqlalchemy.exc.IntegrityError if an explanation for this match_result_id was
    already created (match_result_id UNIQUE) -- callers must catch this and re-fetch the
    row a concurrent request already inserted, rather than generating (and storing) a
    second one for the same match.
    """
    row = MatchExplanation(
        match_result_id=match_result_id,
        explanation=explanation,
        model=model,
        prompt_version=prompt_version,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Recruiters
# ---------------------------------------------------------------------------

def get_recruiter_by_email(db: Session, email: str) -> Optional[Recruiter]:
    return db.query(Recruiter).filter(Recruiter.email == email).first()


def get_recruiter_by_id(db: Session, recruiter_id: uuid.UUID) -> Optional[Recruiter]:
    return db.query(Recruiter).filter(Recruiter.id == recruiter_id).first()


# ---------------------------------------------------------------------------
# Applications (candidate selection + recruiter decisions)
# ---------------------------------------------------------------------------

def create_application(
    db: Session,
    job_id: uuid.UUID,
    match_result_id: uuid.UUID,
    candidate_id: uuid.UUID,
    selected_by: uuid.UUID,
    required_language: Optional[str] = None,
    required_cefr: Optional[str] = None,
) -> Application:
    """
    Create a new Application in the 'selected' state. Raises sqlalchemy.exc.IntegrityError
    if this candidate was already selected for this job (job_id, candidate_id unique) or if
    this match_result was already used to create an application (match_result_id unique) --
    callers should catch this and translate it to a 409 Conflict rather than a check-then-act
    race, since the uniqueness is enforced atomically by the database, not by application code.

    required_language/required_cefr are the caller's (main.py: select_candidate) snapshot of
    the job's language requirement *at this exact moment* -- see language_gate.py and the
    Application model's own docstring for why this must be copied in, not read live from
    `jobs` on every later request.
    """
    application = Application(
        job_id=job_id,
        match_result_id=match_result_id,
        candidate_id=candidate_id,
        selected_by=selected_by,
        required_language=required_language,
        required_cefr=required_cefr,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def get_application(db: Session, application_id: uuid.UUID) -> Optional[Application]:
    return db.query(Application).filter(Application.id == application_id).first()


def list_applications_for_job(db: Session, job_id: uuid.UUID) -> list[Application]:
    return (
        db.query(Application)
        .filter(Application.job_id == job_id)
        .order_by(Application.selected_at.desc())
        .all()
    )


def list_all_applications(db: Session) -> list[tuple[Application, str]]:
    """
    Every application across every job, newest-selection-first, each paired with its
    job's title -- the global counterpart to list_applications_for_job (same ordering
    rule: Application.selected_at desc). One JOIN, not an N+1 per-application title
    lookup. No pagination/filtering yet, matching this project's existing "no infra
    ahead of demonstrated need" rule (same as list_jobs_with_match_counts).
    """
    rows = (
        db.query(Application, Job.title)
        .join(Job, Job.id == Application.job_id)
        .order_by(Application.selected_at.desc())
        .all()
    )
    return [(application, job_title) for application, job_title in rows]


_DECIDABLE_STATUSES = ("selected", "invited", "interviewed")


def transition_application(
    db: Session,
    application_id: uuid.UUID,
    recruiter_id: uuid.UUID,
    decision: str,
) -> Optional[Application]:
    """
    Atomically transition an application to 'accepted' or 'rejected', recording an audit
    row on success. Allowed from 'selected', 'invited', or 'interviewed' -- the interview
    is informative for the recruiter's decision, not a mandatory gate before deciding.

    Returns None if the application does not exist, or if it is not currently in a
    decidable state (already decided). The transition itself is a single conditional
    UPDATE ... WHERE status IN (...) -- this is what makes it safe under concurrent
    duplicate requests: if two requests race, the database guarantees only one UPDATE
    matches the WHERE clause and affects a row, regardless of request timing. The other
    gets rowcount 0 and this function returns None for it.
    """
    current = get_application(db, application_id)
    if current is None:
        return None
    previous_status = current.status

    new_status = "accepted" if decision == "accept" else "rejected"
    result = db.execute(
        update(Application)
        .where(Application.id == application_id, Application.status.in_(_DECIDABLE_STATUSES))
        .values(
            status=new_status,
            decided_by=recruiter_id,
            decided_at=func.now(),
            updated_at=func.now(),
        )
    )
    if result.rowcount == 0:
        db.rollback()
        return None

    db.add(
        RecruiterDecision(
            application_id=application_id,
            recruiter_id=recruiter_id,
            decision=decision,
            previous_status=previous_status,
            new_status=new_status,
        )
    )
    db.commit()
    db.refresh(current)
    return current


def record_invite_attempt(
    db: Session, application_id: uuid.UUID, attempt_number: int
) -> Optional[Application]:
    """
    Atomically record a successful VoiceHR invitation. Allowed from 'selected' (first
    invite) or 'invited' (re-invite/resend) -- both leave the application in 'invited'.

    Returns None if the application is not in one of those two states (e.g. it has
    already progressed to interviewed/accepted/rejected). attempt_number is computed by
    the caller as current_attempt_number + 1 *before* calling VoiceHR, and is only
    persisted here after VoiceHR's response is known -- see main.py's invite endpoint
    for why this ordering is what makes VoiceHR's own idempotency check effective.
    """
    result = db.execute(
        update(Application)
        .where(
            Application.id == application_id,
            Application.status.in_(("selected", "invited")),
        )
        .values(
            status="invited",
            invite_attempt_number=attempt_number,
            invited_at=func.now(),
            updated_at=func.now(),
        )
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    db.commit()
    return get_application(db, application_id)


def record_interview_result(
    db: Session,
    application_id: uuid.UUID,
    interview_status: str,
    assessment: dict,
) -> Optional[Application]:
    """
    Atomically transition an application from 'invited' to 'interviewed', storing the
    assessment. Returns None (a no-op, not an error) if the application is not currently
    'invited' -- e.g. a duplicate/out-of-order webhook delivered after the recruiter
    already made a decision, or before an invite was ever recorded. The caller (the
    webhook route) treats None as "nothing to do, respond 200" rather than a failure.
    """
    result = db.execute(
        update(Application)
        .where(Application.id == application_id, Application.status == "invited")
        .values(
            status="interviewed",
            interview_status=interview_status,
            interview_assessment=assessment,
            interview_completed_at=func.now(),
            updated_at=func.now(),
        )
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    db.commit()
    return get_application(db, application_id)


def get_application_by_id(db: Session, application_id: uuid.UUID) -> Optional[Application]:
    return get_application(db, application_id)


# ---------------------------------------------------------------------------
# Interview webhook events (VoiceHR -> RWS delivery log + dedup)
# ---------------------------------------------------------------------------

def insert_webhook_event(
    db: Session,
    delivery_id: str,
    application_id: Optional[uuid.UUID],
    external_reference_id: str,
    interview_status: str,
    assessment: dict,
    processed: bool,
) -> InterviewWebhookEvent:
    """
    Insert a webhook receipt row. Raises sqlalchemy.exc.IntegrityError if delivery_id was
    already recorded -- callers must catch this and treat it as a duplicate delivery
    (200 no-op), not an error, since the database's UNIQUE constraint is the actual
    dedup mechanism, not a check-then-act read beforehand.
    """
    event = InterviewWebhookEvent(
        delivery_id=delivery_id,
        application_id=application_id,
        external_reference_id=external_reference_id,
        interview_status=interview_status,
        assessment=assessment,
        processed=processed,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def mark_webhook_event_processed(db: Session, event_id: uuid.UUID, processed: bool) -> None:
    db.execute(
        update(InterviewWebhookEvent)
        .where(InterviewWebhookEvent.id == event_id)
        .values(processed=processed)
    )
    db.commit()
