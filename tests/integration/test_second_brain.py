"""
Integration tests for Second Brain — candidate knowledge ingestion and retrieval.

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


# ===========================================================================
# Candidate detail lookup — GET /v1/knowledge/candidates/{candidate_id}
# ===========================================================================

_KNOWLEDGE_URL = "/v1/knowledge/candidates"


def test_get_candidate_returns_stored_knowledge(client):
    cid = str(uuid.uuid4())
    client.post(
        _INGEST_URL,
        json=_make_payload(
            candidate_id=cid,
            skills=["Python", "SQL"],
            years=4.0,
            titles=["Backend Developer"],
        ),
        headers={"x-api-key": _VALID_KEY},
    )

    resp = client.get(f"{_KNOWLEDGE_URL}/{cid}", headers={"x-api-key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate_id"] == cid
    assert body["skills"] == ["Python", "SQL"]
    assert body["years_of_experience"] == 4.0
    assert body["job_titles"] == ["Backend Developer"]


def test_get_candidate_not_found_returns_404(client):
    resp = client.get(
        f"{_KNOWLEDGE_URL}/{uuid.uuid4()}", headers={"x-api-key": _VALID_KEY}
    )
    assert resp.status_code == 404


def test_get_candidate_missing_key_returns_401(client):
    resp = client.get(f"{_KNOWLEDGE_URL}/{uuid.uuid4()}")
    assert resp.status_code == 401


# ===========================================================================
# Retrieval — POST /v1/retrieve/candidates
# ===========================================================================

_RETRIEVE_URL = "/v1/retrieve/candidates"


def _seed(db_session, candidate_id: uuid.UUID, embedding: list[float], **kw) -> None:
    """Insert a candidate directly with a known embedding, bypassing the LLM mock."""
    from db import upsert_candidate_knowledge
    upsert_candidate_knowledge(
        db_session,
        candidate_id=candidate_id,
        skills=kw.get("skills", ["Python"]),
        years_of_experience=kw.get("years_of_experience", 2.0),
        job_titles=kw.get("job_titles", ["Engineer"]),
        raw_text=kw.get("raw_text", "placeholder"),
        source_version="1",
        embedding=embedding,
    )


# ---------------------------------------------------------------------------
# Basic retrieval
# ---------------------------------------------------------------------------

def test_retrieve_empty_knowledge_base(client):
    resp = client.post(
        _RETRIEVE_URL,
        json={"query_text": "Python engineer", "top_k": 5},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json() == {"matches": []}


def test_retrieve_returns_ingested_candidate(client, db_session):
    cid = uuid.uuid4()
    # Mock returns [0.1]*768; store identical embedding → similarity = 1.0
    _seed(db_session, cid, [0.1] * 768)

    resp = client.post(
        _RETRIEVE_URL,
        json={"query_text": "Python engineer", "top_k": 10},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 200
    matches = resp.json()["matches"]
    assert len(matches) == 1
    assert matches[0]["candidate_id"] == str(cid)


def test_retrieve_response_shape(client, db_session):
    cid = uuid.uuid4()
    _seed(db_session, cid, [0.1] * 768)

    resp = client.post(
        _RETRIEVE_URL,
        json={"query_text": "Python developer", "top_k": 5},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 200
    match = resp.json()["matches"][0]
    assert set(match.keys()) == {"candidate_id", "similarity_score"}
    # No PII fields present
    assert "name" not in match
    assert "email" not in match


# ---------------------------------------------------------------------------
# top-k behaviour
# ---------------------------------------------------------------------------

def test_retrieve_top_k_limits_results(client, db_session):
    for _ in range(4):
        _seed(db_session, uuid.uuid4(), [0.1] * 768)

    resp = client.post(
        _RETRIEVE_URL,
        json={"query_text": "engineer", "top_k": 2},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert len(resp.json()["matches"]) == 2


def test_retrieve_top_k_default_is_20(client, db_session):
    for _ in range(5):
        _seed(db_session, uuid.uuid4(), [0.1] * 768)

    resp = client.post(
        _RETRIEVE_URL,
        json={"query_text": "engineer"},  # no top_k → default 20
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert len(resp.json()["matches"]) == 5  # only 5 exist


# ---------------------------------------------------------------------------
# Similarity ordering and clamping
# ---------------------------------------------------------------------------

def test_retrieve_results_ordered_by_similarity_desc(client, db_session):
    """
    Seed candidates with embeddings at three known cosine distances from the
    mock query vector [0.1]*768, then verify descending order in the response.

    Query q = [0.1]*768 (uniform, norm = 0.1*sqrt(768))

    Candidate A = [1.0]*768  → cos(q, A) = 1.0  (parallel)
    Candidate B = [1.0]*384 + [0.0]*384  → cos(q, B) ≈ 0.707  (half-zero)
    Candidate C = [-1.0]*768 → cos(q, C) = -1.0 → clamped to 0.0  (anti-parallel)
    """
    cid_a, cid_b, cid_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _seed(db_session, cid_a, [1.0] * 768)
    _seed(db_session, cid_b, [1.0] * 384 + [0.0] * 384)
    _seed(db_session, cid_c, [-1.0] * 768)

    resp = client.post(
        _RETRIEVE_URL,
        json={"query_text": "Python engineer", "top_k": 10},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 200
    matches = resp.json()["matches"]
    assert len(matches) == 3

    scores = [m["similarity_score"] for m in matches]
    assert scores == sorted(scores, reverse=True), "Results must be sorted by similarity descending"

    ids = [m["candidate_id"] for m in matches]
    assert ids[0] == str(cid_a), "Highest similarity candidate must come first"
    assert ids[-1] == str(cid_c), "Anti-parallel candidate must come last"


def test_retrieve_similarity_clamped_to_zero(client, db_session):
    """Anti-parallel embedding (cosine similarity = -1) must be clamped to 0.0."""
    cid = uuid.uuid4()
    _seed(db_session, cid, [-1.0] * 768)

    resp = client.post(
        _RETRIEVE_URL,
        json={"query_text": "Python engineer", "top_k": 5},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 200
    matches = resp.json()["matches"]
    assert len(matches) == 1
    assert matches[0]["similarity_score"] == 0.0


def test_retrieve_similarity_within_bounds(client, db_session):
    """All returned similarity scores must be in [0, 1]."""
    for embedding in ([1.0] * 768, [0.1] * 768, [-1.0] * 768):
        _seed(db_session, uuid.uuid4(), embedding)

    resp = client.post(
        _RETRIEVE_URL,
        json={"query_text": "engineer", "top_k": 10},
        headers={"x-api-key": _VALID_KEY},
    )
    for match in resp.json()["matches"]:
        score = match["similarity_score"]
        assert 0.0 <= score <= 1.0, f"Score {score} out of [0, 1]"


# ---------------------------------------------------------------------------
# candidate_ids filter
# ---------------------------------------------------------------------------

def test_retrieve_candidate_ids_filter(client, db_session):
    cid_a, cid_b, cid_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for cid in (cid_a, cid_b, cid_c):
        _seed(db_session, cid, [0.1] * 768)

    resp = client.post(
        _RETRIEVE_URL,
        json={
            "query_text": "engineer",
            "top_k": 10,
            "candidate_ids": [str(cid_a), str(cid_b)],
        },
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 200
    returned_ids = {m["candidate_id"] for m in resp.json()["matches"]}
    assert returned_ids == {str(cid_a), str(cid_b)}
    assert str(cid_c) not in returned_ids


def test_retrieve_empty_candidate_ids_returns_empty(client, db_session):
    _seed(db_session, uuid.uuid4(), [0.1] * 768)

    resp = client.post(
        _RETRIEVE_URL,
        json={"query_text": "engineer", "top_k": 10, "candidate_ids": []},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["matches"] == []


# ---------------------------------------------------------------------------
# Invalid payloads / PII boundary
# ---------------------------------------------------------------------------

def test_retrieve_missing_query_text_rejected(client):
    resp = client.post(
        _RETRIEVE_URL,
        json={"top_k": 5},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_retrieve_empty_query_text_rejected(client):
    resp = client.post(
        _RETRIEVE_URL,
        json={"query_text": "", "top_k": 5},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_retrieve_top_k_zero_rejected(client):
    resp = client.post(
        _RETRIEVE_URL,
        json={"query_text": "engineer", "top_k": 0},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_retrieve_pii_field_rejected(client):
    resp = client.post(
        _RETRIEVE_URL,
        json={"query_text": "engineer", "top_k": 5, "email": "x@example.com"},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_retrieve_missing_key_returns_401(client):
    resp = client.post(
        _RETRIEVE_URL,
        json={"query_text": "Python engineer", "top_k": 5},
    )
    assert resp.status_code == 401


def test_retrieve_wrong_key_returns_401(client):
    resp = client.post(
        _RETRIEVE_URL,
        json={"query_text": "Python engineer", "top_k": 5},
        headers={"x-api-key": "wrong"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Embedding failure
# ---------------------------------------------------------------------------

def test_retrieve_embedding_failure_returns_502(client_failing_llm):
    resp = client_failing_llm.post(
        _RETRIEVE_URL,
        json={"query_text": "Python engineer", "top_k": 5},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 502
    assert "Embedding failed" in resp.json()["detail"]


# ===========================================================================
# Recruiter-decision feedback write-back -- POST /v1/knowledge/candidates/{id}/feedback
# ===========================================================================

def _feedback_url(candidate_id) -> str:
    return f"/v1/knowledge/candidates/{candidate_id}/feedback"


def _make_feedback_payload(
    application_id: str | None = None,
    decision: str = "accepted",
    job_title: str = "Backend Engineer",
    matched_requirements: list[str] | None = None,
    missing_requirements: list[str] | None = None,
    final_score: float = 0.85,
    decided_at: str = "2026-08-27T00:00:00Z",
) -> dict:
    return {
        "application_id": application_id or str(uuid.uuid4()),
        "decision": decision,
        "job_title": job_title,
        "matched_requirements": matched_requirements if matched_requirements is not None else ["Python"],
        "missing_requirements": missing_requirements if missing_requirements is not None else [],
        "final_score": final_score,
        "decided_at": decided_at,
    }


def test_submit_feedback_returns_200(client):
    cid = uuid.uuid4()
    resp = client.post(
        _feedback_url(cid), json=_make_feedback_payload(), headers={"x-api-key": _VALID_KEY}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "application_id" in body


def test_submit_feedback_persists_row(client, db_session: Session):
    from db import CandidateFeedback

    cid = uuid.uuid4()
    app_id = str(uuid.uuid4())
    payload = _make_feedback_payload(
        application_id=app_id,
        decision="rejected",
        job_title="Data Scientist",
        matched_requirements=["Python", "SQL"],
        missing_requirements=["Spark"],
        final_score=0.42,
    )
    client.post(_feedback_url(cid), json=payload, headers={"x-api-key": _VALID_KEY})

    row = (
        db_session.query(CandidateFeedback)
        .filter(CandidateFeedback.application_id == uuid.UUID(app_id))
        .first()
    )
    assert row is not None
    assert row.candidate_id == cid
    assert row.decision == "rejected"
    assert row.job_title == "Data Scientist"
    assert row.matched_requirements == ["Python", "SQL"]
    assert row.missing_requirements == ["Spark"]
    assert row.final_score == 0.42


def test_submit_feedback_is_idempotent_upsert(client, db_session: Session):
    """A repeated delivery for the same application_id overwrites the same row
    instead of creating a duplicate -- this is what makes the fire-and-forget
    write-back from RWS safe to deliver more than once."""
    from db import CandidateFeedback

    cid = uuid.uuid4()
    app_id = str(uuid.uuid4())

    client.post(
        _feedback_url(cid),
        json=_make_feedback_payload(application_id=app_id, decision="accepted", final_score=0.9),
        headers={"x-api-key": _VALID_KEY},
    )
    client.post(
        _feedback_url(cid),
        json=_make_feedback_payload(application_id=app_id, decision="rejected", final_score=0.9),
        headers={"x-api-key": _VALID_KEY},
    )

    rows = (
        db_session.query(CandidateFeedback)
        .filter(CandidateFeedback.application_id == uuid.UUID(app_id))
        .all()
    )
    assert len(rows) == 1
    assert rows[0].decision == "rejected"  # reflects the latest delivery


def test_submit_feedback_different_candidate_same_url_shape(client, db_session: Session):
    """candidate_id is a path parameter and must be stored correctly even though it's
    not repeated in the body."""
    from db import CandidateFeedback

    cid = uuid.uuid4()
    resp = client.post(
        _feedback_url(cid), json=_make_feedback_payload(), headers={"x-api-key": _VALID_KEY}
    )
    app_id = resp.json()["application_id"]

    row = (
        db_session.query(CandidateFeedback)
        .filter(CandidateFeedback.application_id == uuid.UUID(app_id))
        .first()
    )
    assert row.candidate_id == cid


def test_submit_feedback_pii_email_field_rejected(client):
    cid = uuid.uuid4()
    payload = _make_feedback_payload()
    payload["candidate_email"] = "alice@example.com"
    resp = client.post(_feedback_url(cid), json=payload, headers={"x-api-key": _VALID_KEY})
    assert resp.status_code == 422


def test_submit_feedback_pii_name_field_rejected(client):
    cid = uuid.uuid4()
    payload = _make_feedback_payload()
    payload["candidate_name"] = "Alice Dupont"
    resp = client.post(_feedback_url(cid), json=payload, headers={"x-api-key": _VALID_KEY})
    assert resp.status_code == 422


def test_submit_feedback_invalid_decision_rejected(client):
    cid = uuid.uuid4()
    payload = _make_feedback_payload(decision="maybe")
    resp = client.post(_feedback_url(cid), json=payload, headers={"x-api-key": _VALID_KEY})
    assert resp.status_code == 422


def test_submit_feedback_score_out_of_range_rejected(client):
    cid = uuid.uuid4()
    payload = _make_feedback_payload(final_score=1.5)
    resp = client.post(_feedback_url(cid), json=payload, headers={"x-api-key": _VALID_KEY})
    assert resp.status_code == 422


def test_submit_feedback_missing_application_id_rejected(client):
    cid = uuid.uuid4()
    payload = _make_feedback_payload()
    del payload["application_id"]
    resp = client.post(_feedback_url(cid), json=payload, headers={"x-api-key": _VALID_KEY})
    assert resp.status_code == 422


def test_submit_feedback_invalid_candidate_id_in_path_rejected(client):
    resp = client.post(
        "/v1/knowledge/candidates/not-a-uuid/feedback",
        json=_make_feedback_payload(),
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_submit_feedback_missing_key_returns_401(client):
    cid = uuid.uuid4()
    resp = client.post(_feedback_url(cid), json=_make_feedback_payload())
    assert resp.status_code == 401


def test_submit_feedback_wrong_key_returns_401(client):
    cid = uuid.uuid4()
    resp = client.post(
        _feedback_url(cid), json=_make_feedback_payload(), headers={"x-api-key": "wrong"}
    )
    assert resp.status_code == 401


def test_submit_feedback_accepts_candidate_with_no_knowledge_row(client, db_session: Session):
    """Feedback must be accepted even for a candidate_id with no candidate_knowledge
    row (e.g. it was never ingested, or was ingested and later removed) -- this table
    is not a foreign key into candidate_knowledge, by design."""
    from db import CandidateFeedback

    cid = uuid.uuid4()  # deliberately never ingested via /v1/knowledge/candidates
    resp = client.post(
        _feedback_url(cid), json=_make_feedback_payload(), headers={"x-api-key": _VALID_KEY}
    )
    assert resp.status_code == 200

    row = db_session.query(CandidateFeedback).filter(CandidateFeedback.candidate_id == cid).first()
    assert row is not None
