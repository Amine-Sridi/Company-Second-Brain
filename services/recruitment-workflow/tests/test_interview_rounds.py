"""
tests/test_interview_rounds.py
------------------------------
Route-level tests for POST /v1/applications/{id}/interview-rounds (main.py:
create_interview_round_endpoint) and for interview_rounds being embedded in every
existing Application read path (main.py: _application_response).

No real Postgres: db.py's create_interview_round/list_interview_rounds_for_application/
get_application are monkeypatched directly, the same pattern test_accept_language_gate.py
already established for this test suite. Each fake Application/InterviewRound is a
plain SimpleNamespace carrying exactly the fields the code under test reads.
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


def _auth_header(recruiter_id: uuid.UUID) -> dict:
    return {"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"}


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


def _fake_round(
    application_id, created_by, round_type="technical", status="scheduled", outcome=None,
    notification_status="pending", notification_error=None,
):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        application_id=application_id,
        round_type=round_type,
        status=status,
        outcome=outcome,
        interviewer_name="Jane Doe",
        interviewer_email="jane.doe@example.com",
        location="https://meet.example.com/room",
        scheduled_at=now,
        notes=None,
        notification_status=notification_status,
        notification_error=notification_error,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )


def _create_round(test_client, recruiter_id, application_id, **overrides):
    body = {
        "round_type": "technical",
        "interviewer_name": "Jane Doe",
        "interviewer_email": "jane.doe@example.com",
        "location": "https://meet.example.com/room",
        "scheduled_at": "2026-10-01T10:00:00Z",
    }
    body.update(overrides)
    return test_client.post(
        f"/v1/applications/{application_id}/interview-rounds",
        json=body,
        headers=_auth_header(recruiter_id),
    )


# ---------------------------------------------------------------------------
# Notification mocking helpers -- create_interview_round_endpoint now always
# attempts a candidate+interviewer email after creating the round (see
# main.py: _notify_interview_round), so every test exercising that endpoint's
# success path needs resume_intelligence_client.get_contact and
# interview_round_mailer.send_interview_round_notification mocked, the same way
# it already needs get_application/create_interview_round mocked.
# ---------------------------------------------------------------------------

async def _fake_get_contact_success(base_url, api_key, candidate_id):
    return {
        "candidate_id": str(candidate_id), "name": "Test Candidate",
        "email": "candidate@example.com", "phone": None,
    }


async def _fake_send_notification_success(**kwargs):
    return None


def _mock_successful_notification(monkeypatch, round_namespace):
    """Wires the notification path to succeed and makes
    main.update_interview_round_notification write its result onto
    round_namespace in place, so the response reflects the real computed
    notification_status/notification_error rather than _fake_round's own
    ('pending') default."""
    import interview_round_mailer
    import resume_intelligence_client

    monkeypatch.setattr(resume_intelligence_client, "get_contact", _fake_get_contact_success)
    monkeypatch.setattr(
        interview_round_mailer, "send_interview_round_notification", _fake_send_notification_success
    )
    monkeypatch.setattr(main, "get_job", lambda db, jid: SimpleNamespace(id=jid, title="Backend Engineer"))

    def _fake_update_notification(db, round_id, notification_status, notification_error):
        round_namespace.notification_status = notification_status
        round_namespace.notification_error = notification_error
        return round_namespace

    monkeypatch.setattr(main, "update_interview_round_notification", _fake_update_notification)


# ---------------------------------------------------------------------------
# Create succeeds once the language gate is clear.
# ---------------------------------------------------------------------------

def test_create_succeeds_when_no_language_requirement_configured(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(status="interviewed", interview_assessment=None)
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)

    created = _fake_round(application.id, recruiter_id)
    captured = {}

    def _create(db, **kwargs):
        captured.update(kwargs)
        return created

    monkeypatch.setattr(main, "create_interview_round", _create)
    _mock_successful_notification(monkeypatch, created)

    resp = _create_round(test_client, recruiter_id, application.id)

    assert resp.status_code == 201
    body = resp.json()
    assert body["round_type"] == "technical"
    assert body["status"] == "scheduled"
    assert body["outcome"] is None
    assert body["interviewer_name"] == "Jane Doe"
    assert body["notification_status"] == "sent"
    assert captured["application_id"] == application.id
    assert captured["created_by"] == recruiter_id
    assert captured["round_type"] == "technical"
    assert captured["interviewer_email"] == "jane.doe@example.com"
    assert captured["location"] == "https://meet.example.com/room"


def test_create_succeeds_when_language_gate_passed(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(
        status="interviewed", required_language="en", required_cefr="B2",
        interview_assessment={"language": "en", "cefr_level": "C1", "evaluation_status": "completed", "inconclusive": False},
    )
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    created = _fake_round(application.id, recruiter_id, round_type="managerial")
    monkeypatch.setattr(main, "create_interview_round", lambda db, **kwargs: created)
    _mock_successful_notification(monkeypatch, created)

    resp = _create_round(test_client, recruiter_id, application.id, round_type="managerial")

    assert resp.status_code == 201
    assert resp.json()["round_type"] == "managerial"


# ---------------------------------------------------------------------------
# Blocked while the language gate is pending/failed/needs_review.
# ---------------------------------------------------------------------------

def test_create_blocked_while_language_pending(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(
        status="invited", required_language="en", required_cefr="B2", interview_assessment=None,
    )
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    monkeypatch.setattr(main, "create_interview_round", lambda *a, **k: pytest.fail("must not be called"))

    resp = _create_round(test_client, recruiter_id, application.id)

    assert resp.status_code == 422
    assert "pending" in resp.json()["detail"]


def test_create_blocked_while_language_failed(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(
        status="interviewed", required_language="en", required_cefr="C1",
        interview_assessment={"language": "en", "cefr_level": "B2", "evaluation_status": "completed", "inconclusive": False},
    )
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    monkeypatch.setattr(main, "create_interview_round", lambda *a, **k: pytest.fail("must not be called"))

    resp = _create_round(test_client, recruiter_id, application.id)

    assert resp.status_code == 422
    assert "failed" in resp.json()["detail"]


def test_create_blocked_while_language_needs_review(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(
        status="interviewed", required_language="en", required_cefr="B2",
        interview_assessment={"language": "fr", "cefr_level": "C1", "evaluation_status": "completed", "inconclusive": False},
    )
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    monkeypatch.setattr(main, "create_interview_round", lambda *a, **k: pytest.fail("must not be called"))

    resp = _create_round(test_client, recruiter_id, application.id)

    assert resp.status_code == 422
    assert "needs_review" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Blocked after a terminal accept/reject decision, regardless of the gate.
# ---------------------------------------------------------------------------

def test_create_blocked_after_accepted(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(
        status="accepted", required_language="en", required_cefr="B2",
        interview_assessment={"language": "en", "cefr_level": "C1", "evaluation_status": "completed", "inconclusive": False},
    )
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    monkeypatch.setattr(main, "create_interview_round", lambda *a, **k: pytest.fail("must not be called"))

    resp = _create_round(test_client, recruiter_id, application.id)

    assert resp.status_code == 422
    assert "accepted" in resp.json()["detail"]


def test_create_blocked_after_rejected(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(status="rejected")
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    monkeypatch.setattr(main, "create_interview_round", lambda *a, **k: pytest.fail("must not be called"))

    resp = _create_round(test_client, recruiter_id, application.id)

    assert resp.status_code == 422
    assert "rejected" in resp.json()["detail"]


def test_create_returns_404_for_unknown_application(client, monkeypatch):
    test_client, recruiter_id = client
    monkeypatch.setattr(main, "get_application", lambda db, aid: None)

    resp = _create_round(test_client, recruiter_id, uuid.uuid4())

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# interviewer_email is validated at the request-schema boundary (EmailStr) --
# rejected with a 422 before the route body (and so before create_interview_round)
# ever runs.
# ---------------------------------------------------------------------------

def test_create_rejects_malformed_interviewer_email(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(status="interviewed", interview_assessment=None)
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    monkeypatch.setattr(main, "create_interview_round", lambda *a, **k: pytest.fail("must not be called"))

    resp = _create_round(test_client, recruiter_id, application.id, interviewer_email="not-an-email")

    assert resp.status_code == 422


def test_create_rejects_missing_interviewer_email(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(status="interviewed", interview_assessment=None)
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    monkeypatch.setattr(main, "create_interview_round", lambda *a, **k: pytest.fail("must not be called"))

    body = {
        "round_type": "technical",
        "interviewer_name": "Jane Doe",
        "scheduled_at": "2026-10-01T10:00:00Z",
    }
    resp = test_client.post(
        f"/v1/applications/{application.id}/interview-rounds",
        json=body,
        headers=_auth_header(recruiter_id),
    )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Notification best-effort: a failure at either the contact lookup or the mailer
# itself must never block round creation -- see main.py's
# create_interview_round_endpoint / _notify_interview_round docstrings.
# ---------------------------------------------------------------------------

def test_create_still_succeeds_when_notification_provider_fails(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(status="interviewed", interview_assessment=None)
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)

    created = _fake_round(application.id, recruiter_id)
    monkeypatch.setattr(main, "create_interview_round", lambda db, **kwargs: created)
    monkeypatch.setattr(main, "get_job", lambda db, jid: SimpleNamespace(id=jid, title="Backend Engineer"))

    import interview_round_mailer
    import resume_intelligence_client

    monkeypatch.setattr(resume_intelligence_client, "get_contact", _fake_get_contact_success)

    async def _failing_send(**kwargs):
        raise interview_round_mailer.NotificationDeliveryError("simulated Resend failure")

    monkeypatch.setattr(interview_round_mailer, "send_interview_round_notification", _failing_send)

    def _fake_update_notification(db, round_id, notification_status, notification_error):
        created.notification_status = notification_status
        created.notification_error = notification_error
        return created

    monkeypatch.setattr(main, "update_interview_round_notification", _fake_update_notification)

    resp = _create_round(test_client, recruiter_id, application.id)

    # The round is still created (201), never blocked or rolled back by the
    # notification failure -- only its notification_status/error reflect it.
    assert resp.status_code == 201
    body = resp.json()
    assert body["notification_status"] == "failed"
    assert "simulated Resend failure" in body["notification_error"]


def test_create_still_succeeds_when_candidate_contact_lookup_fails(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(status="interviewed", interview_assessment=None)
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)

    created = _fake_round(application.id, recruiter_id)
    monkeypatch.setattr(main, "create_interview_round", lambda db, **kwargs: created)
    monkeypatch.setattr(main, "get_job", lambda db, jid: SimpleNamespace(id=jid, title="Backend Engineer"))

    import resume_intelligence_client

    # No contact registered for this candidate -- get_contact returns None, the
    # real client module's own "not found" signal (see resume_intelligence_client).
    async def _no_contact(base_url, api_key, candidate_id):
        return None

    monkeypatch.setattr(resume_intelligence_client, "get_contact", _no_contact)

    def _fake_update_notification(db, round_id, notification_status, notification_error):
        created.notification_status = notification_status
        created.notification_error = notification_error
        return created

    monkeypatch.setattr(main, "update_interview_round_notification", _fake_update_notification)

    resp = _create_round(test_client, recruiter_id, application.id)

    assert resp.status_code == 201
    body = resp.json()
    assert body["notification_status"] == "failed"
    assert "contact" in body["notification_error"].lower()


def test_create_notification_never_persists_candidate_contact_fields(client, monkeypatch):
    """The interviewer_email/location/notes fields actually persisted (captured via
    the create_interview_round call) must never contain the candidate's own
    email/name -- those are only ever passed to the mailer in memory, never stored
    on the round itself."""
    test_client, recruiter_id = client
    application = _fake_application(status="interviewed", interview_assessment=None)
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)

    created = _fake_round(application.id, recruiter_id)
    captured = {}

    def _create(db, **kwargs):
        captured.update(kwargs)
        return created

    monkeypatch.setattr(main, "create_interview_round", _create)
    _mock_successful_notification(monkeypatch, created)

    resp = _create_round(test_client, recruiter_id, application.id)

    assert resp.status_code == 201
    candidate_email = "candidate@example.com"
    candidate_name = "Test Candidate"
    for value in captured.values():
        if isinstance(value, str):
            assert candidate_email not in value
            assert candidate_name not in value


# ---------------------------------------------------------------------------
# interview_rounds is embedded on every existing Application read path.
# ---------------------------------------------------------------------------

def test_select_candidate_response_embeds_empty_interview_rounds(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(status="selected")
    match = SimpleNamespace(
        id=application.match_result_id, job_id=application.job_id,
        candidate_id=application.candidate_id, qualified=True,
    )
    monkeypatch.setattr(main, "get_match_result", lambda db, mid: match)
    monkeypatch.setattr(main, "get_job", lambda db, jid: SimpleNamespace(
        required_language=None, min_language_cefr=None,
    ))
    monkeypatch.setattr(main, "create_application", lambda db, **kwargs: application)
    monkeypatch.setattr(main, "list_interview_rounds_for_application", lambda db, aid: [])

    resp = test_client.post(
        f"/v1/matches/{match.id}/select", headers=_auth_header(recruiter_id)
    )

    assert resp.status_code == 201
    assert resp.json()["interview_rounds"] == []


def test_job_applications_list_embeds_existing_rounds(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(status="interviewed")
    round_ = _fake_round(application.id, recruiter_id, status="completed", outcome="pass")

    monkeypatch.setattr(main, "get_job", lambda db, jid: SimpleNamespace(id=jid))
    monkeypatch.setattr(main, "list_applications_for_job", lambda db, jid: [application])
    monkeypatch.setattr(
        main, "list_interview_rounds_for_application",
        lambda db, aid: [round_] if aid == application.id else [],
    )

    resp = test_client.get(
        f"/v1/jobs/{application.job_id}/applications", headers=_auth_header(recruiter_id)
    )

    assert resp.status_code == 200
    rounds = resp.json()[0]["interview_rounds"]
    assert len(rounds) == 1
    assert rounds[0]["id"] == str(round_.id)
    assert rounds[0]["status"] == "completed"
    assert rounds[0]["outcome"] == "pass"
    assert rounds[0]["interviewer_name"] == "Jane Doe"


def test_global_applications_list_embeds_existing_rounds(client, monkeypatch):
    test_client, recruiter_id = client
    application = _fake_application(status="interviewed")
    round_ = _fake_round(application.id, recruiter_id, status="scheduled")

    monkeypatch.setattr(main, "list_all_applications", lambda db: [(application, "Some Job")])
    monkeypatch.setattr(
        main, "list_interview_rounds_for_application",
        lambda db, aid: [round_] if aid == application.id else [],
    )

    resp = test_client.get("/v1/applications", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    body = resp.json()[0]
    assert body["job_title"] == "Some Job"
    assert len(body["interview_rounds"]) == 1
    assert body["interview_rounds"][0]["round_type"] == round_.round_type


# ---------------------------------------------------------------------------
# Reschedule -- only from 'scheduled'
# ---------------------------------------------------------------------------

def _reschedule(test_client, recruiter_id, application_id, round_id, scheduled_at="2026-11-01T09:00:00Z"):
    return test_client.post(
        f"/v1/applications/{application_id}/interview-rounds/{round_id}/reschedule",
        json={"scheduled_at": scheduled_at},
        headers=_auth_header(recruiter_id),
    )


def test_reschedule_succeeds_from_scheduled(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    existing = _fake_round(application_id, recruiter_id, status="scheduled")
    updated = SimpleNamespace(**{**existing.__dict__, "scheduled_at": datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)})

    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: existing if rid == existing.id else None)
    monkeypatch.setattr(main, "reschedule_interview_round", lambda db, rid, scheduled_at: updated)

    resp = _reschedule(test_client, recruiter_id, application_id, existing.id)

    assert resp.status_code == 200
    assert resp.json()["scheduled_at"] == "2026-11-01T09:00:00Z"


def test_reschedule_blocked_when_not_scheduled(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    existing = _fake_round(application_id, recruiter_id, status="completed", outcome="pass")

    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: existing)
    monkeypatch.setattr(main, "reschedule_interview_round", lambda db, rid, scheduled_at: None)

    resp = _reschedule(test_client, recruiter_id, application_id, existing.id)

    assert resp.status_code == 409
    assert "completed" in resp.json()["detail"]


def test_reschedule_returns_404_for_unknown_round(client, monkeypatch):
    test_client, recruiter_id = client
    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: None)
    monkeypatch.setattr(main, "reschedule_interview_round", lambda *a, **k: pytest.fail("must not be called"))

    resp = _reschedule(test_client, recruiter_id, uuid.uuid4(), uuid.uuid4())

    assert resp.status_code == 404


def test_reschedule_returns_404_when_round_belongs_to_a_different_application(client, monkeypatch):
    test_client, recruiter_id = client
    real_application_id = uuid.uuid4()
    other_application_id = uuid.uuid4()
    existing = _fake_round(other_application_id, recruiter_id, status="scheduled")

    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: existing)
    monkeypatch.setattr(main, "reschedule_interview_round", lambda *a, **k: pytest.fail("must not be called"))

    resp = _reschedule(test_client, recruiter_id, real_application_id, existing.id)

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Complete -- only from 'scheduled', outcome required
# ---------------------------------------------------------------------------

def _complete(test_client, recruiter_id, application_id, round_id, outcome="pass", notes=None):
    body = {"outcome": outcome}
    if notes is not None:
        body["notes"] = notes
    return test_client.post(
        f"/v1/applications/{application_id}/interview-rounds/{round_id}/complete",
        json=body,
        headers=_auth_header(recruiter_id),
    )


def test_complete_succeeds_from_scheduled(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    existing = _fake_round(application_id, recruiter_id, status="scheduled")
    completed = SimpleNamespace(**{**existing.__dict__, "status": "completed", "outcome": "pass", "notes": "Strong on system design."})

    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: existing)
    captured = {}

    def _complete_fn(db, rid, outcome, notes):
        captured.update(rid=rid, outcome=outcome, notes=notes)
        return completed

    monkeypatch.setattr(main, "complete_interview_round", _complete_fn)

    resp = _complete(test_client, recruiter_id, application_id, existing.id, outcome="pass", notes="Strong on system design.")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["outcome"] == "pass"
    assert body["notes"] == "Strong on system design."
    assert captured == {"rid": existing.id, "outcome": "pass", "notes": "Strong on system design."}


def test_complete_without_notes_does_not_pass_notes_through(client, monkeypatch):
    """Omitting notes must reach complete_interview_round as None, not an empty
    string or the request dropping the field silently -- db.py's own
    complete_interview_round relies on exactly this to decide whether to
    overwrite the existing notes."""
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    existing = _fake_round(application_id, recruiter_id, status="scheduled")
    completed = SimpleNamespace(**{**existing.__dict__, "status": "completed", "outcome": "fail"})

    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: existing)
    captured = {}

    def _complete_fn(db, rid, outcome, notes):
        captured.update(outcome=outcome, notes=notes)
        return completed

    monkeypatch.setattr(main, "complete_interview_round", _complete_fn)

    resp = _complete(test_client, recruiter_id, application_id, existing.id, outcome="fail")

    assert resp.status_code == 200
    assert captured["notes"] is None


def test_complete_rejects_invalid_outcome_before_reaching_the_db(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: pytest.fail("must not be called"))
    monkeypatch.setattr(main, "complete_interview_round", lambda *a, **k: pytest.fail("must not be called"))

    resp = _complete(test_client, recruiter_id, application_id, uuid.uuid4(), outcome="maybe")

    assert resp.status_code == 422


def test_complete_blocked_when_not_scheduled(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    existing = _fake_round(application_id, recruiter_id, status="cancelled")

    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: existing)
    monkeypatch.setattr(main, "complete_interview_round", lambda db, rid, outcome, notes: None)

    resp = _complete(test_client, recruiter_id, application_id, existing.id)

    assert resp.status_code == 409
    assert "cancelled" in resp.json()["detail"]


def test_complete_returns_404_for_unknown_round(client, monkeypatch):
    test_client, recruiter_id = client
    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: None)
    monkeypatch.setattr(main, "complete_interview_round", lambda *a, **k: pytest.fail("must not be called"))

    resp = _complete(test_client, recruiter_id, uuid.uuid4(), uuid.uuid4())

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cancel -- only from 'scheduled'
# ---------------------------------------------------------------------------

def _cancel(test_client, recruiter_id, application_id, round_id):
    return test_client.post(
        f"/v1/applications/{application_id}/interview-rounds/{round_id}/cancel",
        headers=_auth_header(recruiter_id),
    )


def test_cancel_succeeds_from_scheduled(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    existing = _fake_round(application_id, recruiter_id, status="scheduled")
    cancelled = SimpleNamespace(**{**existing.__dict__, "status": "cancelled"})

    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: existing)
    monkeypatch.setattr(main, "cancel_interview_round", lambda db, rid: cancelled)

    resp = _cancel(test_client, recruiter_id, application_id, existing.id)

    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_cancel_blocked_when_not_scheduled(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    existing = _fake_round(application_id, recruiter_id, status="completed", outcome="needs_review")

    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: existing)
    monkeypatch.setattr(main, "cancel_interview_round", lambda db, rid: None)

    resp = _cancel(test_client, recruiter_id, application_id, existing.id)

    assert resp.status_code == 409
    assert "completed" in resp.json()["detail"]


def test_cancel_returns_404_for_unknown_round(client, monkeypatch):
    test_client, recruiter_id = client
    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: None)
    monkeypatch.setattr(main, "cancel_interview_round", lambda *a, **k: pytest.fail("must not be called"))

    resp = _cancel(test_client, recruiter_id, uuid.uuid4(), uuid.uuid4())

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Resend notification -- explicit resend, only from 'scheduled', 10s cooldown,
# never creates a new round (see main.py: resend_interview_round_notification_
# endpoint). Unlike create, a failure here IS the request's own failure (502) --
# resending a notification has no other value if delivery itself fails.
# ---------------------------------------------------------------------------

def _resend_notification(test_client, recruiter_id, application_id, round_id):
    return test_client.post(
        f"/v1/applications/{application_id}/interview-rounds/{round_id}/resend-notification",
        headers=_auth_header(recruiter_id),
    )


def _mock_application_and_job_for_resend(monkeypatch, application_id, candidate_id=None):
    application = SimpleNamespace(
        id=application_id, job_id=uuid.uuid4(), candidate_id=candidate_id or uuid.uuid4(),
    )
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    monkeypatch.setattr(main, "get_job", lambda db, jid: SimpleNamespace(id=jid, title="Backend Engineer"))
    return application


def test_resend_notification_succeeds_and_never_creates_a_new_round(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    existing = _fake_round(application_id, recruiter_id, status="scheduled")
    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: existing)
    _mock_application_and_job_for_resend(monkeypatch, application_id)
    _mock_successful_notification(monkeypatch, existing)
    monkeypatch.setattr(
        main, "create_interview_round", lambda *a, **k: pytest.fail("must never create a new round")
    )

    resp = _resend_notification(test_client, recruiter_id, application_id, existing.id)

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(existing.id)
    assert body["notification_status"] == "sent"


def test_resend_notification_failure_returns_502_but_still_records_status(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    existing = _fake_round(application_id, recruiter_id, status="scheduled")
    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: existing)
    _mock_application_and_job_for_resend(monkeypatch, application_id)

    import interview_round_mailer
    import resume_intelligence_client

    monkeypatch.setattr(resume_intelligence_client, "get_contact", _fake_get_contact_success)

    async def _failing_send(**kwargs):
        raise interview_round_mailer.NotificationDeliveryError("simulated Resend failure")

    monkeypatch.setattr(interview_round_mailer, "send_interview_round_notification", _failing_send)

    recorded = {}

    def _fake_update_notification(db, round_id, notification_status, notification_error):
        recorded["status"] = notification_status
        recorded["error"] = notification_error
        existing.notification_status = notification_status
        existing.notification_error = notification_error
        return existing

    monkeypatch.setattr(main, "update_interview_round_notification", _fake_update_notification)

    resp = _resend_notification(test_client, recruiter_id, application_id, existing.id)

    assert resp.status_code == 502
    assert "simulated Resend failure" in resp.json()["detail"]
    # The real outcome is still persisted even though the response itself is an
    # error -- a later read must not show a stale pre-attempt status.
    assert recorded["status"] == "failed"
    assert "simulated Resend failure" in recorded["error"]


def test_resend_notification_blocked_when_not_scheduled(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    existing = _fake_round(application_id, recruiter_id, status="completed", outcome="pass")
    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: existing)
    monkeypatch.setattr(
        main, "update_interview_round_notification", lambda *a, **k: pytest.fail("must not be called")
    )

    resp = _resend_notification(test_client, recruiter_id, application_id, existing.id)

    assert resp.status_code == 409
    assert "completed" in resp.json()["detail"]


def test_resend_notification_returns_404_for_unknown_round(client, monkeypatch):
    test_client, recruiter_id = client
    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: None)

    resp = _resend_notification(test_client, recruiter_id, uuid.uuid4(), uuid.uuid4())

    assert resp.status_code == 404


def test_resend_notification_404_when_round_belongs_to_a_different_application(client, monkeypatch):
    test_client, recruiter_id = client
    real_application_id = uuid.uuid4()
    existing = _fake_round(real_application_id, recruiter_id, status="scheduled")
    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: existing)

    resp = _resend_notification(test_client, recruiter_id, uuid.uuid4(), existing.id)

    assert resp.status_code == 404


def test_resend_notification_enforces_a_cooldown_between_rapid_attempts(client, monkeypatch):
    test_client, recruiter_id = client
    application_id = uuid.uuid4()
    existing = _fake_round(application_id, recruiter_id, status="scheduled")
    monkeypatch.setattr(main, "get_interview_round", lambda db, rid: existing)
    _mock_application_and_job_for_resend(monkeypatch, application_id)
    _mock_successful_notification(monkeypatch, existing)

    first = _resend_notification(test_client, recruiter_id, application_id, existing.id)
    assert first.status_code == 200

    second = _resend_notification(test_client, recruiter_id, application_id, existing.id)
    assert second.status_code == 429
