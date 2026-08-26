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
    of a qualified MatchResult. State machine: selected -> accepted | rejected (terminal).
    Future VoiceHR integration will add invited/interviewed states between selected and
    the final decision -- not implemented yet, per current scope.
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
    selected_by: uuid.UUID = Column(
        UUID(as_uuid=True), ForeignKey("recruitment.recruiters.id"), nullable=False
    )
    selected_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
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
) -> tuple[Job, JobDescription]:
    """Create a new Job and its first (version 1) JobDescription in one transaction."""
    job = Job(title=title, threshold=threshold)
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


def get_match_result(db: Session, match_id: uuid.UUID) -> Optional[MatchResult]:
    return db.query(MatchResult).filter(MatchResult.id == match_id).first()


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
) -> Application:
    """
    Create a new Application in the 'selected' state. Raises sqlalchemy.exc.IntegrityError
    if this candidate was already selected for this job (job_id, candidate_id unique) or if
    this match_result was already used to create an application (match_result_id unique) --
    callers should catch this and translate it to a 409 Conflict rather than a check-then-act
    race, since the uniqueness is enforced atomically by the database, not by application code.
    """
    application = Application(
        job_id=job_id,
        match_result_id=match_result_id,
        candidate_id=candidate_id,
        selected_by=selected_by,
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


def transition_application(
    db: Session,
    application_id: uuid.UUID,
    recruiter_id: uuid.UUID,
    decision: str,
) -> Optional[Application]:
    """
    Atomically transition an application from 'selected' to 'accepted' or 'rejected',
    recording an audit row on success.

    Returns None if the application does not exist, or if it is not currently in the
    'selected' state (already decided). The transition itself is a single conditional
    UPDATE ... WHERE status = 'selected' -- this is what makes it safe under concurrent
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
        .where(Application.id == application_id, Application.status == "selected")
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
