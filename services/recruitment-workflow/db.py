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
