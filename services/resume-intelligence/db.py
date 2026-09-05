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

from sqlalchemy import Column, DateTime, Text, create_engine, text
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
