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


def search_candidates(
    db: Session,
    query_embedding: list[float],
    top_k: int,
    candidate_ids: list[uuid.UUID] | None = None,
) -> list[tuple[uuid.UUID, float]]:
    """
    Return up to top_k candidates ordered by cosine similarity (descending).

    Uses the HNSW index on embedding via the <=> cosine-distance operator.
    Similarity = 1 - cosine_distance, clamped to [0, 1] so negative cosine
    similarities (anti-parallel embeddings) are floored to 0.

    candidate_ids: when provided, restricts the search to that subset.
    An empty list returns nothing immediately.
    """
    if candidate_ids is not None and len(candidate_ids) == 0:
        return []

    # Embed the query vector as a literal in the SQL string. This is safe
    # because the values come from our own embedding model (a Python list of
    # floats), not from user input. str(float) never produces quotes or SQL
    # metacharacters. This avoids a psycopg3 + SQLAlchemy text() conflict
    # where ::vector casts on bound parameters fail to compile.
    vec_literal = "[" + ",".join(map(str, query_embedding)) + "]"

    if candidate_ids:
        # UUIDs are Pydantic-validated hex strings — safe to format directly.
        placeholders = ", ".join(f"'{cid}'" for cid in candidate_ids)
        sql = text(f"""
            SELECT candidate_id,
                   GREATEST(0.0, LEAST(1.0, 1.0 - (embedding <=> '{vec_literal}'::vector))) AS similarity
            FROM second_brain.candidate_knowledge
            WHERE candidate_id IN ({placeholders})
            ORDER BY embedding <=> '{vec_literal}'::vector
            LIMIT :top_k
        """)
    else:
        sql = text(f"""
            SELECT candidate_id,
                   GREATEST(0.0, LEAST(1.0, 1.0 - (embedding <=> '{vec_literal}'::vector))) AS similarity
            FROM second_brain.candidate_knowledge
            ORDER BY embedding <=> '{vec_literal}'::vector
            LIMIT :top_k
        """)

    rows = db.execute(sql, {"top_k": top_k}).fetchall()
    return [(uuid.UUID(str(row.candidate_id)), float(row.similarity)) for row in rows]
