"""
Integration tests for Second Brain — candidate knowledge ingestion.

Run inside the csb-second-brain container after copying conftest_second_brain.py
as /app/conftest.py and this file as /app/test_second_brain.py:

    docker exec csb-second-brain python -m pytest /app/test_second_brain.py -v
"""
import uuid

import pytest
from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_KEY = "test-service-key"
_INGEST_URL = "/v1/knowledge/candidates"


def _make_payload(
    candidate_id: str | None = None,
    skills: list[str] | None = None,
    years: float | None = 3.5,
    titles: list[str] | None = None,
    raw_text: str = "Experienced Python engineer with FastAPI and PostgreSQL skills.",
    source_version: str = "1",
) -> dict:
    return {
        "candidate_id": candidate_id or str(uuid.uuid4()),
        "skills": skills if skills is not None else ["Python", "FastAPI"],
        "years_of_experience": years,
        "job_titles": titles if titles is not None else ["Software Engineer"],
        "raw_text": raw_text,
        "source_version": source_version,
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "second-brain"}


# ---------------------------------------------------------------------------
# Valid ingestion
# ---------------------------------------------------------------------------

def test_ingest_valid_candidate_returns_200(client):
    resp = client.post(
        _INGEST_URL,
        json=_make_payload(),
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "candidate_id" in body


def test_ingest_response_echoes_candidate_id(client):
    cid = str(uuid.uuid4())
    resp = client.post(
        _INGEST_URL,
        json=_make_payload(candidate_id=cid),
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["candidate_id"] == cid


# ---------------------------------------------------------------------------
# Database persistence
# ---------------------------------------------------------------------------

def test_ingest_persists_row(client, db_session: Session):
    from db import CandidateKnowledge

    cid = uuid.uuid4()
    payload = _make_payload(
        candidate_id=str(cid),
        skills=["Python", "SQL"],
        titles=["Backend Developer"],
        raw_text="Five years building APIs in Python.",
        source_version="2",
    )
    client.post(_INGEST_URL, json=payload, headers={"x-api-key": _VALID_KEY})

    row = db_session.query(CandidateKnowledge).filter_by(candidate_id=cid).first()
    assert row is not None
    assert row.skills == ["Python", "SQL"]
    assert row.job_titles == ["Backend Developer"]
    assert row.raw_text == "Five years building APIs in Python."
    assert row.source_version == "2"
    assert row.years_of_experience == 3.5


def test_ingest_stores_embedding_vector(client, db_session: Session):
    from db import CandidateKnowledge, EMBEDDING_DIMENSIONS

    cid = uuid.uuid4()
    client.post(
        _INGEST_URL,
        json=_make_payload(candidate_id=str(cid)),
        headers={"x-api-key": _VALID_KEY},
    )

    row = db_session.query(CandidateKnowledge).filter_by(candidate_id=cid).first()
    assert row is not None
    assert row.embedding is not None
    assert len(row.embedding) == EMBEDDING_DIMENSIONS


def test_ingest_null_years_of_experience(client, db_session: Session):
    from db import CandidateKnowledge

    cid = uuid.uuid4()
    client.post(
        _INGEST_URL,
        json=_make_payload(candidate_id=str(cid), years=None),
        headers={"x-api-key": _VALID_KEY},
    )

    row = db_session.query(CandidateKnowledge).filter_by(candidate_id=cid).first()
    assert row.years_of_experience is None


# ---------------------------------------------------------------------------
# Upsert behavior
# ---------------------------------------------------------------------------

def test_upsert_updates_existing_row(client, db_session: Session):
    from db import CandidateKnowledge

    cid = str(uuid.uuid4())
    client.post(
        _INGEST_URL,
        json=_make_payload(candidate_id=cid, skills=["Python"], source_version="1"),
        headers={"x-api-key": _VALID_KEY},
    )
    client.post(
        _INGEST_URL,
        json=_make_payload(candidate_id=cid, skills=["Python", "Rust"], source_version="2"),
        headers={"x-api-key": _VALID_KEY},
    )

    rows = db_session.query(CandidateKnowledge).filter_by(candidate_id=uuid.UUID(cid)).all()
    assert len(rows) == 1
    assert rows[0].skills == ["Python", "Rust"]
    assert rows[0].source_version == "2"


def test_upsert_preserves_created_at(client, db_session: Session):
    from db import CandidateKnowledge

    cid = str(uuid.uuid4())
    client.post(
        _INGEST_URL,
        json=_make_payload(candidate_id=cid, source_version="1"),
        headers={"x-api-key": _VALID_KEY},
    )
    row_before = db_session.query(CandidateKnowledge).filter_by(
        candidate_id=uuid.UUID(cid)
    ).first()
    created_at_original = row_before.created_at

    client.post(
        _INGEST_URL,
        json=_make_payload(candidate_id=cid, source_version="2"),
        headers={"x-api-key": _VALID_KEY},
    )
    db_session.expire(row_before)
    row_after = db_session.query(CandidateKnowledge).filter_by(
        candidate_id=uuid.UUID(cid)
    ).first()
    assert row_after.created_at == created_at_original


# ---------------------------------------------------------------------------
# PII boundary enforcement
# ---------------------------------------------------------------------------

def test_pii_email_field_rejected(client):
    payload = _make_payload()
    payload["email"] = "alice@example.com"
    resp = client.post(_INGEST_URL, json=payload, headers={"x-api-key": _VALID_KEY})
    assert resp.status_code == 422


def test_pii_name_field_rejected(client):
    payload = _make_payload()
    payload["name"] = "Alice Dupont"
    resp = client.post(_INGEST_URL, json=payload, headers={"x-api-key": _VALID_KEY})
    assert resp.status_code == 422


def test_pii_phone_field_rejected(client):
    payload = _make_payload()
    payload["phone"] = "+33600000000"
    resp = client.post(_INGEST_URL, json=payload, headers={"x-api-key": _VALID_KEY})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Invalid payloads
# ---------------------------------------------------------------------------

def test_missing_candidate_id_rejected(client):
    payload = _make_payload()
    del payload["candidate_id"]
    resp = client.post(_INGEST_URL, json=payload, headers={"x-api-key": _VALID_KEY})
    assert resp.status_code == 422


def test_empty_raw_text_rejected(client):
    payload = _make_payload(raw_text="")
    resp = client.post(_INGEST_URL, json=payload, headers={"x-api-key": _VALID_KEY})
    assert resp.status_code == 422


def test_invalid_uuid_rejected(client):
    payload = _make_payload(candidate_id="not-a-uuid")
    resp = client.post(_INGEST_URL, json=payload, headers={"x-api-key": _VALID_KEY})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_missing_api_key_returns_401(client):
    resp = client.post(_INGEST_URL, json=_make_payload())
    assert resp.status_code == 401


def test_wrong_api_key_returns_401(client):
    resp = client.post(
        _INGEST_URL,
        json=_make_payload(),
        headers={"x-api-key": "wrong-key"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Embedding failure handling
# ---------------------------------------------------------------------------

def test_embedding_failure_returns_502(client_failing_llm):
    resp = client_failing_llm.post(
        _INGEST_URL,
        json=_make_payload(),
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 502
    assert "Embedding failed" in resp.json()["detail"]
