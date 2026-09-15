"""
tests/test_accept_language_gate.py
------------------------------
Route-level enforcement tests for POST /v1/applications/{id}/accept's VoiceHR
language eligibility gate (main.py: accept_application, language_gate.py).

No real Postgres: main.get_application/transition_application/get_match_result/
get_job are monkeypatched directly, the same pattern test_jobs_list.py already
established for this test suite. Each fake Application is a plain SimpleNamespace
carrying exactly the fields _language_gate_info/_application_response read.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from auth import create_access_token


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


def _fake_application(
    status="interviewed",
    required_language=None,
    required_cefr=None,
    interview_assessment=None,
):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        match_result_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        status=status,
        selected_at=now,
        invite_attempt_number=1,
        invited_at=now,
        interview_status="completed" if interview_assessment else None,
        interview_assessment=interview_assessment,
        interview_completed_at=now if interview_assessment else None,
        decided_at=None,
        required_language=required_language,
        required_cefr=required_cefr,
    )


def _auth_header(recruiter_id: uuid.UUID) -> dict:
    return {"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"}


def _accept(test_client, recruiter_id, application_id):
    return test_client.post(
        f"/v1/applications/{application_id}/accept", headers=_auth_header(recruiter_id)
    )


def _stub_transition_and_feedback(monkeypatch, application):
    """Only reached if the gate does NOT block -- proves the real transition path
    still runs for an allowed accept, not just that the 422 short-circuit worked."""
    accepted = SimpleNamespace(**{**application.__dict__, "status": "accepted", "decided_at": datetime.now(timezone.utc)})
    monkeypatch.setattr(main, "transition_application", lambda db, aid, rid, decision: accepted)
    monkeypatch.setattr(
        main, "get_match_result",
        lambda db, mid: SimpleNamespace(matched_requirements=[], missing_requirements=[], final_score=0.8),
    )
    monkeypatch.setattr(main, "get_job", lambda db, jid: SimpleNamespace(title="Some Job"))
    monkeypatch.setattr(main, "list_interview_rounds_for_application", lambda db, aid: [])
    return accepted


# ---------------------------------------------------------------------------
# No requirement configured -- current/legacy behavior must be preserved exactly.
# ---------------------------------------------------------------------------

def test_accept_with_no_language_requirement_is_allowed(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(status="interviewed", interview_assessment=None)
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    _stub_transition_and_feedback(monkeypatch, application)

    resp = _accept(test_client, recruiter_id, application.id)

    assert resp.status_code == 200
    assert resp.json()["language_gate"]["status"] == "not_required"


# ---------------------------------------------------------------------------
# Blocking statuses: pending / failed / needs_review
# ---------------------------------------------------------------------------

def test_accept_blocked_while_pending(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(
        status="invited", required_language="en", required_cefr="B2", interview_assessment=None,
    )
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    called = {"transition": False}
    monkeypatch.setattr(main, "transition_application", lambda *a, **k: called.update(transition=True))

    resp = _accept(test_client, recruiter_id, application.id)

    assert resp.status_code == 422
    assert "pending" in resp.json()["detail"]
    assert called["transition"] is False


def test_accept_blocked_while_failed(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(
        status="interviewed", required_language="en", required_cefr="C1",
        interview_assessment={"language": "en", "cefr_level": "B2", "evaluation_status": "completed", "inconclusive": False},
    )
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    called = {"transition": False}
    monkeypatch.setattr(main, "transition_application", lambda *a, **k: called.update(transition=True))

    resp = _accept(test_client, recruiter_id, application.id)

    assert resp.status_code == 422
    body = resp.json()
    assert "failed" in body["detail"]
    assert called["transition"] is False
    assert body["detail"].count("C1") >= 1 and "B2" in body["detail"]


def test_accept_blocked_while_needs_review_language_mismatch(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(
        status="interviewed", required_language="en", required_cefr="B2",
        interview_assessment={"language": "fr", "cefr_level": "C1", "evaluation_status": "completed", "inconclusive": False},
    )
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    called = {"transition": False}
    monkeypatch.setattr(main, "transition_application", lambda *a, **k: called.update(transition=True))

    resp = _accept(test_client, recruiter_id, application.id)

    assert resp.status_code == 422
    assert "needs_review" in resp.json()["detail"]
    assert called["transition"] is False


def test_accept_blocked_while_needs_review_missing_score(client, monkeypatch):
    # VoiceHR's own evaluation failed -- an interview result exists, but no CEFR to compare.
    test_client, recruiter_id = client
    application = _fake_application(
        status="interviewed", required_language="en", required_cefr="B2",
        interview_assessment={"language": "en", "cefr_level": None, "evaluation_status": "failed", "inconclusive": False},
    )
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    monkeypatch.setattr(main, "transition_application", lambda *a, **k: pytest.fail("must not be called"))

    resp = _accept(test_client, recruiter_id, application.id)

    assert resp.status_code == 422
    assert "needs_review" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Passed -- accept proceeds to the real state-machine transition.
# ---------------------------------------------------------------------------

def test_accept_allowed_when_passed(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(
        status="interviewed", required_language="en", required_cefr="B2",
        interview_assessment={"language": "en", "cefr_level": "C1", "evaluation_status": "completed", "inconclusive": False},
    )
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    accepted = _stub_transition_and_feedback(monkeypatch, application)

    resp = _accept(test_client, recruiter_id, application.id)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["language_gate"]["status"] == "passed"


def test_accept_allowed_when_passed_at_exact_required_level(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(
        status="interviewed", required_language="en", required_cefr="B2",
        interview_assessment={"language": "en", "cefr_level": "B2", "evaluation_status": "completed", "inconclusive": False},
    )
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    _stub_transition_and_feedback(monkeypatch, application)

    resp = _accept(test_client, recruiter_id, application.id)

    assert resp.status_code == 200
    assert resp.json()["language_gate"]["status"] == "passed"


def test_accept_returns_404_for_unknown_application_before_gate_check(client, monkeypatch):
    test_client, recruiter_id = client
    monkeypatch.setattr(main, "get_application", lambda db, aid: None)

    resp = _accept(test_client, recruiter_id, uuid.uuid4())

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Reject is never gated -- a recruiter can always say no regardless of screening.
# ---------------------------------------------------------------------------

def test_reject_is_never_blocked_by_the_language_gate(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(
        status="invited", required_language="en", required_cefr="B2", interview_assessment=None,
    )
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    rejected = SimpleNamespace(**{**application.__dict__, "status": "rejected", "decided_at": datetime.now(timezone.utc)})
    monkeypatch.setattr(main, "transition_application", lambda db, aid, rid, decision: rejected)
    monkeypatch.setattr(
        main, "get_match_result",
        lambda db, mid: SimpleNamespace(matched_requirements=[], missing_requirements=[], final_score=0.8),
    )
    monkeypatch.setattr(main, "get_job", lambda db, jid: SimpleNamespace(title="Some Job"))
    monkeypatch.setattr(main, "list_interview_rounds_for_application", lambda db, aid: [])

    resp = test_client.post(
        f"/v1/applications/{application.id}/reject", headers=_auth_header(recruiter_id)
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
