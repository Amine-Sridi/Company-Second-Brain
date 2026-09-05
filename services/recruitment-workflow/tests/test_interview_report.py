"""
tests/test_interview_report.py
------------------------------
Auth-boundary, response-shape and error-semantics tests for
GET /v1/interviews/{application_id}/report and
GET /v1/interviews/{application_id}/recording on RWS.

Architecture under test: both routes transiently proxy VoiceHR's own RWS-facing
(service-key-gated) endpoints and persist nothing in RWS -- the same shape as
GET /v1/candidates/{id}/identity (see test_candidate_identity.py). No real Postgres
or VoiceHR is required: get_application and voicehr_client.get_interview_report/
get_interview_recording are all mocked.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
import voicehr_client
from auth import create_access_token
from voicehr_client import VoiceHRClientError


def _recruiter_jwt(recruiter_id: uuid.UUID) -> str:
    token, _ = create_access_token(recruiter_id, "recruiter@example.com", main.settings.jwt_secret, 45)
    return token


def _fake_recruiter(recruiter_id: uuid.UUID):
    return SimpleNamespace(id=recruiter_id, email="recruiter@example.com", name="Test Recruiter")


def _fake_application(application_id: uuid.UUID):
    return SimpleNamespace(id=application_id, candidate_id=uuid.uuid4(), status="interviewed")


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


def _auth_header(recruiter_id: uuid.UUID) -> dict:
    return {"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"}


# ---------------------------------------------------------------------------
# Report -- auth boundary
# ---------------------------------------------------------------------------

def test_report_without_jwt_returns_401(client, monkeypatch):
    calls = {"n": 0}

    async def should_not_be_called(*a, **k):
        calls["n"] += 1

    monkeypatch.setattr(voicehr_client, "get_interview_report", should_not_be_called)

    test_client, _ = client
    resp = test_client.get(f"/v1/interviews/{uuid.uuid4()}/report")

    assert resp.status_code == 401
    assert calls["n"] == 0


def test_report_with_service_api_key_instead_of_jwt_returns_401(client):
    test_client, _ = client
    resp = test_client.get(
        f"/v1/interviews/{uuid.uuid4()}/report", headers={"x-api-key": main.settings.voicehr_api_key}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Report -- success / shape
# ---------------------------------------------------------------------------

def test_report_success_rewrites_recording_to_same_origin_url(client, monkeypatch):
    application_id = uuid.uuid4()
    monkeypatch.setattr(main, "get_application", lambda db, aid: _fake_application(aid))

    async def fake_get_interview_report(base_url, api_key, external_reference_id):
        assert external_reference_id == str(application_id)
        return {
            "candidate_name": "Jean Dupont",
            "candidate_email": "jean.dupont@example.com",
            "overall_score": 88,
            "cefr_level": "C1",
            "dimensions": {"fluency": 90},
            "transcript": [{"role": "user", "text": "Bonjour"}],
            "recording": {"available": True},
        }

    monkeypatch.setattr(voicehr_client, "get_interview_report", fake_get_interview_report)

    test_client, recruiter_id = client
    resp = test_client.get(f"/v1/interviews/{application_id}/report", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate_name"] == "Jean Dupont"
    assert body["overall_score"] == 88
    assert body["cefr_level"] == "C1"
    assert body["dimensions"] == {"fluency": 90}
    assert body["transcript"] == [{"role": "user", "text": "Bonjour"}]
    # The whole point of this rewrite: a same-origin, RWS-relative URL -- never a
    # VoiceHR host, port, or path.
    assert body["recording"] == {"available": True, "url": f"/v1/interviews/{application_id}/recording"}


def test_report_recording_unavailable_returns_null_url(client, monkeypatch):
    application_id = uuid.uuid4()
    monkeypatch.setattr(main, "get_application", lambda db, aid: _fake_application(aid))

    async def fake_get_interview_report(base_url, api_key, external_reference_id):
        return {"candidate_name": "Jean Dupont", "recording": {"available": False}}

    monkeypatch.setattr(voicehr_client, "get_interview_report", fake_get_interview_report)

    test_client, recruiter_id = client
    resp = test_client.get(f"/v1/interviews/{application_id}/report", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    assert resp.json()["recording"] == {"available": False, "url": None}


# ---------------------------------------------------------------------------
# Report -- error semantics
# ---------------------------------------------------------------------------

def test_report_unknown_application_returns_404(client, monkeypatch):
    monkeypatch.setattr(main, "get_application", lambda db, aid: None)

    test_client, recruiter_id = client
    resp = test_client.get(f"/v1/interviews/{uuid.uuid4()}/report", headers=_auth_header(recruiter_id))

    assert resp.status_code == 404


def test_report_no_report_yet_returns_404(client, monkeypatch):
    application_id = uuid.uuid4()
    monkeypatch.setattr(main, "get_application", lambda db, aid: _fake_application(aid))

    async def fake_get_interview_report(base_url, api_key, external_reference_id):
        return None

    monkeypatch.setattr(voicehr_client, "get_interview_report", fake_get_interview_report)

    test_client, recruiter_id = client
    resp = test_client.get(f"/v1/interviews/{application_id}/report", headers=_auth_header(recruiter_id))

    assert resp.status_code == 404


def test_voicehr_failure_returns_502_without_leaking_exception_text(client, monkeypatch, caplog):
    application_id = uuid.uuid4()
    monkeypatch.setattr(main, "get_application", lambda db, aid: _fake_application(aid))

    async def raising_get_interview_report(base_url, api_key, external_reference_id):
        raise VoiceHRClientError("connection refused: internal detail jean.dupont@example.com")

    monkeypatch.setattr(voicehr_client, "get_interview_report", raising_get_interview_report)

    test_client, recruiter_id = client
    with caplog.at_level("DEBUG"):
        resp = test_client.get(f"/v1/interviews/{application_id}/report", headers=_auth_header(recruiter_id))

    assert resp.status_code == 502
    assert "connection refused" not in resp.text
    assert "jean.dupont@example.com" not in resp.text
    assert "connection refused" not in caplog.text
    assert "jean.dupont@example.com" not in caplog.text


# ---------------------------------------------------------------------------
# Recording -- auth boundary, success, error semantics
# ---------------------------------------------------------------------------

def test_recording_without_jwt_returns_401(client):
    test_client, _ = client
    resp = test_client.get(f"/v1/interviews/{uuid.uuid4()}/recording")
    assert resp.status_code == 401


def test_recording_success_streams_bytes_with_content_type(client, monkeypatch):
    application_id = uuid.uuid4()
    monkeypatch.setattr(main, "get_application", lambda db, aid: _fake_application(aid))

    class _FakeResponse:
        content = b"fake audio bytes"
        headers = {"content-type": "audio/webm"}

    async def fake_get_interview_recording(base_url, api_key, external_reference_id):
        assert external_reference_id == str(application_id)
        return _FakeResponse()

    monkeypatch.setattr(voicehr_client, "get_interview_recording", fake_get_interview_recording)

    test_client, recruiter_id = client
    resp = test_client.get(f"/v1/interviews/{application_id}/recording", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/webm"
    assert resp.content == b"fake audio bytes"


def test_recording_unavailable_returns_404(client, monkeypatch):
    application_id = uuid.uuid4()
    monkeypatch.setattr(main, "get_application", lambda db, aid: _fake_application(aid))

    async def fake_get_interview_recording(base_url, api_key, external_reference_id):
        return None

    monkeypatch.setattr(voicehr_client, "get_interview_recording", fake_get_interview_recording)

    test_client, recruiter_id = client
    resp = test_client.get(f"/v1/interviews/{application_id}/recording", headers=_auth_header(recruiter_id))

    assert resp.status_code == 404


def test_recording_unknown_application_returns_404(client, monkeypatch):
    monkeypatch.setattr(main, "get_application", lambda db, aid: None)

    test_client, recruiter_id = client
    resp = test_client.get(f"/v1/interviews/{uuid.uuid4()}/recording", headers=_auth_header(recruiter_id))

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# No RWS persistence
# ---------------------------------------------------------------------------

def test_report_route_has_no_db_write_path():
    # Same structural proof pattern as get_candidate_identity/get_candidates: both
    # routes take a `db` param only to resolve the application (a read), and
    # neither function body contains a commit/add call -- verified here by
    # asserting on the function source rather than a mocked db, so a future
    # accidental db.add()/db.commit() would fail this test even if it happened to
    # not raise.
    import inspect

    report_source = inspect.getsource(main.get_interview_report)
    recording_source = inspect.getsource(main.get_interview_recording)
    for source in (report_source, recording_source):
        assert "db.add(" not in source
        assert "db.commit(" not in source
