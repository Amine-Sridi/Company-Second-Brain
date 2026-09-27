"""
tests/test_candidate_knowledge.py
------------------------------
Auth-boundary, response-shape and error-semantics tests for
GET /v1/candidates/{candidate_id}/knowledge on RWS.

Architecture under test: this route transiently proxies Second Brain's own
GET /v1/knowledge/candidates/{candidate_id} (via clients.get_candidate) and
persists nothing in RWS. Note that POST /v1/jobs no longer uses this function: scoring
now fetches every retrieved candidate in one clients.get_candidates_batch call (audit
M2), so this route is the remaining single-candidate caller -- the same shape as GET /v1/candidates/{id}/identity (see
test_candidate_identity.py). No real Postgres or Second Brain is required: clients.
get_candidate is mocked.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import clients
import main
from auth import create_access_token
from clients import SecondBrainClientError


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
    # require_recruiter itself still depends on get_db (the auth dependency, common
    # to every route); the route underneath it takes no `db` parameter of its own --
    # see test_route_has_no_db_dependency below.
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

def test_knowledge_without_jwt_returns_401(client, monkeypatch):
    calls = {"n": 0}

    async def should_not_be_called(*a, **k):
        calls["n"] += 1

    monkeypatch.setattr(clients, "get_candidate", should_not_be_called)

    test_client, _ = client
    resp = test_client.get(f"/v1/candidates/{uuid.uuid4()}/knowledge")

    assert resp.status_code == 401
    assert calls["n"] == 0


def test_knowledge_with_service_api_key_instead_of_jwt_returns_401(client):
    test_client, _ = client
    resp = test_client.get(
        f"/v1/candidates/{uuid.uuid4()}/knowledge", headers={"x-api-key": main.settings.second_brain_api_key}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Success / response shape
# ---------------------------------------------------------------------------

def test_successful_lookup_returns_skills_titles_and_experience(client, monkeypatch):
    candidate_id = uuid.uuid4()

    async def fake_get_candidate(base_url, api_key, cid):
        assert cid == candidate_id
        return {
            "candidate_id": str(candidate_id),
            "skills": ["Python", "FastAPI", "PostgreSQL"],
            "job_titles": ["Backend Engineer", "Senior Backend Engineer"],
            "years_of_experience": 5.5,
            "raw_text": "this must never appear in the response",
            "source_version": "1",
        }

    monkeypatch.setattr(clients, "get_candidate", fake_get_candidate)

    test_client, recruiter_id = client
    resp = test_client.get(f"/v1/candidates/{candidate_id}/knowledge", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate_id"] == str(candidate_id)
    assert body["skills"] == ["Python", "FastAPI", "PostgreSQL"]
    assert body["job_titles"] == ["Backend Engineer", "Senior Backend Engineer"]
    assert body["years_of_experience"] == 5.5


def test_response_excludes_raw_text_and_source_version(client, monkeypatch):
    candidate_id = uuid.uuid4()

    async def fake_get_candidate(base_url, api_key, cid):
        return {
            "candidate_id": str(candidate_id),
            "skills": [],
            "job_titles": [],
            "years_of_experience": None,
            "raw_text": "sensitive-ish extracted text, never a contract field here",
            "source_version": "1",
        }

    monkeypatch.setattr(clients, "get_candidate", fake_get_candidate)

    test_client, recruiter_id = client
    resp = test_client.get(f"/v1/candidates/{candidate_id}/knowledge", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    body = resp.json()
    assert "raw_text" not in body
    assert "source_version" not in body
    assert "embedding" not in body
    assert set(body.keys()) == {"candidate_id", "skills", "job_titles", "years_of_experience"}


def test_missing_years_of_experience_is_null_not_an_error(client, monkeypatch):
    candidate_id = uuid.uuid4()

    async def fake_get_candidate(base_url, api_key, cid):
        return {
            "candidate_id": str(candidate_id),
            "skills": ["Go"],
            "job_titles": [],
            "years_of_experience": None,
            "raw_text": "x",
            "source_version": "1",
        }

    monkeypatch.setattr(clients, "get_candidate", fake_get_candidate)

    test_client, recruiter_id = client
    resp = test_client.get(f"/v1/candidates/{candidate_id}/knowledge", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    assert resp.json()["years_of_experience"] is None


# ---------------------------------------------------------------------------
# Error semantics
# ---------------------------------------------------------------------------

def test_unknown_candidate_returns_404(client, monkeypatch):
    async def fake_get_candidate(base_url, api_key, cid):
        return None

    monkeypatch.setattr(clients, "get_candidate", fake_get_candidate)

    test_client, recruiter_id = client
    resp = test_client.get(f"/v1/candidates/{uuid.uuid4()}/knowledge", headers=_auth_header(recruiter_id))

    assert resp.status_code == 404


def test_second_brain_failure_returns_502_without_leaking_exception_text(client, monkeypatch, caplog):
    async def raising_get_candidate(base_url, api_key, cid):
        raise SecondBrainClientError("connection refused: internal detail about the index")

    monkeypatch.setattr(clients, "get_candidate", raising_get_candidate)

    test_client, recruiter_id = client
    with caplog.at_level("DEBUG"):
        resp = test_client.get(f"/v1/candidates/{uuid.uuid4()}/knowledge", headers=_auth_header(recruiter_id))

    assert resp.status_code == 502
    assert "connection refused" not in resp.text
    assert "connection refused" not in caplog.text


# ---------------------------------------------------------------------------
# No RWS persistence
# ---------------------------------------------------------------------------

def test_route_has_no_db_dependency():
    # Literal, direct proof RWS never touches its own database for this endpoint,
    # same as get_candidate_identity/get_candidates' own tests.
    import inspect

    sig = inspect.signature(main.get_candidate_knowledge)
    assert "db" not in sig.parameters
