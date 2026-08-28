"""
Integration tests for Resume Intelligence — candidates table, dedup, and contact endpoint.

All tests run against the live Postgres container (localhost:5432).
Each test runs inside a transaction that is rolled back afterwards, so the
candidates table is left clean between tests.

Requirements to run:
  - Docker Postgres running: `docker compose up postgres -d` from infra/
  - Migration applied: `alembic upgrade head` inside the container OR
    manually via the DATABASE_URL pointing at localhost.
"""
import uuid

import pytest
from sqlalchemy.orm import Session

# conftest.py adds both the project root and the service dir to sys.path.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candidate_json(
    first_name="Alice",
    last_name="Dupont",
    email="alice@example.com",
    phone="+33600000000",
) -> dict:
    """Minimal candidate_json mimicking the parser's output structure."""
    return {
        "basics": {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": phone,
            "job_title": "Software Engineer",
        },
        "work": [],
        "meta": {"confidence": 0.9},
    }


# ---------------------------------------------------------------------------
# normalize_email (pure unit — no DB needed)
# ---------------------------------------------------------------------------

def test_normalize_email_lowercases():
    from db import normalize_email
    assert normalize_email("Alice@Example.COM") == "alice@example.com"


def test_normalize_email_strips_whitespace():
    from db import normalize_email
    assert normalize_email("  bob@example.com  ") == "bob@example.com"


def test_normalize_email_none_returns_none():
    from db import normalize_email
    assert normalize_email(None) is None


def test_normalize_email_empty_returns_none():
    from db import normalize_email
    assert normalize_email("") is None


# ---------------------------------------------------------------------------
# store_candidate — candidate creation
# ---------------------------------------------------------------------------

def test_store_candidate_creates_new(db_session: Session):
    from db import store_candidate, Candidate

    cj = _make_candidate_json()
    candidate_id, is_new = store_candidate(db_session, cj, raw_text="some extracted text")

    assert is_new is True
    assert isinstance(candidate_id, uuid.UUID)

    row = db_session.query(Candidate).filter_by(id=candidate_id).first()
    assert row is not None
    assert row.name == "Alice Dupont"
    assert row.email == "alice@example.com"
    assert row.email_normalized == "alice@example.com"
    assert row.phone == "+33600000000"


def test_store_candidate_returns_uuid(db_session: Session):
    from db import store_candidate

    cj = _make_candidate_json(email="uuid_test@example.com")
    candidate_id, _ = store_candidate(db_session, cj, raw_text="text")
    assert isinstance(candidate_id, uuid.UUID)


def test_store_candidate_persists_raw_text(db_session: Session):
    from db import store_candidate, Candidate

    raw = "Experienced Python engineer with FastAPI skills."
    cj = _make_candidate_json(email="rawtext@example.com")
    candidate_id, _ = store_candidate(db_session, cj, raw_text=raw)

    row = db_session.query(Candidate).filter_by(id=candidate_id).first()
    assert row.raw_text == raw


def test_store_candidate_persists_candidate_json(db_session: Session):
    from db import store_candidate, Candidate

    cj = _make_candidate_json(email="json_persist@example.com")
    candidate_id, _ = store_candidate(db_session, cj, raw_text="t")

    row = db_session.query(Candidate).filter_by(id=candidate_id).first()
    assert row.candidate_json["basics"]["email"] == "json_persist@example.com"


# ---------------------------------------------------------------------------
# Deduplication by normalized email
# ---------------------------------------------------------------------------

def test_duplicate_email_returns_existing_id(db_session: Session):
    from db import store_candidate

    cj = _make_candidate_json(email="dedup@example.com")
    id1, is_new1 = store_candidate(db_session, cj, raw_text="first submission")
    id2, is_new2 = store_candidate(db_session, cj, raw_text="second submission")

    assert is_new1 is True
    assert is_new2 is False
    assert id1 == id2


def test_email_dedup_is_case_insensitive(db_session: Session):
    from db import store_candidate

    cj1 = _make_candidate_json(email="CaseTest@Example.COM")
    cj2 = _make_candidate_json(email="casetest@example.com")

    id1, is_new1 = store_candidate(db_session, cj1, raw_text="t")
    id2, is_new2 = store_candidate(db_session, cj2, raw_text="t")

    assert is_new1 is True
    assert is_new2 is False
    assert id1 == id2


def test_email_dedup_ignores_whitespace(db_session: Session):
    from db import store_candidate

    cj1 = _make_candidate_json(email="  spaces@example.com  ")
    cj2 = _make_candidate_json(email="spaces@example.com")

    id1, _ = store_candidate(db_session, cj1, raw_text="t")
    id2, is_new2 = store_candidate(db_session, cj2, raw_text="t")

    assert id1 == id2
    assert is_new2 is False


# ---------------------------------------------------------------------------
# Missing email — no dedup, always insert
# ---------------------------------------------------------------------------

def test_missing_email_always_inserts(db_session: Session):
    from db import store_candidate

    cj = _make_candidate_json(email=None)
    id1, is_new1 = store_candidate(db_session, cj, raw_text="t")
    id2, is_new2 = store_candidate(db_session, cj, raw_text="t")

    assert is_new1 is True
    assert is_new2 is True
    assert id1 != id2


def test_missing_email_stores_none_normalized(db_session: Session):
    from db import store_candidate, Candidate

    cj = _make_candidate_json(email=None)
    candidate_id, _ = store_candidate(db_session, cj, raw_text="t")

    row = db_session.query(Candidate).filter_by(id=candidate_id).first()
    assert row.email_normalized is None
    assert row.email is None


def test_missing_name_falls_back_to_unknown(db_session: Session):
    from db import store_candidate, Candidate

    cj = {"basics": {"email": "noname@example.com"}, "work": [], "meta": {}}
    candidate_id, _ = store_candidate(db_session, cj, raw_text="t")

    row = db_session.query(Candidate).filter_by(id=candidate_id).first()
    assert row.name == "Unknown"


# ---------------------------------------------------------------------------
# GET /v1/candidates/{candidate_id}/contact — happy path + auth
# ---------------------------------------------------------------------------

def test_contact_endpoint_returns_data(client, db_session: Session):
    from db import store_candidate

    cj = _make_candidate_json(email="contact_test@example.com", phone="+33600000001")
    candidate_id, _ = store_candidate(db_session, cj, raw_text="t")

    resp = client.get(
        f"/v1/candidates/{candidate_id}/contact",
        headers={"x-api-key": "test-service-key"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate_id"] == str(candidate_id)
    assert body["name"] == "Alice Dupont"
    assert body["email"] == "contact_test@example.com"
    assert body["phone"] == "+33600000001"


def test_contact_endpoint_no_key_returns_401(client, db_session: Session):
    from db import store_candidate

    cj = _make_candidate_json(email="auth_test@example.com")
    candidate_id, _ = store_candidate(db_session, cj, raw_text="t")

    resp = client.get(f"/v1/candidates/{candidate_id}/contact")
    assert resp.status_code == 401


def test_contact_endpoint_wrong_key_returns_401(client, db_session: Session):
    from db import store_candidate

    cj = _make_candidate_json(email="wrongkey@example.com")
    candidate_id, _ = store_candidate(db_session, cj, raw_text="t")

    resp = client.get(
        f"/v1/candidates/{candidate_id}/contact",
        headers={"x-api-key": "not-the-right-key"},
    )
    assert resp.status_code == 401


def test_contact_endpoint_unknown_id_returns_404(client):
    resp = client.get(
        f"/v1/candidates/{uuid.uuid4()}/contact",
        headers={"x-api-key": "test-service-key"},
    )
    assert resp.status_code == 404


def test_contact_endpoint_phone_optional(client, db_session: Session):
    from db import store_candidate

    cj = _make_candidate_json(email="nophone@example.com", phone=None)
    candidate_id, _ = store_candidate(db_session, cj, raw_text="t")

    resp = client.get(
        f"/v1/candidates/{candidate_id}/contact",
        headers={"x-api-key": "test-service-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["phone"] is None


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "resume-intelligence"}


# ---------------------------------------------------------------------------
# Observability: correlation ID propagation + no PII in logs
# ---------------------------------------------------------------------------

def test_correlation_id_generated_when_absent(client):
    resp = client.get("/health")
    assert "x-correlation-id" in {k.lower() for k in resp.headers.keys()}
    assert len(resp.headers["x-correlation-id"]) > 0


def test_correlation_id_echoed_when_provided(client):
    resp = client.get("/health", headers={"X-Correlation-ID": "caller-supplied-id-123"})
    assert resp.headers["x-correlation-id"] == "caller-supplied-id-123"


def test_correlation_id_differs_across_requests(client):
    first = client.get("/health").headers["x-correlation-id"]
    second = client.get("/health").headers["x-correlation-id"]
    assert first != second


def test_contact_lookup_never_logs_pii(client, db_session: Session, caplog):
    """The contact endpoint returns name/email/phone in its response body -- this test
    asserts none of that PII ever appears in the service's own log output for the
    request that served it."""
    import logging

    from db import store_candidate

    cj = _make_candidate_json(
        first_name="Sensitive", last_name="Candidate",
        email="sensitive.pii.marker@example.com", phone="+33612345678",
    )
    candidate_id, _ = store_candidate(db_session, cj, raw_text="t")

    with caplog.at_level(logging.DEBUG):
        resp = client.get(
            f"/v1/candidates/{candidate_id}/contact",
            headers={"x-api-key": "test-service-key"},
        )
    assert resp.status_code == 200
    assert resp.json()["email"] == "sensitive.pii.marker@example.com"  # sanity: PII really was returned

    log_text = caplog.text
    assert "sensitive.pii.marker@example.com" not in log_text
    assert "+33612345678" not in log_text
    assert "Sensitive Candidate" not in log_text
    assert "Sensitive" not in log_text


def test_service_api_key_never_logged(client, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        client.get(f"/v1/candidates/{uuid.uuid4()}/contact", headers={"x-api-key": "test-service-key"})
        client.get(f"/v1/candidates/{uuid.uuid4()}/contact", headers={"x-api-key": "wrong-key-value"})

    assert "test-service-key" not in caplog.text
    assert "wrong-key-value" not in caplog.text
