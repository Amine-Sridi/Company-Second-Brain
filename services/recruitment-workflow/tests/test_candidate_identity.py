"""
tests/test_candidate_identity.py
------------------------------
Auth-boundary, response-shape and error-semantics tests for
GET /v1/candidates/{candidate_id}/identity on RWS.

Architecture under test: this route transiently proxies Resume Intelligence's contact
lookup for the recruiter UI's benefit and persists nothing in RWS. No real Postgres or
Resume Intelligence is required -- get_recruiter_by_id and
resume_intelligence_client.get_contact are both mocked.
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
    # require_recruiter itself still depends on get_db to look up the recruiter (it's
    # the auth dependency, common to every route); that's overridden here same as
    # every other test file. The identity *route* underneath it takes no `db`
    # parameter of its own -- see test_route_has_no_db_dependency below, which checks
    # that directly on the function signature.
    main.app.dependency_overrides[main.get_db] = lambda: SimpleNamespace()
    try:
        yield TestClient(main.app), recruiter_id
    finally:
        main.app.dependency_overrides.clear()


def _get_identity(test_client: TestClient, candidate_id: uuid.UUID, *, headers: dict | None = None):
    return test_client.get(f"/v1/candidates/{candidate_id}/identity", headers=headers or {})


# ---------------------------------------------------------------------------
# Auth boundary
# ---------------------------------------------------------------------------

def test_identity_without_jwt_returns_401(client, monkeypatch):
    calls = {"n": 0}

    async def should_not_be_called(*a, **k):
        calls["n"] += 1

    monkeypatch.setattr(resume_intelligence_client, "get_contact", should_not_be_called)

    test_client, _ = client
    resp = _get_identity(test_client, uuid.uuid4())

    assert resp.status_code == 401
    assert calls["n"] == 0


def test_identity_with_service_api_key_instead_of_jwt_returns_401(client):
    test_client, _ = client
    resp = _get_identity(
        test_client, uuid.uuid4(), headers={"x-api-key": main.settings.resume_intelligence_api_key}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Successful lookup / response shape
# ---------------------------------------------------------------------------

def test_successful_lookup_returns_candidate_id_name_email(client, monkeypatch):
    candidate_id = uuid.uuid4()

    async def fake_get_contact(base_url, api_key, cid):
        assert cid == candidate_id
        return {
            "candidate_id": str(candidate_id),
            "name": "Jean Dupont",
            "email": "jean.dupont@example.com",
            "phone": "+33612345678",
        }

    monkeypatch.setattr(resume_intelligence_client, "get_contact", fake_get_contact)

    test_client, recruiter_id = client
    resp = _get_identity(
        test_client, candidate_id, headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate_id"] == str(candidate_id)
    assert body["name"] == "Jean Dupont"
    assert body["email"] == "jean.dupont@example.com"


def test_response_excludes_phone_and_other_pii(client, monkeypatch):
    candidate_id = uuid.uuid4()

    async def fake_get_contact(base_url, api_key, cid):
        return {
            "candidate_id": str(candidate_id),
            "name": "Jean Dupont",
            "email": "jean.dupont@example.com",
            "phone": "+33612345678",
        }

    monkeypatch.setattr(resume_intelligence_client, "get_contact", fake_get_contact)

    test_client, recruiter_id = client
    resp = _get_identity(
        test_client, candidate_id, headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "phone" not in body
    assert set(body.keys()) == {"candidate_id", "name", "email"}


# ---------------------------------------------------------------------------
# Error semantics
# ---------------------------------------------------------------------------

def test_unknown_candidate_returns_404(client, monkeypatch):
    async def fake_get_contact(base_url, api_key, cid):
        return None

    monkeypatch.setattr(resume_intelligence_client, "get_contact", fake_get_contact)

    test_client, recruiter_id = client
    resp = _get_identity(
        test_client, uuid.uuid4(), headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"}
    )

    assert resp.status_code == 404


def test_resume_intelligence_failure_returns_502_without_leaking_exception_text(client, monkeypatch, caplog):
    async def raising_get_contact(base_url, api_key, cid):
        raise ResumeIntelligenceClientError("connection refused: internal detail jean.dupont@example.com")

    monkeypatch.setattr(resume_intelligence_client, "get_contact", raising_get_contact)

    test_client, recruiter_id = client
    with caplog.at_level("DEBUG"):
        resp = _get_identity(
            test_client, uuid.uuid4(), headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"}
        )

    assert resp.status_code == 502
    assert "connection refused" not in resp.text
    assert "jean.dupont@example.com" not in resp.text
    assert "connection refused" not in caplog.text
    assert "jean.dupont@example.com" not in caplog.text


# ---------------------------------------------------------------------------
# No RWS persistence
# ---------------------------------------------------------------------------

def test_route_has_no_db_dependency():
    # Literal, direct proof RWS never touches its own database for this endpoint: the
    # route function declares no `db: Session = Depends(get_db)` parameter at all --
    # unlike every other RWS route, which does. There is nothing to write to, so
    # there is nothing for a mocked/asserted db.add()/db.commit() to catch.
    import inspect

    sig = inspect.signature(main.get_candidate_identity)
    assert "db" not in sig.parameters
