"""
tests/test_resend_invite.py
----------------------------
Unit-level coverage (no real Postgres -- same monkeypatch pattern as
test_language_requirement_workflow.py) for POST /v1/applications/{id}/resend-invite:
the explicit-resend counterpart to POST /v1/applications/{id}/invite that reuses
VoiceHR's existing interview/token instead of creating a new one. The fuller,
real-Postgres scenarios (resend after a genuine select+invite, VoiceHR call
recording) live in tests/integration/test_recruitment_workflow.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from auth import create_access_token
from voicehr_client import VoiceHRClientError


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
    # Each test gets its own application_id (uuid4), so the module-level resend
    # cooldown dict never leaks state between tests without needing an explicit reset.
    main._resend_cooldowns.clear()
    try:
        yield TestClient(main.app), recruiter_id
    finally:
        main.app.dependency_overrides.clear()


def _auth_header(recruiter_id: uuid.UUID) -> dict:
    return {"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"}


def _invited_application(application_id: uuid.UUID):
    return SimpleNamespace(
        id=application_id, status="invited", invite_attempt_number=1, job_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(), match_result_id=uuid.uuid4(),
        selected_at=datetime.now(timezone.utc), invited_at=datetime.now(timezone.utc),
        interview_status=None, interview_assessment=None, interview_completed_at=None,
        decided_at=None, required_language=None, required_cefr=None,
    )


def test_resend_invite_success_calls_voicehr_and_stays_invited(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    application = _invited_application(application_id)
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)

    captured = {}

    async def fake_resend_invite(base_url, api_key, external_reference_id):
        captured["external_reference_id"] = external_reference_id

    monkeypatch.setattr(main.voicehr_client, "resend_invite", fake_resend_invite)
    monkeypatch.setattr(main, "list_interview_rounds_for_application", lambda db, aid: [])

    resp = test_client.post(
        f"/v1/applications/{application_id}/resend-invite", headers=_auth_header(recruiter_id)
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "invited"
    assert body["invite_attempt_number"] == 1  # unchanged -- no new attempt/interview
    assert captured["external_reference_id"] == str(application_id)


def test_resend_invite_on_selected_application_returns_409(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    application = SimpleNamespace(id=application_id, status="selected", invite_attempt_number=0)
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)

    async def should_not_be_called(*a, **k):
        raise AssertionError("resend must not call VoiceHR for a non-invited application")

    monkeypatch.setattr(main.voicehr_client, "resend_invite", should_not_be_called)

    resp = test_client.post(
        f"/v1/applications/{application_id}/resend-invite", headers=_auth_header(recruiter_id)
    )
    assert resp.status_code == 409


def test_resend_invite_unknown_application_returns_404(client, monkeypatch):
    test_client, recruiter_id = client
    monkeypatch.setattr(main, "get_application", lambda db, aid: None)

    resp = test_client.post(
        f"/v1/applications/{uuid.uuid4()}/resend-invite", headers=_auth_header(recruiter_id)
    )
    assert resp.status_code == 404


def test_resend_invite_failure_leaves_application_invited(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    application = _invited_application(application_id)
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)

    async def failing_resend_invite(*a, **k):
        raise VoiceHRClientError("simulated delivery failure")

    monkeypatch.setattr(main.voicehr_client, "resend_invite", failing_resend_invite)

    resp = test_client.post(
        f"/v1/applications/{application_id}/resend-invite", headers=_auth_header(recruiter_id)
    )
    assert resp.status_code == 502
    # The in-memory application object itself was never mutated -- resend_invite
    # never calls record_invite_attempt or any other status-changing db.py function.
    assert application.status == "invited"


def test_resend_invite_requires_jwt_not_service_key(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    monkeypatch.setattr(main, "get_application", lambda db, aid: _invited_application(application_id))

    resp = test_client.post(
        f"/v1/applications/{application_id}/resend-invite",
        headers={"x-api-key": "some-service-key"},
    )
    assert resp.status_code == 401


def test_rapid_repeated_resend_is_rate_limited(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    application = _invited_application(application_id)
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)

    call_count = {"n": 0}

    async def fake_resend_invite(base_url, api_key, external_reference_id):
        call_count["n"] += 1

    monkeypatch.setattr(main.voicehr_client, "resend_invite", fake_resend_invite)
    monkeypatch.setattr(main, "list_interview_rounds_for_application", lambda db, aid: [])

    headers = _auth_header(recruiter_id)
    first = test_client.post(f"/v1/applications/{application_id}/resend-invite", headers=headers)
    second = test_client.post(f"/v1/applications/{application_id}/resend-invite", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429
    assert call_count["n"] == 1  # the second click never reached VoiceHR at all
