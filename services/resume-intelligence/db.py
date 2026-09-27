"""
Database layer for Resume Intelligence.

Owns the SQLAlchemy Candidate model and the store_candidate upsert function.
All PII (name, email, phone) lives exclusively here — no other service writes
to this schema or reads these columns directly.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Generator, Optional

from sqlalchemy import Boolean, Column, DateTime, Integer, Text, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


# ---------------------------------------------------------------------------
# Engine (lazy — avoids crashing at import if DATABASE_URL is not yet set)
# ---------------------------------------------------------------------------

_engine = None
_SessionLocal = None


def _get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Copy .env.example to .env and fill it in, or export it before running."
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
        _SessionLocal = sessionmaker(
            bind=_get_engine(), autocommit=False, autoflush=False
        )
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency — yields a DB session and closes it after the request."""
    db = _get_session_factory()()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = {"schema": "resume_intel"}

    id: uuid.UUID = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    email_normalized: Optional[str] = Column(Text, unique=True, nullable=True, index=True)
    name: str = Column(Text, nullable=False)
    email: Optional[str] = Column(Text, nullable=True)
    phone: Optional[str] = Column(Text, nullable=True)
    raw_text: str = Column(Text, nullable=False)
    candidate_json: dict = Column(JSONB, nullable=False)
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class IngestJob(Base):
    """
    Durable state for one queued resume ingestion.

    This exists so **Redis is never the source of truth**. ARQ keeps its own job records,
    but Redis is a cache/broker here: it can be flushed, evicted or restarted, and when
    that happens the only acceptable loss is throughput, never the knowledge that a
    recruiter uploaded a CV. A row lands here before anything is enqueued, so a job that
    Redis loses is still visible as `queued` and can be re-enqueued.

    Deliberately holds **no PII**: no name, no email, no extracted text. `candidate_id`
    is the opaque link to the real record once parsing succeeds, and `error` is a generic
    string (the pipeline's own exception messages can embed extracted candidate data,
    which is why api.py already refuses to log them).
    """

    __tablename__ = "ingest_jobs"
    __table_args__ = {"schema": "resume_intel"}

    id: uuid.UUID = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # queued -> running -> succeeded | failed
    status: str = Column(Text, nullable=False, server_default=text("'queued'"))
    # Path on the resume_uploads volume, mounted only by this service and its worker.
    upload_path: str = Column(Text, nullable=False)
    original_filename: Optional[str] = Column(Text, nullable=True)
    candidate_id: Optional[uuid.UUID] = Column(UUID(as_uuid=True), nullable=True)
    is_new: Optional[bool] = Column(Boolean, nullable=True)
    # Generic reason only -- never an exception message (may contain PII).
    error: Optional[str] = Column(Text, nullable=True)
    attempts: int = Column(Integer, nullable=False, server_default=text("0"))
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def normalize_email(email: str | None) -> str | None:
    """Lowercase + strip whitespace. Returns None if email is absent."""
    if not email:
        return None
    return email.strip().lower()


def store_candidate(
    db: Session,
    candidate_json: dict,
    raw_text: str,
) -> tuple[uuid.UUID, bool]:
    """
    Upsert a candidate by normalized email.

    Returns (candidate_id, is_new).

    Dedup rule: if a candidate with the same normalized email already exists,
    return the existing id without modifying any data (immutable after creation).
    If the parsed JSON has no email, always insert a new row — we cannot dedup
    without an identifier, so we never silently merge unknown candidates.
    """
    basics = candidate_json.get("basics") or {}
    first_name = (basics.get("first_name") or "").strip()
    last_name = (basics.get("last_name") or "").strip()
    name = f"{first_name} {last_name}".strip() or "Unknown"
    email_raw: str | None = basics.get("email") or None
    phone: str | None = basics.get("phone") or None
    email_norm = normalize_email(email_raw)

    if email_norm:
        existing = (
            db.query(Candidate)
            .filter(Candidate.email_normalized == email_norm)
            .first()
        )
        if existing:
            return existing.id, False

    candidate = Candidate(
        email_normalized=email_norm,
        name=name,
        email=email_raw,
        phone=phone,
        raw_text=raw_text,
        candidate_json=candidate_json,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate.id, True


def list_candidates(db: Session) -> list[Candidate]:
    """
    Every candidate, newest first. No pagination/search: this project's current scale
    doesn't need it (see FRONTEND_HANDOFF.md's Candidates-page backend prerequisites) --
    revisit only if a real dataset size makes the full list impractical, matching the
    same rule already applied to RWS's GET /v1/jobs.

    Callers must read only candidate_id/name/email/created_at off the returned rows --
    this function itself returns full Candidate ORM objects (raw_text/candidate_json/
    phone included) purely because that's the natural SQLAlchemy return shape; the
    approved-field allow-list is enforced at the API response-model boundary
    (api.py's CandidateListItem), not here.
    """
    return db.query(Candidate).order_by(Candidate.created_at.desc()).all()


# ---------------------------------------------------------------------------
# Ingest job state
#
# Every transition is its own small committed write. The worker runs outside any
# request, so there is no surrounding transaction to ride on, and a crash mid-pipeline
# must leave a row that says so rather than an invisible one.
# ---------------------------------------------------------------------------

def create_ingest_job(db: Session, upload_path: str, original_filename: str | None) -> IngestJob:
    """Records the job as `queued` BEFORE it is enqueued, so a job Redis never receives
    is still visible and re-enqueueable rather than silently lost."""
    job = IngestJob(upload_path=upload_path, original_filename=original_filename, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_ingest_job(db: Session, job_id: uuid.UUID) -> Optional[IngestJob]:
    return db.query(IngestJob).filter(IngestJob.id == job_id).one_or_none()


def mark_ingest_job_running(db: Session, job_id: uuid.UUID) -> None:
    db.query(IngestJob).filter(IngestJob.id == job_id).update(
        {
            IngestJob.status: "running",
            IngestJob.attempts: IngestJob.attempts + 1,
            IngestJob.updated_at: text("now()"),
        },
        synchronize_session=False,
    )
    db.commit()


def mark_ingest_job_succeeded(
    db: Session, job_id: uuid.UUID, candidate_id: uuid.UUID, is_new: bool
) -> None:
    db.query(IngestJob).filter(IngestJob.id == job_id).update(
        {
            IngestJob.status: "succeeded",
            IngestJob.candidate_id: candidate_id,
            IngestJob.is_new: is_new,
            IngestJob.error: None,
            IngestJob.updated_at: text("now()"),
        },
        synchronize_session=False,
    )
    db.commit()


def mark_ingest_job_failed(db: Session, job_id: uuid.UUID, error: str) -> None:
    """`error` must be a generic, caller-chosen reason -- never an exception message.
    The extraction pipeline's exceptions can embed candidate name/email/phone, which is
    exactly why api.py logs only the exception *type*."""
    db.query(IngestJob).filter(IngestJob.id == job_id).update(
        {
            IngestJob.status: "failed",
            IngestJob.error: error,
            IngestJob.updated_at: text("now()"),
        },
        synchronize_session=False,
    )
    db.commit()
