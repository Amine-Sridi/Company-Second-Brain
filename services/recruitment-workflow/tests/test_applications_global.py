"""
tests/test_applications_global.py
------------------------------
Auth-boundary and response-shape tests for GET /v1/applications on RWS -- the global
(unscoped) counterpart to the existing GET /v1/jobs/{job_id}/applications.

No real Postgres is required: main.list_all_applications (imported from db.py into
main's namespace) is monkeypatched directly, same style as test_jobs_list.py -- these
tests exercise the route's own auth wiring, ordering/shape passthrough and response
model, not db.py's SQL. The real JOIN/ORDER BY query is exercised by the live smoke
check (see FRONTEND_HANDOFF.md/handoff.md for that pass's results).

Also includes a regression check that GET /v1/jobs/{job_id}/applications (unchanged
by this task) still behaves exactly as before -- it does not gain a job_title field
and remains scoped to one job.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
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


def _fake_application(*, job_id=None, status="selected", **overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid.uuid4(),
        job_id=job_id or uuid.uuid4(),
        match_result_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        status=status,
        selected_at=now,
        invite_attempt_number=0,
        invited_at=None,
        interview_status=None,
        interview_assessment=None,
        interview_completed_at=None,
        decided_at=None,
        required_language=None,
        required_cefr=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Auth boundary
# ---------------------------------------------------------------------------

def test_applications_without_jwt_returns_401(client):
    test_client, _ = client
    resp = test_client.get("/v1/applications")
    assert resp.status_code == 401


def test_applications_with_service_api_key_instead_of_jwt_returns_401(client):
    test_client, _ = client
    resp = test_client.get("/v1/applications", headers={"x-api-key": main.settings.service_api_key})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Response shape / content
# ---------------------------------------------------------------------------

def test_empty_list_returns_empty_array(client, monkeypatch):
    monkeypatch.setattr(main, "list_all_applications", lambda db: [])
    test_client, recruiter_id = client
    resp = test_client.get("/v1/applications", headers=_auth_header(recruiter_id))
    assert resp.status_code == 200
    assert resp.json() == []


def test_multiple_applications_across_jobs_carry_correct_job_title_and_order(client, monkeypatch):
    now = datetime.now(timezone.utc)
    job_a_id = uuid.uuid4()
    job_b_id = uuid.uuid4()

    newest = _fake_application(job_id=job_a_id, status="invited", selected_at=now)
    middle = _fake_application(job_id=job_b_id, status="selected", selected_at=now - timedelta(hours=1))
    oldest = _fake_application(job_id=job_a_id, status="accepted", selected_at=now - timedelta(hours=2))

    # db.py's own query already returns newest-selected-first with each row's job
    # title already joined in; the route must pass both through unchanged.
    monkeypatch.setattr(
        main, "list_all_applications",
        lambda db: [(newest, "Backend Engineer"), (middle, "Data Analyst"), (oldest, "Backend Engineer")],
    )
    monkeypatch.setattr(main, "list_interview_rounds_for_application", lambda db, aid: [])

    test_client, recruiter_id = client
    resp = test_client.get("/v1/applications", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    body = resp.json()
    assert [row["application_id"] for row in body] == [str(newest.id), str(middle.id), str(oldest.id)]
    assert [row["job_title"] for row in body] == ["Backend Engineer", "Data Analyst", "Backend Engineer"]
    assert [row["job_id"] for row in body] == [str(job_a_id), str(job_b_id), str(job_a_id)]
    # Every field GET /v1/jobs/{job_id}/applications already returns is still present.
    assert set(body[0].keys()) == {
        "application_id", "job_id", "match_result_id", "candidate_id", "status",
        "selected_at", "invite_attempt_number", "invited_at", "interview_status",
        "interview_assessment", "interview_completed_at", "decided_at", "job_title",
        "language_gate", "interview_rounds",
    }


def test_interview_assessment_and_status_pass_through(client, monkeypatch):
    application = _fake_application(
        status="interviewed",
        interview_status="completed",
        interview_assessment={"overall_score": 91, "cefr_level": "C1", "evaluation_status": "completed"},
        interview_completed_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(main, "list_all_applications", lambda db: [(application, "Backend Engineer")])
    monkeypatch.setattr(main, "list_interview_rounds_for_application", lambda db, aid: [])

    test_client, recruiter_id = client
    resp = test_client.get("/v1/applications", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["interview_status"] == "completed"
    assert row["interview_assessment"]["overall_score"] == 91


# ---------------------------------------------------------------------------
# Regression: the existing job-scoped endpoint is unchanged by this task
# ---------------------------------------------------------------------------

def test_job_scoped_applications_endpoint_still_works_and_has_no_job_title(client, monkeypatch):
    job_id = uuid.uuid4()
    application = _fake_application(job_id=job_id, status="selected")

    monkeypatch.setattr(main, "get_job", lambda db, jid: SimpleNamespace(id=jid) if jid == job_id else None)
    monkeypatch.setattr(main, "list_applications_for_job", lambda db, jid: [application] if jid == job_id else [])
    monkeypatch.setattr(main, "list_interview_rounds_for_application", lambda db, aid: [])

    test_client, recruiter_id = client
    resp = test_client.get(f"/v1/jobs/{job_id}/applications", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["application_id"] == str(application.id)
    # ApplicationResponse (not GlobalApplicationResponse) is still the model here --
    # no job_title field, exactly as before this task.
    assert "job_title" not in body[0]
    assert set(body[0].keys()) == {
        "application_id", "job_id", "match_result_id", "candidate_id", "status",
        "selected_at", "invite_attempt_number", "invited_at", "interview_status",
        "interview_assessment", "interview_completed_at", "decided_at", "language_gate",
        "interview_rounds",
    }


def test_job_scoped_applications_unknown_job_still_returns_404(client, monkeypatch):
    monkeypatch.setattr(main, "get_job", lambda db, jid: None)

    test_client, recruiter_id = client
    resp = test_client.get(f"/v1/jobs/{uuid.uuid4()}/applications", headers=_auth_header(recruiter_id))

    assert resp.status_code == 404
