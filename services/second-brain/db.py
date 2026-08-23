"""
Database layer for Second Brain.

PII-free by design: this schema must never store name, email, phone, or address.
The only identifier is the opaque candidate_id UUID from Resume Intelligence.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Generator, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, Float, Text, create_engine, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

EMBEDDING_DIMENSIONS = 768


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
            "Copy .env.example to .env and fill in the second_brain_role credentials."
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


class CandidateKnowledge(Base):
    __tablename__ = "candidate_knowledge"
    __table_args__ = {"schema": "second_brain"}

    candidate_id: uuid.UUID = Column(UUID(as_uuid=True), primary_key=True)
    skills: list = Column(JSONB, nullable=False)
    years_of_experience: Optional[float] = Column(Float, nullable=True)
    job_titles: list = Column(JSONB, nullable=False)
    raw_text: str = Column(Text, nullable=False)
    source_version: str = Column(Text, nullable=False)
    embedding = Column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def upsert_candidate_knowledge(
    db: Session,
    candidate_id: uuid.UUID,
    skills: list[str],
    years_of_experience: Optional[float],
    job_titles: list[str],
    raw_text: str,
    source_version: str,
    embedding: list[float],
) -> None:
    """
    Insert or update candidate knowledge by candidate_id.

    On conflict (same candidate_id), all mutable fields are updated and
    updated_at is refreshed. created_at is never overwritten.
    """
    stmt = (
        pg_insert(CandidateKnowledge)
        .values(
            candidate_id=candidate_id,
            skills=skills,
            years_of_experience=years_of_experience,
            job_titles=job_titles,
            raw_text=raw_text,
            source_version=source_version,
            embedding=embedding,
        )
        .on_conflict_do_update(
            index_elements=["candidate_id"],
            set_={
                "skills": skills,
                "years_of_experience": years_of_experience,
                "job_titles": job_titles,
                "raw_text": raw_text,
                "source_version": source_version,
                "embedding": embedding,
                "updated_at": func.now(),
            },
        )
    )
    db.execute(stmt)
    db.commit()
