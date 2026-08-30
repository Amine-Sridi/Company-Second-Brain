"""
tests/test_resume_upload.py
------------------------------
Auth-boundary and forwarding tests for POST /v1/resumes on RWS.

Architecture under test: recruiter/admin uploads go
    recruiter client --JWT--> RWS --service key--> Resume Intelligence
Resume Intelligence never sees or validates the recruiter's JWT; RWS never exposes its
Resume Intelligence service key to the client. No real Postgres or Resume Intelligence
is required -- get_recruiter_by_id and resume_intelligence_client.upload_resume are
both mocked; DATABASE_URL is a syntactically valid but unreachable URL (see conftest.py).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
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
    main.app.dependency_overrides[main.get_db] = lambda: SimpleNamespace()
    try:
        yield TestClient(main.app), recruiter_id
    finally:
        main.app.dependency_overrides.clear()


def _upload(test_client: TestClient, *, headers: dict | None = None):
    return test_client.post(
        "/v1/resumes",
        files={"file": ("resume.pdf", b"resume bytes", "application/pdf")},
        headers=headers or {},
    )


# ---------------------------------------------------------------------------
# Auth boundary
# ---------------------------------------------------------------------------

def test_upload_without_jwt_returns_401(client, monkeypatch):
    calls = {"n": 0}

    async def should_not_be_called(*a, **k):
        calls["n"] += 1

    monkeypatch.setattr(resume_intelligence_client, "upload_resume", should_not_be_called)

    test_client, _ = client
    resp = _upload(test_client)

    assert resp.status_code == 401
    assert calls["n"] == 0


def test_upload_with_service_api_key_instead_of_jwt_returns_401(client):
    # A service API key must never satisfy the recruiter-JWT check on this endpoint --
    # require_recruiter reads Authorization: Bearer, not X-Api-Key, and the two secrets
    # (JWT_SECRET vs SERVICE_API_KEY) are unrelated.
    test_client, _ = client
    resp = _upload(test_client, headers={"x-api-key": main.settings.resume_intelligence_api_key})
    assert resp.status_code == 401


def test_upload_with_garbage_bearer_token_returns_401(client):
    test_client, _ = client
    resp = _upload(test_client, headers={"Authorization": "Bearer not-a-real-jwt"})
    assert resp.status_code == 401


def test_upload_with_valid_jwt_is_accepted(client, monkeypatch):
    async def fake_upload_resume(base_url, api_key, filename, content, content_type):
        return httpx.Response(201, json={"candidate_id": str(uuid.uuid4()), "is_new": True})

    monkeypatch.setattr(resume_intelligence_client, "upload_resume", fake_upload_resume)

    test_client, recruiter_id = client
    resp = _upload(test_client, headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})

    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Forwarding: correct service key used, never the JWT; correlation ID untouched by
# this endpoint (call_service's own responsibility, not re-tested here); multipart
# content/filename/content-type all forwarded correctly.
# ---------------------------------------------------------------------------

def test_forwards_to_resume_intelligence_with_the_service_key_not_the_jwt(client, monkeypatch):
    seen = {}

    async def fake_upload_resume(base_url, api_key, filename, content, content_type):
        seen["base_url"] = base_url
        seen["api_key"] = api_key
        seen["filename"] = filename
        seen["content"] = content
        seen["content_type"] = content_type
        return httpx.Response(201, json={"candidate_id": str(uuid.uuid4()), "is_new": True})

    monkeypatch.setattr(resume_intelligence_client, "upload_resume", fake_upload_resume)

    test_client, recruiter_id = client
    jwt = _recruiter_jwt(recruiter_id)
    resp = test_client.post(
        "/v1/resumes",
        files={"file": ("CV_Jean_Dupont.pdf", b"%PDF-1.4 fake resume bytes", "application/pdf")},
        headers={"Authorization": f"Bearer {jwt}"},
    )

    assert resp.status_code == 201
    assert seen["base_url"] == main.settings.resume_intelligence_url
    assert seen["api_key"] == main.settings.resume_intelligence_api_key
    assert seen["api_key"] != jwt
    assert seen["filename"] == "CV_Jean_Dupont.pdf"
    assert seen["content"] == b"%PDF-1.4 fake resume bytes"
    assert seen["content_type"] == "application/pdf"


# ---------------------------------------------------------------------------
# Response-semantics passthrough
# ---------------------------------------------------------------------------

def test_success_response_preserves_candidate_id_and_is_new(client, monkeypatch):
    candidate_id = str(uuid.uuid4())

    async def fake_upload_resume(base_url, api_key, filename, content, content_type):
        return httpx.Response(201, json={"candidate_id": candidate_id, "is_new": False})

    monkeypatch.setattr(resume_intelligence_client, "upload_resume", fake_upload_resume)

    test_client, recruiter_id = client
    resp = _upload(test_client, headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})

    assert resp.status_code == 201
    assert resp.json() == {"candidate_id": candidate_id, "is_new": False}


def test_resume_intelligence_422_passes_through_as_422(client, monkeypatch):
    async def fake_upload_resume(base_url, api_key, filename, content, content_type):
        return httpx.Response(422, json={"detail": "Unsupported file type '.exe'."})

    monkeypatch.setattr(resume_intelligence_client, "upload_resume", fake_upload_resume)

    test_client, recruiter_id = client
    resp = _upload(test_client, headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})

    assert resp.status_code == 422
    assert resp.json()["detail"] == "Unsupported file type '.exe'."


def test_resume_intelligence_502_becomes_rws_502(client, monkeypatch):
    async def fake_upload_resume(base_url, api_key, filename, content, content_type):
        return httpx.Response(502, json={"detail": "Structured extraction failed"})

    monkeypatch.setattr(resume_intelligence_client, "upload_resume", fake_upload_resume)

    test_client, recruiter_id = client
    resp = _upload(test_client, headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})

    assert resp.status_code == 502


def test_resume_intelligence_5xx_becomes_rws_502_generic_detail(client, monkeypatch):
    async def fake_upload_resume(base_url, api_key, filename, content, content_type):
        return httpx.Response(500, json={"detail": "Failed to process resume"})

    monkeypatch.setattr(resume_intelligence_client, "upload_resume", fake_upload_resume)

    test_client, recruiter_id = client
    resp = _upload(test_client, headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})

    assert resp.status_code == 502


def test_resume_intelligence_unreachable_becomes_rws_502(client, monkeypatch):
    async def raising_upload_resume(base_url, api_key, filename, content, content_type):
        raise ResumeIntelligenceClientError("connection refused")

    monkeypatch.setattr(resume_intelligence_client, "upload_resume", raising_upload_resume)

    test_client, recruiter_id = client
    resp = _upload(test_client, headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})

    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# No PII / JWT / API key leakage in logs
# ---------------------------------------------------------------------------

def test_jwt_and_service_key_never_appear_in_logs(client, monkeypatch, caplog):
    async def fake_upload_resume(base_url, api_key, filename, content, content_type):
        return httpx.Response(201, json={"candidate_id": str(uuid.uuid4()), "is_new": True})

    monkeypatch.setattr(resume_intelligence_client, "upload_resume", fake_upload_resume)

    test_client, recruiter_id = client
    jwt = _recruiter_jwt(recruiter_id)
    with caplog.at_level("DEBUG"):
        resp = test_client.post(
            "/v1/resumes",
            files={"file": ("resume.pdf", b"resume bytes for jean.dupont@example.com", "application/pdf")},
            headers={"Authorization": f"Bearer {jwt}"},
        )

    assert resp.status_code == 201
    assert jwt not in caplog.text
    assert main.settings.resume_intelligence_api_key not in caplog.text
    assert "jean.dupont@example.com" not in caplog.text


def test_resume_intelligence_failure_detail_does_not_leak_service_key_or_jwt(client, monkeypatch, caplog):
    async def raising_upload_resume(base_url, api_key, filename, content, content_type):
        raise ResumeIntelligenceClientError("connection refused")

    monkeypatch.setattr(resume_intelligence_client, "upload_resume", raising_upload_resume)

    test_client, recruiter_id = client
    jwt = _recruiter_jwt(recruiter_id)
    with caplog.at_level("DEBUG"):
        resp = _upload(test_client, headers={"Authorization": f"Bearer {jwt}"})

    assert resp.status_code == 502
    assert jwt not in resp.text
    assert jwt not in caplog.text
    assert main.settings.resume_intelligence_api_key not in resp.text
    assert main.settings.resume_intelligence_api_key not in caplog.text


# ---------------------------------------------------------------------------
# resume_intelligence_client.upload_resume itself -- multipart shape and the
# extended timeout (resume ingestion runs real extraction + up to 3 bounded Gemini
# attempts before Second Brain is even called, routinely longer than
# shared.http_client.call_service's plain-JSON-call default).
# ---------------------------------------------------------------------------

def test_upload_resume_client_uses_multipart_and_an_extended_timeout(monkeypatch):
    import asyncio

    seen = {}

    async def fake_call_service(method, url, *, api_key, files=None, timeout=None, **kwargs):
        seen.update(method=method, url=url, api_key=api_key, files=files, timeout=timeout)
        return httpx.Response(201, json={"candidate_id": str(uuid.uuid4()), "is_new": True})

    monkeypatch.setattr(resume_intelligence_client, "call_service", fake_call_service)

    asyncio.run(resume_intelligence_client.upload_resume(
        "http://resume-intelligence:8000", "test-service-key", "cv.pdf", b"content", "application/pdf",
    ))

    assert seen["method"] == "POST"
    assert seen["url"] == "http://resume-intelligence:8000/v1/resumes"
    assert seen["api_key"] == "test-service-key"
    assert seen["files"] == {"file": ("cv.pdf", b"content", "application/pdf")}
    assert seen["timeout"] == resume_intelligence_client._UPLOAD_TIMEOUT_SECONDS
    assert seen["timeout"] > 10.0
