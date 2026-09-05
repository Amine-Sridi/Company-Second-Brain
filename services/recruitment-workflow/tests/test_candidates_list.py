"""
tests/test_candidates_list.py
------------------------------
Auth-boundary, response-shape and error-semantics tests for GET /v1/candidates on RWS.

Architecture under test: this route transiently proxies Resume Intelligence's own
GET /v1/candidates for the recruiter UI's Candidates page and persists nothing in RWS
-- the same shape as GET /v1/candidates/{id}/identity (see test_candidate_identity.py).
No real Postgres or Resume Intelligence is required: get_recruiter_by_id and
resume_intelligence_client.list_candidates are both mocked.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
import resume_intelligence_client
from auth import create_access_token
from resume_intelligence_client import ResumeIntelligenceClientError


def _recruiter_jwt(recruiter_id: uuid.UUID) -> str:
    token, _ = create_access_token(recruiter_id, "recruiter@example.com", main.settings.jwt_secret, 45)
    return token


def _fake_recruiter(recruiter_id: uuid.UUID):
    return SimpleNamespace(id=recruiter_id, email="recruiter@example.com", name="Test Recruiter")


@pytest.fixture
def client(monkeypatch):
    recruiter_id = uuid.uuid4()
    monkeypatch.setattr(
        main, "get_recruiter_by_id",
        lambda db, rid: _fake_recruiter(recruiter_id) if rid == recruiter_id else None,
    )
    # require_recruiter itself still depends on get_db (the auth dependency, common to
    # every route); the route underneath it takes no `db` parameter of its own -- see
    # test_route_has_no_db_dependency below.
    main.app.dependency_overrides[main.get_db] = lambda: SimpleNamespace()
    try:
        yield TestClient(main.app), recruiter_id
    finally:
        main.app.dependency_overrides.clear()


def _auth_header(recruiter_id: uuid.UUID) -> dict:
    return {"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"}


# ---------------------------------------------------------------------------
# Auth boundary
# ---------------------------------------------------------------------------

def test_candidates_without_jwt_returns_401(client, monkeypatch):
    calls = {"n": 0}

    async def should_not_be_called(*a, **k):
        calls["n"] += 1

    monkeypatch.setattr(resume_intelligence_client, "list_candidates", should_not_be_called)

    test_client, _ = client
    resp = test_client.get("/v1/candidates")

    assert resp.status_code == 401
    assert calls["n"] == 0


def test_candidates_with_service_api_key_instead_of_jwt_returns_401(client):
    # A service API key must never substitute for a recruiter JWT on this endpoint.
    test_client, _ = client
    resp = test_client.get("/v1/candidates", headers={"x-api-key": main.settings.resume_intelligence_api_key})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Response shape / content
# ---------------------------------------------------------------------------

def test_empty_list_returns_empty_array(client, monkeypatch):
    async def fake_list_candidates(base_url, api_key):
        return []

    monkeypatch.setattr(resume_intelligence_client, "list_candidates", fake_list_candidates)

    test_client, recruiter_id = client
    resp = test_client.get("/v1/candidates", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    assert resp.json() == []


def test_non_empty_list_passes_through_resume_intelligence_order(client, monkeypatch):
    newer_id = str(uuid.uuid4())
    older_id = str(uuid.uuid4())

    async def fake_list_candidates(base_url, api_key):
        # Resume Intelligence's own query already returns newest-first; the route
        # must pass that ordering through unchanged, not re-sort or reverse it.
        return [
            {
                "candidate_id": newer_id,
                "name": "Newer Candidate",
                "email": "newer@example.com",
                "created_at": "2026-01-02T00:00:00+00:00",
            },
            {
                "candidate_id": older_id,
                "name": "Older Candidate",
                "email": None,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        ]

    monkeypatch.setattr(resume_intelligence_client, "list_candidates", fake_list_candidates)

    test_client, recruiter_id = client
    resp = test_client.get("/v1/candidates", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    body = resp.json()
    assert [c["candidate_id"] for c in body] == [newer_id, older_id]
    assert body[0]["name"] == "Newer Candidate"
    assert body[0]["email"] == "newer@example.com"
    assert body[1]["email"] is None


def test_response_excludes_phone_and_other_pii(client, monkeypatch):
    candidate_id = str(uuid.uuid4())

    async def fake_list_candidates(base_url, api_key):
        # Resume Intelligence's own response model already excludes phone/raw_text/
        # candidate_json; this pins that RWS's own CandidateSummaryResponse would
        # exclude them too even if a future Resume Intelligence change leaked one in.
        return [
            {
                "candidate_id": candidate_id,
                "name": "Jean Dupont",
                "email": "jean.dupont@example.com",
                "created_at": "2026-01-01T00:00:00+00:00",
                "phone": "+33612345678",
            }
        ]

    monkeypatch.setattr(resume_intelligence_client, "list_candidates", fake_list_candidates)

    test_client, recruiter_id = client
    resp = test_client.get("/v1/candidates", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    row = resp.json()[0]
    assert "phone" not in row
    assert set(row.keys()) == {"candidate_id", "name", "email", "created_at"}


# ---------------------------------------------------------------------------
# Error semantics
# ---------------------------------------------------------------------------

def test_resume_intelligence_failure_returns_502_without_leaking_exception_text(client, monkeypatch, caplog):
    async def raising_list_candidates(base_url, api_key):
        raise ResumeIntelligenceClientError("connection refused: internal detail jean.dupont@example.com")

    monkeypatch.setattr(resume_intelligence_client, "list_candidates", raising_list_candidates)

    test_client, recruiter_id = client
    with caplog.at_level("DEBUG"):
        resp = test_client.get("/v1/candidates", headers=_auth_header(recruiter_id))

    assert resp.status_code == 502
    assert "connection refused" not in resp.text
    assert "jean.dupont@example.com" not in resp.text
    assert "connection refused" not in caplog.text
    assert "jean.dupont@example.com" not in caplog.text


# ---------------------------------------------------------------------------
# No RWS persistence
# ---------------------------------------------------------------------------

def test_route_has_no_db_dependency():
    # Literal, direct proof RWS never touches its own database for this endpoint,
    # same as get_candidate_identity's own test.
    import inspect

    sig = inspect.signature(main.get_candidates)
    assert "db" not in sig.parameters
