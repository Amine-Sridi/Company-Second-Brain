"""
Integration tests for the post-VoiceHR interview-rounds stage -- real Postgres via
db_session (see conftest_recruitment_workflow.py), the one thing the RWS-local
monkeypatched suite (services/recruitment-workflow/tests/test_interview_rounds.py)
cannot cover: that recruitment.interview_rounds' CHECK constraints are actually
enforced by the database, and that a real create_interview_round() call persists and
round-trips correctly end to end (real select -> invite -> webhook -> create round).

Run inside the csb-recruitment-workflow container after copying
conftest_recruitment_workflow.py as /app/conftest.py and this file as
/app/test_interview_rounds_integration.py:

    docker exec -e TEST_DATABASE_URL=... csb-recruitment-workflow \
        python -m pytest /app/test_interview_rounds_integration.py -v
(see db_safety.py -- this suite refuses to run without a database name that
unmistakably marks itself as disposable, never the live demo database.)
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

try:
    from conftest_recruitment_workflow import SEED_RECRUITER_EMAIL, make_candidate, make_webhook_payload, select_and_invite
except ImportError:
    from conftest import SEED_RECRUITER_EMAIL, make_candidate, make_webhook_payload, select_and_invite

_WEBHOOK_URL = "/v1/webhooks/voicehr"


def _interviewed_application(client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr):
    """Real select -> invite -> completed-webhook, leaving the application
    'interviewed' with a passing (no language requirement, so not_required)
    gate -- the common starting point for every test below."""
    _, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    body, headers = make_webhook_payload(application_id)
    resp = client.post(_WEBHOOK_URL, content=body, headers=headers)
    assert resp.status_code == 200
    return application_id


# ---------------------------------------------------------------------------
# End-to-end: real create_interview_round(), embedded on real read paths
# ---------------------------------------------------------------------------

def test_create_interview_round_persists_and_is_embedded_on_read(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr, db_session
):
    application_id = _interviewed_application(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )

    resp = client.post(
        f"/v1/applications/{application_id}/interview-rounds",
        json={
            "round_type": "technical",
            "interviewer_name": "Jane Doe",
            "scheduled_at": "2026-10-01T10:00:00Z",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "scheduled"
    assert body["outcome"] is None
    assert body["round_type"] == "technical"

    from db import Application, InterviewRound

    row = db_session.query(InterviewRound).filter(InterviewRound.id == uuid.UUID(body["id"])).first()
    assert row is not None
    assert row.interviewer_name == "Jane Doe"
    assert str(row.application_id) == application_id

    application = db_session.query(Application).filter(Application.id == uuid.UUID(application_id)).first()

    # Embedded on the existing application read paths, not just the create response --
    # both the global list and the job-scoped list, the same two paths the RWS-local
    # monkeypatched suite exercises with fakes.
    global_apps = client.get("/v1/applications", headers=auth_headers)
    assert global_apps.status_code == 200
    matching = [a for a in global_apps.json() if a["application_id"] == application_id]
    assert len(matching) == 1
    assert len(matching[0]["interview_rounds"]) == 1
    assert matching[0]["interview_rounds"][0]["id"] == body["id"]

    job_apps = client.get(f"/v1/jobs/{application.job_id}/applications", headers=auth_headers)
    assert job_apps.status_code == 200
    matching_job_scoped = [a for a in job_apps.json() if a["application_id"] == application_id]
    assert len(matching_job_scoped) == 1
    assert len(matching_job_scoped[0]["interview_rounds"]) == 1


# ---------------------------------------------------------------------------
# Gating: blocked while the language gate is pending, blocked after a decision
# ---------------------------------------------------------------------------

def test_create_blocked_while_language_gate_pending_via_real_db(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    """Real select+invite with a job that has a language requirement, no webhook
    delivered yet -- language_gate.status is 'pending' from real DB state, not a
    monkeypatched fake."""
    candidate = make_candidate(skills=["Python"], similarity_score=0.9)
    mock_second_brain.set_candidates([candidate])
    mock_resume_intelligence.set_contact(
        candidate["candidate_id"],
        {"candidate_id": candidate["candidate_id"], "name": "Test Candidate", "email": "candidate@example.com", "phone": None},
    )
    submit_resp = client.post(
        "/v1/jobs",
        json={
            "title": "Backend Engineer", "raw_description": "Backend engineer role.",
            "threshold": 0.1, "top_k": 20, "required_language": "en", "min_language_cefr": "B2",
        },
        headers=auth_headers,
    )
    match_id = submit_resp.json()["matches"][0]["match_id"]
    application_id = client.post(f"/v1/matches/{match_id}/select", headers=auth_headers).json()["application_id"]

    resp = client.post(
        f"/v1/applications/{application_id}/interview-rounds",
        json={"round_type": "technical", "interviewer_name": "Jane Doe", "scheduled_at": "2026-10-01T10:00:00Z"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert "pending" in resp.json()["detail"]


def test_create_blocked_after_real_accept_via_real_db(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    application_id = _interviewed_application(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    accept_resp = client.post(f"/v1/applications/{application_id}/accept", headers=auth_headers)
    assert accept_resp.status_code == 200

    resp = client.post(
        f"/v1/applications/{application_id}/interview-rounds",
        json={"round_type": "technical", "interviewer_name": "Jane Doe", "scheduled_at": "2026-10-01T10:00:00Z"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert "accepted" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# DB CHECK constraints -- enforced by Postgres itself, independent of any
# application-code validation (defense in depth for the lifecycle actions the
# next task, reschedule/complete/cancel, will add).
# ---------------------------------------------------------------------------

def _seed_application_and_recruiter(db_session):
    """Raw-SQL-friendly (application_id, recruiter_id) pair that satisfies
    interview_rounds' foreign keys, without going through the full select/invite
    HTTP flow -- these tests only care about the CHECK constraints, not the
    application state machine."""
    from db import get_recruiter_by_email

    recruiter = get_recruiter_by_email(db_session, SEED_RECRUITER_EMAIL)
    assert recruiter is not None

    job_id = uuid.uuid4()
    db_session.execute(
        text(
            "INSERT INTO recruitment.jobs (id, title, threshold) "
            "VALUES (:id, 'Raw SQL Test Job', 0.5)"
        ),
        {"id": job_id},
    )
    match_id = uuid.uuid4()
    db_session.execute(
        text(
            "INSERT INTO recruitment.job_descriptions "
            "(id, job_id, version, raw_text, required_skills, preferred_skills, titles, structuring_model) "
            "VALUES (:jd_id, :job_id, 1, 'raw', '[]', '[]', '[]', 'test-model')"
        ),
        {"jd_id": uuid.uuid4(), "job_id": job_id},
    )
    scoring_config_id = db_session.execute(
        text("SELECT id FROM recruitment.scoring_configs ORDER BY version DESC LIMIT 1")
    ).scalar_one()
    candidate_id = uuid.uuid4()
    db_session.execute(
        text(
            "INSERT INTO recruitment.match_results "
            "(id, job_id, job_description_id, job_description_version, scoring_config_id, "
            "scoring_config_version, embedding_model_version, candidate_id, candidate_snapshot, "
            "threshold_snapshot, semantic_score, skill_score, experience_score, title_score, "
            "deterministic_score, final_score, hard_gate_passed, matched_requirements, "
            "missing_requirements, qualified) "
            "VALUES (:id, :job_id, (SELECT id FROM recruitment.job_descriptions WHERE job_id = :job_id), 1, "
            ":scoring_config_id, 1, 'test-embedding', :candidate_id, '{}', 0.5, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, "
            "true, '[]', '[]', true)"
        ),
        {"id": match_id, "job_id": job_id, "scoring_config_id": scoring_config_id, "candidate_id": candidate_id},
    )
    application_id = uuid.uuid4()
    db_session.execute(
        text(
            "INSERT INTO recruitment.applications "
            "(id, job_id, match_result_id, candidate_id, status, selected_by) "
            "VALUES (:id, :job_id, :match_id, :candidate_id, 'interviewed', :recruiter_id)"
        ),
        {"id": application_id, "job_id": job_id, "match_id": match_id, "candidate_id": candidate_id, "recruiter_id": recruiter.id},
    )
    db_session.flush()
    return application_id, recruiter.id


def _insert_round(db_session, application_id, recruiter_id, *, round_type="technical", status="scheduled", outcome=None):
    db_session.execute(
        text(
            "INSERT INTO recruitment.interview_rounds "
            "(application_id, round_type, status, outcome, interviewer_name, created_by) "
            "VALUES (:application_id, :round_type, :status, :outcome, 'Jane Doe', :created_by)"
        ),
        {
            "application_id": application_id, "round_type": round_type, "status": status,
            "outcome": outcome, "created_by": recruiter_id,
        },
    )
    db_session.flush()


def test_check_constraint_rejects_invalid_round_type(db_session):
    application_id, recruiter_id = _seed_application_and_recruiter(db_session)
    with pytest.raises(IntegrityError):
        _insert_round(db_session, application_id, recruiter_id, round_type="onsite")
    db_session.rollback()


def test_check_constraint_rejects_invalid_status(db_session):
    application_id, recruiter_id = _seed_application_and_recruiter(db_session)
    with pytest.raises(IntegrityError):
        _insert_round(db_session, application_id, recruiter_id, status="in_progress")
    db_session.rollback()


def test_check_constraint_rejects_invalid_outcome(db_session):
    application_id, recruiter_id = _seed_application_and_recruiter(db_session)
    with pytest.raises(IntegrityError):
        _insert_round(db_session, application_id, recruiter_id, status="completed", outcome="maybe")
    db_session.rollback()


def test_check_constraint_rejects_outcome_set_while_still_scheduled(db_session):
    """The paired outcome/status CHECK -- a 'scheduled' round must never carry a
    pass/fail/needs_review verdict."""
    application_id, recruiter_id = _seed_application_and_recruiter(db_session)
    with pytest.raises(IntegrityError):
        _insert_round(db_session, application_id, recruiter_id, status="scheduled", outcome="pass")
    db_session.rollback()


def test_check_constraint_rejects_completed_without_outcome(db_session):
    """The other half of the same paired CHECK -- a 'completed' round must always
    carry a verdict."""
    application_id, recruiter_id = _seed_application_and_recruiter(db_session)
    with pytest.raises(IntegrityError):
        _insert_round(db_session, application_id, recruiter_id, status="completed", outcome=None)
    db_session.rollback()


def test_check_constraint_allows_valid_scheduled_and_completed_rows(db_session):
    application_id, recruiter_id = _seed_application_and_recruiter(db_session)
    _insert_round(db_session, application_id, recruiter_id, status="scheduled", outcome=None)
    _insert_round(db_session, application_id, recruiter_id, round_type="managerial", status="completed", outcome="needs_review")

    from db import InterviewRound

    rows = db_session.query(InterviewRound).filter(InterviewRound.application_id == application_id).all()
    assert len(rows) == 2
    statuses = {r.status for r in rows}
    assert statuses == {"scheduled", "completed"}


# ---------------------------------------------------------------------------
# Lifecycle actions -- reschedule / complete / cancel, through the real API,
# against real Postgres (including migration 0007's CHECK constraints, which a
# monkeypatched test can only assume are satisfied, never actually prove).
# ---------------------------------------------------------------------------

def _create_round_via_api(client, auth_headers, application_id, **overrides):
    body = {
        "round_type": "technical",
        "interviewer_name": "Jane Doe",
        "scheduled_at": "2026-10-01T10:00:00Z",
    }
    body.update(overrides)
    resp = client.post(
        f"/v1/applications/{application_id}/interview-rounds", json=body, headers=auth_headers
    )
    assert resp.status_code == 201
    return resp.json()


def test_reschedule_persists_new_scheduled_at(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    application_id = _interviewed_application(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    round_ = _create_round_via_api(client, auth_headers, application_id)

    resp = client.post(
        f"/v1/applications/{application_id}/interview-rounds/{round_['id']}/reschedule",
        json={"scheduled_at": "2026-11-15T14:00:00Z"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["scheduled_at"] == "2026-11-15T14:00:00Z"
    assert resp.json()["status"] == "scheduled"


def test_complete_persists_outcome_and_notes_real_db(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr, db_session
):
    """The success path itself is proof the paired outcome/status CHECK constraint
    (migration 0007) is satisfied by complete_interview_round's real UPDATE --
    if it weren't, this would fail with a 500 (IntegrityError), not return 200."""
    application_id = _interviewed_application(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    round_ = _create_round_via_api(client, auth_headers, application_id)

    resp = client.post(
        f"/v1/applications/{application_id}/interview-rounds/{round_['id']}/complete",
        json={"outcome": "pass", "notes": "Strong system design answers."},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["outcome"] == "pass"
    assert body["notes"] == "Strong system design answers."

    from db import InterviewRound

    row = db_session.query(InterviewRound).filter(InterviewRound.id == uuid.UUID(round_["id"])).first()
    assert row.status == "completed"
    assert row.outcome == "pass"


def test_complete_without_notes_preserves_notes_set_at_scheduling(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    application_id = _interviewed_application(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    round_ = _create_round_via_api(client, auth_headers, application_id, notes="Prep: focus on system design.")

    resp = client.post(
        f"/v1/applications/{application_id}/interview-rounds/{round_['id']}/complete",
        json={"outcome": "fail"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["notes"] == "Prep: focus on system design."


def test_reschedule_blocked_after_real_complete(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    application_id = _interviewed_application(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    round_ = _create_round_via_api(client, auth_headers, application_id)
    client.post(
        f"/v1/applications/{application_id}/interview-rounds/{round_['id']}/complete",
        json={"outcome": "pass"},
        headers=auth_headers,
    )

    resp = client.post(
        f"/v1/applications/{application_id}/interview-rounds/{round_['id']}/reschedule",
        json={"scheduled_at": "2026-12-01T09:00:00Z"},
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert "completed" in resp.json()["detail"]


def test_cancel_persists_status_real_db(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr, db_session
):
    application_id = _interviewed_application(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    round_ = _create_round_via_api(client, auth_headers, application_id)

    resp = client.post(
        f"/v1/applications/{application_id}/interview-rounds/{round_['id']}/cancel",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert resp.json()["outcome"] is None

    from db import InterviewRound

    row = db_session.query(InterviewRound).filter(InterviewRound.id == uuid.UUID(round_["id"])).first()
    assert row.status == "cancelled"


def test_cancel_blocked_after_real_cancel(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    application_id = _interviewed_application(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    round_ = _create_round_via_api(client, auth_headers, application_id)
    client.post(
        f"/v1/applications/{application_id}/interview-rounds/{round_['id']}/cancel", headers=auth_headers
    )

    resp = client.post(
        f"/v1/applications/{application_id}/interview-rounds/{round_['id']}/cancel", headers=auth_headers
    )
    assert resp.status_code == 409
    assert "cancelled" in resp.json()["detail"]


def test_lifecycle_actions_404_when_round_belongs_to_a_different_real_application(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    application_a = _interviewed_application(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    application_b = _interviewed_application(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    round_on_a = _create_round_via_api(client, auth_headers, application_a)

    resp = client.post(
        f"/v1/applications/{application_b}/interview-rounds/{round_on_a['id']}/cancel",
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_lifecycle_actions_404_for_unknown_round(client, auth_headers):
    fake_round_id = str(uuid.uuid4())
    fake_application_id = str(uuid.uuid4())

    reschedule_resp = client.post(
        f"/v1/applications/{fake_application_id}/interview-rounds/{fake_round_id}/reschedule",
        json={"scheduled_at": "2026-11-01T09:00:00Z"},
        headers=auth_headers,
    )
    complete_resp = client.post(
        f"/v1/applications/{fake_application_id}/interview-rounds/{fake_round_id}/complete",
        json={"outcome": "pass"},
        headers=auth_headers,
    )
    cancel_resp = client.post(
        f"/v1/applications/{fake_application_id}/interview-rounds/{fake_round_id}/cancel",
        headers=auth_headers,
    )

    assert reschedule_resp.status_code == 404
    assert complete_resp.status_code == 404
    assert cancel_resp.status_code == 404
