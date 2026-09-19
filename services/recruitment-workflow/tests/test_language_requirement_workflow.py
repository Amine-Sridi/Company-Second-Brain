"""
tests/test_language_requirement_workflow.py
------------------------------
Covers the parts of the job-specific VoiceHR language eligibility gate that
test_language_gate.py (the pure function) and test_accept_language_gate.py (accept
enforcement) don't: that a job's requirement is snapshotted onto an Application at
*selection* time, that an invite uses that snapshot's language, that the
PATCH .../language-requirement endpoint works and never touches existing
Applications, and that a duplicate VoiceHR webhook delivery still can't double-apply
an interview result. Same no-real-Postgres monkeypatch pattern as the rest of this
suite (see test_jobs_list.py).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

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


# ---------------------------------------------------------------------------
# Selection-time snapshot: POST /v1/matches/{id}/select
# ---------------------------------------------------------------------------

def test_select_snapshots_job_language_requirement_onto_application(client, monkeypatch):
    test_client, recruiter_id = client
    job_id = uuid.uuid4()
    match = SimpleNamespace(id=uuid.uuid4(), job_id=job_id, candidate_id=uuid.uuid4(), qualified=True)
    job = SimpleNamespace(id=job_id, required_language="fr", min_language_cefr="C1")

    monkeypatch.setattr(main, "get_match_result", lambda db, mid: match)
    monkeypatch.setattr(main, "get_job", lambda db, jid: job)

    captured = {}

    def fake_create_application(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            id=uuid.uuid4(), job_id=job_id, match_result_id=match.id, candidate_id=match.candidate_id,
            status="selected", selected_at=datetime.now(timezone.utc), invite_attempt_number=0,
            invited_at=None, interview_status=None, interview_assessment=None,
            interview_completed_at=None, decided_at=None,
            required_language=kwargs["required_language"], required_cefr=kwargs["required_cefr"],
        )

    monkeypatch.setattr(main, "create_application", fake_create_application)
    monkeypatch.setattr(main, "list_interview_rounds_for_application", lambda db, aid: [])

    resp = test_client.post(f"/v1/matches/{match.id}/select", headers=_auth_header(recruiter_id))

    assert resp.status_code == 201
    assert captured["required_language"] == "fr"
    assert captured["required_cefr"] == "C1"
    body = resp.json()
    assert body["language_gate"] == {
        "status": "pending",
        "required_language": "fr",
        "required_cefr": "C1",
        "assessed_language": None,
        "assessed_cefr": None,
    }


def test_select_on_job_without_requirement_snapshots_nulls(client, monkeypatch):
    test_client, recruiter_id = client
    job_id = uuid.uuid4()
    match = SimpleNamespace(id=uuid.uuid4(), job_id=job_id, candidate_id=uuid.uuid4(), qualified=True)
    job = SimpleNamespace(id=job_id, required_language=None, min_language_cefr=None)

    monkeypatch.setattr(main, "get_match_result", lambda db, mid: match)
    monkeypatch.setattr(main, "get_job", lambda db, jid: job)

    captured = {}

    def fake_create_application(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            id=uuid.uuid4(), job_id=job_id, match_result_id=match.id, candidate_id=match.candidate_id,
            status="selected", selected_at=datetime.now(timezone.utc), invite_attempt_number=0,
            invited_at=None, interview_status=None, interview_assessment=None,
            interview_completed_at=None, decided_at=None,
            required_language=None, required_cefr=None,
        )

    monkeypatch.setattr(main, "create_application", fake_create_application)
    monkeypatch.setattr(main, "list_interview_rounds_for_application", lambda db, aid: [])

    resp = test_client.post(f"/v1/matches/{match.id}/select", headers=_auth_header(recruiter_id))

    assert resp.status_code == 201
    assert captured["required_language"] is None
    assert captured["required_cefr"] is None
    assert resp.json()["language_gate"]["status"] == "not_required"


# ---------------------------------------------------------------------------
# PATCH /v1/jobs/{id}/language-requirement
# ---------------------------------------------------------------------------

def test_patch_language_requirement_sets_and_returns_it(client, monkeypatch):
    test_client, recruiter_id = client
    job_id = uuid.uuid4()
    updated_job = SimpleNamespace(
        id=job_id, title="Backend Engineer", created_at=datetime.now(timezone.utc),
        required_language="en", min_language_cefr="B2",
    )
    monkeypatch.setattr(main, "set_job_language_requirement", lambda db, jid, lang, cefr: updated_job)
    monkeypatch.setattr(main, "list_match_results_for_job", lambda db, jid: [object(), object()])
    monkeypatch.setattr(main, "get_job_description_for_job", lambda db, jid: None)

    resp = test_client.patch(
        f"/v1/jobs/{job_id}/language-requirement",
        headers=_auth_header(recruiter_id),
        json={"required_language": "en", "min_language_cefr": "B2"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["required_language"] == "en"
    assert body["min_language_cefr"] == "B2"
    assert body["match_count"] == 2
    assert body["extraction_status"] == "clean"
    assert body["extraction_warnings"] == []


def test_patch_language_requirement_rejects_unpaired_fields(client):
    test_client, recruiter_id = client
    resp = test_client.patch(
        f"/v1/jobs/{uuid.uuid4()}/language-requirement",
        headers=_auth_header(recruiter_id),
        json={"required_language": "en"},
    )
    assert resp.status_code == 422


def test_patch_language_requirement_unknown_job_returns_404(client, monkeypatch):
    test_client, recruiter_id = client
    monkeypatch.setattr(main, "set_job_language_requirement", lambda db, jid, lang, cefr: None)

    resp = test_client.patch(
        f"/v1/jobs/{uuid.uuid4()}/language-requirement",
        headers=_auth_header(recruiter_id),
        json={"required_language": "en", "min_language_cefr": "B2"},
    )
    assert resp.status_code == 404


def test_patch_language_requirement_can_clear_it(client, monkeypatch):
    test_client, recruiter_id = client
    job_id = uuid.uuid4()
    cleared_job = SimpleNamespace(
        id=job_id, title="Backend Engineer", created_at=datetime.now(timezone.utc),
        required_language=None, min_language_cefr=None,
    )
    monkeypatch.setattr(main, "set_job_language_requirement", lambda db, jid, lang, cefr: cleared_job)
    monkeypatch.setattr(main, "list_match_results_for_job", lambda db, jid: [])
    monkeypatch.setattr(main, "get_job_description_for_job", lambda db, jid: None)

    resp = test_client.patch(
        f"/v1/jobs/{job_id}/language-requirement",
        headers=_auth_header(recruiter_id),
        json={"required_language": None, "min_language_cefr": None},
    )
    assert resp.status_code == 200
    assert resp.json()["required_language"] is None


def test_patch_language_requirement_preserves_a_real_flagged_extraction_status(client, monkeypatch):
    # The PATCH response must reflect this job's *actual* extraction_status/
    # extraction_warnings (re-read from JobDescription), not silently reset them
    # to the JobSummaryResponse schema's own "clean"/[] defaults.
    test_client, recruiter_id = client
    job_id = uuid.uuid4()
    updated_job = SimpleNamespace(
        id=job_id, title="Backend Engineer", created_at=datetime.now(timezone.utc),
        required_language="en", min_language_cefr="B2",
    )
    warnings = [{"field": "titles", "message": "7 target title variants were extracted"}]
    flagged_job_description = SimpleNamespace(extraction_status="flagged", extraction_warnings=warnings)
    monkeypatch.setattr(main, "set_job_language_requirement", lambda db, jid, lang, cefr: updated_job)
    monkeypatch.setattr(main, "list_match_results_for_job", lambda db, jid: [])
    monkeypatch.setattr(main, "get_job_description_for_job", lambda db, jid: flagged_job_description)

    resp = test_client.patch(
        f"/v1/jobs/{job_id}/language-requirement",
        headers=_auth_header(recruiter_id),
        json={"required_language": "en", "min_language_cefr": "B2"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["extraction_status"] == "flagged"
    assert body["extraction_warnings"] == warnings


# ---------------------------------------------------------------------------
# Invite uses the application's snapshotted language, not a manual override
# ---------------------------------------------------------------------------

def test_invite_uses_snapshotted_required_language(client, monkeypatch):
    test_client, recruiter_id = client
    application = SimpleNamespace(
        id=uuid.uuid4(), status="selected", invite_attempt_number=0, job_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(), required_language="fr", required_cefr="C1",
    )
    monkeypatch.setattr(main, "get_application", lambda db, aid: application)
    monkeypatch.setattr(main, "get_job", lambda db, jid: SimpleNamespace(title="Backend Engineer"))

    async def fake_get_contact(*a, **k):
        return {"name": "Amine Sridi", "email": "amine@example.com"}

    monkeypatch.setattr(main.resume_intelligence_client, "get_contact", fake_get_contact)

    captured = {}

    async def fake_invite_candidate(*a, **kwargs):
        captured.update(kwargs)
        return {"interview_link": "http://voicehr/interview?token=x", "expires_at": "2099-01-01T00:00:00"}

    monkeypatch.setattr(main.voicehr_client, "invite_candidate", fake_invite_candidate)

    updated = SimpleNamespace(
        id=application.id, job_id=uuid.uuid4(), match_result_id=uuid.uuid4(),
        candidate_id=application.candidate_id, status="invited",
        selected_at=datetime.now(timezone.utc), invite_attempt_number=1,
        invited_at=datetime.now(timezone.utc), interview_status=None, interview_assessment=None,
        interview_completed_at=None, decided_at=None,
        required_language="fr", required_cefr="C1",
    )
    monkeypatch.setattr(main, "record_invite_attempt", lambda db, aid, attempt: updated)
    monkeypatch.setattr(main, "list_interview_rounds_for_application", lambda db, aid: [])

    resp = test_client.post(
        f"/v1/applications/{application.id}/invite",
        headers=_auth_header(recruiter_id),
        json={"language": "en"},  # deliberately different from the snapshot -- must be ignored
    )

    assert resp.status_code == 200
    assert captured["language"] == "fr"
    assert captured["job_title"] == "Backend Engineer"


# ---------------------------------------------------------------------------
# Duplicate webhook delivery: no duplicate state mutation
# ---------------------------------------------------------------------------

def _sign(body: bytes) -> str:
    return hmac.new(main.settings.webhook_hmac_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_duplicate_webhook_delivery_does_not_call_record_interview_result_twice(client, monkeypatch):
    test_client, _ = client
    application_id = str(uuid.uuid4())
    payload = {
        "delivery_id": "dup-1",
        "external_reference_id": application_id,
        "interview_status": "completed",
        "assessment": {
            "overall_score": 88, "language": "en", "cefr_level": "C1",
            "fluency_score": 88, "vocabulary_score": 88, "grammar_score": 88,
            "pronunciation_score": 88, "comprehension_score": 88,
            "inconclusive": False, "evaluation_status": "completed",
        },
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Signature": _sign(body),
        "X-Delivery-Id": "dup-1",
    }

    call_count = {"n": 0}

    def fake_insert_webhook_event(db, **kwargs):
        call_count["n"] += 1
        if call_count["n"] > 1:
            raise IntegrityError("duplicate delivery_id", None, None)
        return SimpleNamespace(id=uuid.uuid4())

    record_calls = {"n": 0}

    def fake_record_interview_result(*a, **k):
        record_calls["n"] += 1
        return SimpleNamespace()  # truthy: "an application was actually updated"

    monkeypatch.setattr(main, "insert_webhook_event", fake_insert_webhook_event)
    monkeypatch.setattr(main, "record_interview_result", fake_record_interview_result)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda db, eid, processed: None)
    # The route's IntegrityError branch calls db.rollback() on a duplicate delivery --
    # the shared client fixture's bare SimpleNamespace() has no such method.
    main.app.dependency_overrides[main.get_db] = lambda: SimpleNamespace(rollback=lambda: None)

    first = test_client.post("/v1/webhooks/voicehr", content=body, headers=headers)
    second = test_client.post("/v1/webhooks/voicehr", content=body, headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate_ignored"
    # record_interview_result must only ever have run once, for the first delivery --
    # the second delivery's IntegrityError short-circuits before touching application state.
    assert record_calls["n"] == 1


def test_webhook_assessment_language_field_is_accepted(client, monkeypatch):
    """Confirms the schema change (shared/schemas.InterviewAssessment.language) doesn't
    break webhook validation -- a 422 here would mean the new field was rejected."""
    test_client, _ = client
    application_id = str(uuid.uuid4())
    payload = {
        "delivery_id": "lang-1",
        "external_reference_id": application_id,
        "interview_status": "completed",
        "assessment": {
            "overall_score": 90, "language": "fr", "cefr_level": "C2",
            "fluency_score": 90, "vocabulary_score": 90, "grammar_score": 90,
            "pronunciation_score": 90, "comprehension_score": 90,
            "inconclusive": False, "evaluation_status": "completed",
        },
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Signature": _sign(body), "X-Delivery-Id": "lang-1"}

    captured = {}
    monkeypatch.setattr(
        main, "insert_webhook_event",
        lambda db, **kwargs: (captured.update(kwargs), SimpleNamespace(id=uuid.uuid4()))[1],
    )
    monkeypatch.setattr(main, "record_interview_result", lambda *a, **k: None)

    resp = test_client.post("/v1/webhooks/voicehr", content=body, headers=headers)

    assert resp.status_code == 200
    assert captured["assessment"]["language"] == "fr"
