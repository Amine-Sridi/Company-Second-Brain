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
    # Deterministic JD-extraction/match-outcome validation metadata (jd_validation.py,
    # match_anomaly.py) -- set once immediately after structuring, and (since
    # milestone 38) possibly updated a second time immediately after matching, via
    # update_job_description_extraction_metadata. Never a correction of the four
    # required_skills/preferred_skills/min_years_experience/titles fields above --
    # those stay exactly what was actually scored, unmodified by either update. See
    # alembic/versions/0008_jd_extraction_validation.py for why these are NOT NULL
    # with a constant default rather than nullable.
    extraction_status: str = Column(Text, nullable=False, server_default=text("'clean'"))
    extraction_warnings: list = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
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


class InterviewRound(Base):
    """
    One technical/managerial interview round for an Application -- the post-VoiceHR
    stage for a candidate whose language gate has cleared (see handoff.md and
    language_gate.py). Deliberately a parallel signal, not a new Application.status
    value: Application.status stays selected/invited/interviewed/accepted/rejected,
    unchanged, exactly the same relationship the language gate already has to that
    state machine. main.py's create_interview_round enforces the two preconditions
    (language gate not blocking, application not already accepted/rejected) -- this
    model and its helpers below assume the caller already checked those, the same
    division of responsibility record_interview_result/transition_application use.

    interviewer_name is free text, not a foreign key to a user/interviewer table:
    this project has exactly one seeded recruiter account and no interviewer
    directory or auth system (CLAUDE.md's non-negotiable decisions) -- inventing one
    here would be exactly the kind of speculative infrastructure CLAUDE.md's code
    style rules warn against. created_by *is* a real FK to recruiters, the same
    audit pattern already used by Application.selected_by/decided_by.
    interviewer_email is the same free-text treatment, for the same reason -- it is
    not candidate PII (the interviewer is an internal contact the recruiter types in
    directly, not the candidate whose PII is restricted to Resume Intelligence), so
    storing it directly here needs no privacy carve-out.

    outcome is NULL unless status='completed' (enforced by a paired DB CHECK
    constraint, the same style as jobs_language_requirement_paired_check) -- a
    scheduled or cancelled round never has a pass/fail/needs_review verdict.

    notification_status/notification_error (migration 0010) record the outcome of
    the best-effort candidate+interviewer email+ICS sent on create/reschedule/
    cancel/resend (interview_round_mailer.py) -- purely advisory, the same
    "observational, not part of the round's own identity" treatment
    jd_validation's extraction_status/extraction_warnings columns already
    established. A notification failure never blocks or reverts the round's own
    create/reschedule/cancel; see main.py's own endpoints.

    notification_sequence (migration 0010, incremented starting migration --
    phase 2, see handoff.md) is the ICS `SEQUENCE` last actually sent for this
    round -- RFC 5545's own mechanism for a calendar client to tell an update
    from a duplicate. It increments on reschedule and cancel (the event itself
    changed), never on create's first send or a plain resend (both are the
    *current* state, not a change to it) -- see
    update_interview_round_notification's own docstring for exactly which
    callers pass a new value.
    """

    __tablename__ = "interview_rounds"
    __table_args__ = {"schema": "recruitment"}

    id: uuid.UUID = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    application_id: uuid.UUID = Column(
        UUID(as_uuid=True), ForeignKey("recruitment.applications.id"), nullable=False
    )
    round_type: str = Column(Text, nullable=False)
    status: str = Column(Text, nullable=False, server_default=text("'scheduled'"))
    outcome: Optional[str] = Column(Text, nullable=True)
    interviewer_name: str = Column(Text, nullable=False)
    interviewer_email: Optional[str] = Column(Text, nullable=True)
    location: Optional[str] = Column(Text, nullable=True)
    scheduled_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    notes: Optional[str] = Column(Text, nullable=True)
    notification_status: str = Column(Text, nullable=False, server_default=text("'pending'"))
    notification_error: Optional[str] = Column(Text, nullable=True)
    notification_sequence: int = Column(Integer, nullable=False, server_default=text("0"))
    created_by: uuid.UUID = Column(
        UUID(as_uuid=True), ForeignKey("recruitment.recruiters.id"), nullable=False
    )
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
    extraction_status: str = "clean",
    extraction_warnings: Optional[list[dict]] = None,
) -> tuple[Job, JobDescription]:
    """
    Adds a new Job and its first (version 1) JobDescription to the session.

    Deliberately does NOT commit -- this is one step inside submit_job's own
    single, atomic transaction (see that function's own docstring for why:
    milestone 39's reliability audit found that committing here immediately,
    before Second Brain retrieval and per-candidate scoring even ran, meant a
    later failure in that pipeline left a real, orphaned Job with zero or
    partial MatchResults durably persisted, with the client only ever seeing a
    502 and no job_id to know it happened). Only `db.flush()`, to assign
    `job.id`/`job_description.id` (and every other server-generated default)
    without finalizing anything -- the caller commits once, after the entire
    pipeline succeeds.

    required_skills/preferred_skills/min_years_experience/titles are expected to
    already be normalized (jd_validation.normalize_extraction) by the caller --
    this function persists exactly what it's given, the same "used for scoring
    is exactly what's stored" reproducibility every other field here already has.
    extraction_status/extraction_warnings default to the same "clean, no
    warnings" values the DB column defaults use, so any caller that predates
    jd_validation.py (e.g. an older test) still gets a valid row.
    """
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
        extraction_status=extraction_status,
        extraction_warnings=extraction_warnings if extraction_warnings is not None else [],
    )
    db.add(job_description)
    db.flush()  # assign job_description.id/created_at without committing
    return job, job_description


def get_job(db: Session, job_id: uuid.UUID) -> Optional[Job]:
    return db.query(Job).filter(Job.id == job_id).first()


def get_job_description_for_job(db: Session, job_id: uuid.UUID) -> Optional[JobDescription]:
    """The one JobDescription for a job (version 1 always today -- see
    list_jobs_with_match_counts's own docstring for why no version filter is
    needed yet). Used wherever a single job's extraction_status/
    extraction_warnings need to be re-read after creation (e.g. after the
    language-requirement PATCH, which returns the same JobSummaryResponse shape
    and must not silently reset those fields to their schema defaults)."""
    return (
        db.query(JobDescription)
        .filter(JobDescription.job_id == job_id)
        .order_by(JobDescription.version.desc())
        .first()
    )


def update_job_description_extraction_metadata(
    db: Session,
    job_description_id: uuid.UUID,
    extraction_status: str,
    extraction_warnings: list[dict],
) -> None:
    """
    Updates only extraction_status/extraction_warnings on an already-flushed
    JobDescription -- used by main.py's post-match anomaly check (match_anomaly.py)
    to append a warning discovered only after that job's MatchResults already
    exist. Never touches required_skills/preferred_skills/min_years_experience/
    titles/raw_text/structuring_model, and never touches any MatchResult row at
    all -- the same "extraction_status/extraction_warnings are observational
    metadata, not part of what's reproduced" boundary jd_validation.py's own
    columns already established (migration 0008).

    Deliberately does NOT commit -- called from inside submit_job's own
    `db.begin_nested()` savepoint (see that function's docstring), so a failure
    here rolls back only this one advisory update, not the job/MatchResults
    already pending in the same still-open, not-yet-committed transaction.
    """
    db.execute(
        update(JobDescription)
        .where(JobDescription.id == job_description_id)
        .values(extraction_status=extraction_status, extraction_warnings=extraction_warnings)
    )


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


def list_jobs_with_match_counts(db: Session) -> list[tuple[Job, str, list, int]]:
    """
    Every job, newest first, paired with its own JobDescription's extraction
    status/warnings (jd_validation.py) and how many MatchResults exist for it --
    one query, not N+1 and not a per-job subquery loop. No pagination/filtering/
    search: recruiter-facing GET /v1/jobs is intentionally a lightweight full
    list, not a browsing API (see FRONTEND_HANDOFF.md).

    Joins JobDescription on job_id without filtering to a specific version:
    every job in this codebase has exactly one JobDescription today (version 1
    always -- no code path here ever creates a second version, PATCH .../
    language-requirement only touches Job). If that ever changes, this would
    need to pick the latest version explicitly instead of grouping by
    JobDescription.id too.

    Grouping by Job.id/JobDescription.id (both primary keys) and ordering by
    Job.created_at is valid Postgres SQL without adding created_at to GROUP BY
    -- every other column of a table is functionally dependent once grouped by
    its primary key.
    """
    rows = (
        db.query(
            Job,
            JobDescription.extraction_status,
            JobDescription.extraction_warnings,
            func.count(MatchResult.id).label("match_count"),
        )
        .outerjoin(JobDescription, JobDescription.job_id == Job.id)
        .outerjoin(MatchResult, MatchResult.job_id == Job.id)
        .group_by(Job.id, JobDescription.id)
        .order_by(Job.created_at.desc())
        .all()
    )
    return [
        (job, extraction_status, extraction_warnings, match_count)
        for job, extraction_status, extraction_warnings, match_count in rows
    ]


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
    """
    Adds one MatchResult to the session. Deliberately does NOT commit -- like
    create_job_with_description, this is one step inside submit_job's single
    atomic transaction (see that function's docstring): committing per-row here
    used to mean a Second Brain failure partway through a job's candidate loop
    left some candidates' MatchResults durably persisted and the rest missing,
    with the client only seeing a 502. Only `db.flush()`, to assign match.id
    without finalizing anything -- the caller commits once, after every
    candidate in the pool has been scored successfully.
    """
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
    db.flush()  # assign match.id without committing
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
    Atomically record an interview result: 'invited' -> 'interviewed' for the first
    one, or an in-place correction of an application already 'interviewed'.

    Returns None (a no-op, not an error) if the application doesn't exist, was never
    invited, or has already been decided -- the caller (the webhook route) treats None
    as "nothing to do, respond 200" rather than a failure.

    Why 'interviewed' is also accepted: VoiceHR can legitimately produce a *better*
    result for an interview it already reported -- an admin re-evaluation
    (processing.retry_report) can turn a failed/insufficient-evidence assessment into a
    real CEFR verdict. While this only matched 'invited', that correction could never
    land: VoiceHR showed the new report, RWS kept the old assessment, and since RWS's
    language gate decides on *its* copy, a recruiter could be blocked from accepting a
    candidate whose report visibly passes. A later delivery for the same application is
    therefore applied, not dropped.

    'accepted'/'rejected' are still never overwritten, deliberately: a terminal
    decision was made on the evidence available at the time, and silently rewriting
    that evidence afterwards would make the audit trail lie about what the recruiter
    actually saw. The delivery is still recorded in interview_webhook_events either
    way, so a late correction after a decision is auditable rather than invisible.

    Deliberately does NOT commit or roll back: this runs inside the same transaction
    as the webhook receipt row that authorizes it (see insert_webhook_event's own
    docstring for the lost-result window that separate transactions opened), so the
    webhook route commits once, after both. A 0-row UPDATE is not an error condition
    and leaves the session perfectly usable, so no rollback is needed for it either
    -- and issuing one here would now also discard the receipt row.
    """
    result = db.execute(
        update(Application)
        .where(
            Application.id == application_id,
            Application.status.in_(("invited", "interviewed")),
        )
        .values(
            status="interviewed",
            interview_status=interview_status,
            interview_assessment=assessment,
            interview_completed_at=func.now(),
            updated_at=func.now(),
        )
    )
    if result.rowcount == 0:
        return None
    db.flush()
    return get_application(db, application_id)


def get_application_by_id(db: Session, application_id: uuid.UUID) -> Optional[Application]:
    return get_application(db, application_id)


# ---------------------------------------------------------------------------
# Interview rounds (post-VoiceHR technical/managerial interview stage)
# ---------------------------------------------------------------------------

def create_interview_round(
    db: Session,
    application_id: uuid.UUID,
    round_type: str,
    interviewer_name: str,
    interviewer_email: str,
    location: Optional[str],
    scheduled_at: Optional[datetime],
    notes: Optional[str],
    created_by: uuid.UUID,
) -> InterviewRound:
    """
    Create a new round in the 'scheduled' state, notification_status defaulting to
    'pending' -- the caller (main.py's create_interview_round_endpoint) attempts the
    candidate+interviewer email notification *after* this call, in a separate
    update_interview_round_notification call, once this row is already committed.
    That split is deliberate: a notification failure must never prevent or roll back
    the round itself existing (see this task's own design in handoff.md). Callers are
    also responsible for the two scheduling preconditions -- language gate not
    blocking, application not already accepted/rejected -- this function does not
    re-check either, the same division of responsibility create_application/
    transition_application already have with their own callers.
    """
    round_ = InterviewRound(
        application_id=application_id,
        round_type=round_type,
        interviewer_name=interviewer_name,
        interviewer_email=interviewer_email,
        location=location,
        scheduled_at=scheduled_at,
        notes=notes,
        created_by=created_by,
    )
    db.add(round_)
    db.commit()
    db.refresh(round_)
    return round_


def update_interview_round_notification(
    db: Session,
    round_id: uuid.UUID,
    notification_status: str,
    notification_error: Optional[str],
    notification_sequence: Optional[int] = None,
) -> Optional[InterviewRound]:
    """
    Records the outcome of a best-effort candidate+interviewer email send (create,
    reschedule, cancel, or resend-notification) -- never touches round_type/
    interviewer_name/interviewer_email/location/scheduled_at/status/outcome. Called
    after the round itself is already committed, so this only ever annotates an
    existing row, never rolls it back regardless of the notification outcome. No
    status precondition (unlike reschedule/complete/cancel): notification is
    orthogonal to a round's own scheduled/completed/cancelled lifecycle, so this
    succeeds regardless of it.

    notification_sequence, if given, is written alongside status/error in the same
    UPDATE -- reschedule/cancel pass the incremented value they already committed
    to using for that send's own ICS SEQUENCE (interview_round_mailer.py), so the
    persisted counter and the SEQUENCE a real calendar client actually received
    can never drift apart. Create's first send and a plain resend both omit it
    (None), leaving the column at whatever it already was -- neither one is a real
    change to the event, so neither increments it (see this column's own docstring
    on InterviewRound for why: SEQUENCE increases only when the event itself
    changes, not on a re-delivery of unchanged content).
    """
    values = {
        "notification_status": notification_status,
        "notification_error": notification_error,
        "updated_at": func.now(),
    }
    if notification_sequence is not None:
        values["notification_sequence"] = notification_sequence
    db.execute(update(InterviewRound).where(InterviewRound.id == round_id).values(**values))
    db.commit()
    return get_interview_round(db, round_id)


def list_interview_rounds_for_application(
    db: Session, application_id: uuid.UUID
) -> list[InterviewRound]:
    """Every round for one application, oldest first -- the order they were
    scheduled in, which is also their natural reading order in the UI."""
    return (
        db.query(InterviewRound)
        .filter(InterviewRound.application_id == application_id)
        .order_by(InterviewRound.created_at.asc())
        .all()
    )


def get_interview_round(db: Session, round_id: uuid.UUID) -> Optional[InterviewRound]:
    return db.query(InterviewRound).filter(InterviewRound.id == round_id).first()


def reschedule_interview_round(
    db: Session, round_id: uuid.UUID, scheduled_at: datetime
) -> Optional[InterviewRound]:
    """
    Atomically move a round's scheduled_at, only from 'scheduled' -- the same
    conditional-UPDATE-WHERE-status pattern record_invite_attempt/
    transition_application already use, race-safe under concurrent duplicate
    requests for the same reason theirs is: only one UPDATE can match the WHERE
    clause regardless of request timing. Returns None (a no-op, not an error) if
    the round doesn't exist or isn't 'scheduled' -- main.py's caller already knows
    which by checking existence first, and turns None into a 409.
    """
    result = db.execute(
        update(InterviewRound)
        .where(InterviewRound.id == round_id, InterviewRound.status == "scheduled")
        .values(scheduled_at=scheduled_at, updated_at=func.now())
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    db.commit()
    return get_interview_round(db, round_id)


def complete_interview_round(
    db: Session, round_id: uuid.UUID, outcome: str, notes: Optional[str]
) -> Optional[InterviewRound]:
    """
    Atomically transition a round from 'scheduled' to 'completed' with its
    outcome, satisfying migration 0007's paired outcome/status CHECK constraint
    by construction (outcome is always set in the same UPDATE that sets
    status='completed'). notes is only overwritten when the caller actually
    supplies one -- passing None leaves whatever was recorded at scheduling time
    untouched rather than clobbering it with a null.
    """
    values = {"status": "completed", "outcome": outcome, "updated_at": func.now()}
    if notes is not None:
        values["notes"] = notes
    result = db.execute(
        update(InterviewRound)
        .where(InterviewRound.id == round_id, InterviewRound.status == "scheduled")
        .values(**values)
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    db.commit()
    return get_interview_round(db, round_id)


def cancel_interview_round(db: Session, round_id: uuid.UUID) -> Optional[InterviewRound]:
    """Atomically transition a round from 'scheduled' to 'cancelled' -- terminal,
    like 'completed'; there is no un-cancel."""
    result = db.execute(
        update(InterviewRound)
        .where(InterviewRound.id == round_id, InterviewRound.status == "scheduled")
        .values(status="cancelled", updated_at=func.now())
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    db.commit()
    return get_interview_round(db, round_id)


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
    Adds a webhook receipt row to the session. Raises sqlalchemy.exc.IntegrityError if
    delivery_id was already recorded -- callers must catch this and treat it as a
    duplicate delivery (200 no-op), not an error, since the database's UNIQUE
    constraint is the actual dedup mechanism, not a check-then-act read beforehand.

    Deliberately does NOT commit -- the receipt and the application state change it
    authorizes are one transaction, owned by main.py's webhook route (the same
    caller-owns-the-transaction split create_job_with_description/create_match_result
    already use). This used to commit here, which opened a real window: the
    delivery_id was durably recorded *before* record_interview_result ran, so a crash
    or DB error between the two left the delivery permanently marked as seen while
    the application was never updated -- and because the UNIQUE constraint is the
    dedup mechanism, every VoiceHR retry of that same delivery then returned
    "duplicate_ignored", discarding a real interview result with no recovery path.
    Only `db.flush()`, which both assigns event.id and is what makes a concurrent
    duplicate delivery block on the unique index until this transaction resolves
    (it then correctly loses with IntegrityError, exactly as before).
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
    db.flush()  # assign event.id (and surface a duplicate delivery_id) without committing
    return event


def mark_webhook_event_processed(db: Session, event_id: uuid.UUID, processed: bool) -> None:
    """Deliberately does NOT commit -- same single-webhook-transaction rule as
    insert_webhook_event above; main.py's webhook route commits once at the end."""
    db.execute(
        update(InterviewWebhookEvent)
        .where(InterviewWebhookEvent.id == event_id)
        .values(processed=processed)
    )
    db.flush()
