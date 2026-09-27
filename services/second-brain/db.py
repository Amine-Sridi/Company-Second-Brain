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
    # Which embedding space this vector belongs to. Cosine similarity is only meaningful
    # within one space, and a cross-space query returns plausible nonsense rather than an
    # error -- see migration 0004 and SCALING_AND_FUTURE_IMPROVEMENTS.md §5.
    embedding_model_version: str = Column(Text, nullable=False)
    # NULL means "no task_type was set", which is the state of every vector written
    # before this column existed. Setting a task_type changes the space.
    embedding_task_type: Optional[str] = Column(Text, nullable=True)
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class CandidateFeedback(Base):
    """
    PII-free write-back of a recruiter's accept/reject decision from RWS. candidate_id
    and application_id are plain UUID columns, not foreign keys -- see the 0003
    migration docstring for why (a cross-schema FK is impossible anyway, and this
    endpoint must accept feedback even for a candidate_id no longer in
    candidate_knowledge).
    """

    __tablename__ = "candidate_feedback"
    __table_args__ = {"schema": "second_brain"}

    id: uuid.UUID = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    candidate_id: uuid.UUID = Column(UUID(as_uuid=True), nullable=False)
    application_id: uuid.UUID = Column(UUID(as_uuid=True), nullable=False, unique=True)
    decision: str = Column(Text, nullable=False)
    job_title: str = Column(Text, nullable=False)
    matched_requirements: list = Column(JSONB, nullable=False)
    missing_requirements: list = Column(JSONB, nullable=False)
    final_score: float = Column(Float, nullable=False)
    decided_at: datetime = Column(DateTime(timezone=True), nullable=False)
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
    embedding_model_version: str,
    embedding_task_type: Optional[str] = None,
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
            embedding_model_version=embedding_model_version,
            embedding_task_type=embedding_task_type,
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
                "embedding_model_version": embedding_model_version,
                "embedding_task_type": embedding_task_type,
                "updated_at": func.now(),
            },
        )
    )
    db.execute(stmt)
    db.commit()


def get_candidate_knowledge(
    db: Session, candidate_id: uuid.UUID
) -> Optional[CandidateKnowledge]:
    """Fetch PII-free candidate knowledge by candidate_id, or None if not found."""
    return (
        db.query(CandidateKnowledge)
        .filter(CandidateKnowledge.candidate_id == candidate_id)
        .first()
    )


def get_candidate_knowledge_many(
    db: Session, candidate_ids: list[uuid.UUID]
) -> list[CandidateKnowledge]:
    """
    Fetch many candidates in one query.

    Exists because RWS's submit_job looped `await get_candidate(...)` once per retrieved
    candidate -- N sequential cross-service round-trips inside a single open transaction
    (audit M2). One `IN` query replaces N.

    Missing ids are simply absent from the result rather than raising: retrieval can
    return a candidate that was deleted moments later, and the caller already has to
    tolerate that. Order is not guaranteed, so callers index by candidate_id.
    """
    if not candidate_ids:
        return []
    return (
        db.query(CandidateKnowledge)
        .filter(CandidateKnowledge.candidate_id.in_(candidate_ids))
        .all()
    )


def search_candidates(
    db: Session,
    query_embedding: list[float],
    top_k: int,
    candidate_ids: list[uuid.UUID] | None = None,
    embedding_model_version: str | None = None,
    embedding_task_type: str | None = None,
) -> list[tuple[uuid.UUID, float]]:
    """
    Return up to top_k candidates ordered by cosine similarity (descending).

    Uses the HNSW index on embedding via the <=> cosine-distance operator.
    Similarity = 1 - cosine_distance, clamped to [0, 1] so negative cosine
    similarities (anti-parallel embeddings) are floored to 0.

    candidate_ids: when provided, restricts the search to that subset.
    An empty list returns nothing immediately.

    embedding_model_version / embedding_task_type: when given, **only vectors from that
    same embedding space are searched**. This is a correctness gate, not a filter for
    convenience. Cosine distance across two spaces still returns numbers and still ranks
    candidates -- it is simply meaningless, and it would flow into `semantic_score` and
    then into an immutable MatchResult with no trace. Returning *fewer* results (or none)
    during a re-embed is the loud, correct behaviour; returning plausible nonsense is not.
    Passing None searches every generation and is only appropriate for maintenance tooling
    that knows what it is doing.
    """
    if candidate_ids is not None and len(candidate_ids) == 0:
        return []

    # Embed the query vector as a literal in the SQL string. This is safe
    # because the values come from our own embedding model (a Python list of
    # floats), not from user input. str(float) never produces quotes or SQL
    # metacharacters. This avoids a psycopg3 + SQLAlchemy text() conflict
    # where ::vector casts on bound parameters fail to compile.
    vec_literal = "[" + ",".join(map(str, query_embedding)) + "]"

    conditions: list[str] = []
    params: dict = {"top_k": top_k}

    if candidate_ids:
        # UUIDs are Pydantic-validated hex strings — safe to format directly.
        placeholders = ", ".join(f"'{cid}'" for cid in candidate_ids)
        conditions.append(f"candidate_id IN ({placeholders})")

    if embedding_model_version is not None:
        # Bound parameter, not interpolated: unlike the vector literal and the UUIDs,
        # this is a plain string and has no reason to be formatted into the SQL.
        conditions.append("embedding_model_version = :embedding_model_version")
        params["embedding_model_version"] = embedding_model_version
        # IS NOT DISTINCT FROM so NULL matches NULL -- "no task_type set" is itself a
        # space, and it is the one every pre-0004 vector belongs to.
        conditions.append("embedding_task_type IS NOT DISTINCT FROM :embedding_task_type")
        params["embedding_task_type"] = embedding_task_type

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = text(f"""
        SELECT candidate_id,
               GREATEST(0.0, LEAST(1.0, 1.0 - (embedding <=> '{vec_literal}'::vector))) AS similarity
        FROM second_brain.candidate_knowledge
        {where}
        ORDER BY embedding <=> '{vec_literal}'::vector
        LIMIT :top_k
    """)

    rows = db.execute(sql, params).fetchall()
    return [(uuid.UUID(str(row.candidate_id)), float(row.similarity)) for row in rows]


def upsert_candidate_feedback(
    db: Session,
    candidate_id: uuid.UUID,
    application_id: uuid.UUID,
    decision: str,
    job_title: str,
    matched_requirements: list[str],
    missing_requirements: list[str],
    final_score: float,
    decided_at: datetime,
) -> None:
    """
    Insert or update recruiter-decision feedback, keyed by application_id.

    Idempotent by design: a decision happens at most once per application, but the
    write-back call from RWS is fire-and-forget (a FastAPI BackgroundTask) and may be
    delivered more than once in practice (e.g. a retry after a timed-out response that
    actually succeeded). ON CONFLICT (application_id) DO UPDATE makes a repeat delivery
    overwrite the same row with the same data rather than create a duplicate.
    """
    stmt = (
        pg_insert(CandidateFeedback)
        .values(
            candidate_id=candidate_id,
            application_id=application_id,
            decision=decision,
            job_title=job_title,
            matched_requirements=matched_requirements,
            missing_requirements=missing_requirements,
            final_score=final_score,
            decided_at=decided_at,
        )
        .on_conflict_do_update(
            index_elements=["application_id"],
            set_={
                "candidate_id": candidate_id,
                "decision": decision,
                "job_title": job_title,
                "matched_requirements": matched_requirements,
                "missing_requirements": missing_requirements,
                "final_score": final_score,
                "decided_at": decided_at,
                "updated_at": func.now(),
            },
        )
    )
    db.execute(stmt)
    db.commit()


def get_candidate_feedback_by_application(
    db: Session, application_id: uuid.UUID
) -> Optional[CandidateFeedback]:
    return (
        db.query(CandidateFeedback)
        .filter(CandidateFeedback.application_id == application_id)
        .first()
    )
