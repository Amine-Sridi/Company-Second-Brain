"""
Integration tests for Recruitment Workflow Service -- JD submission, hybrid scoring,
immutable MatchResult persistence, and scoring config admin.

Run inside the csb-recruitment-workflow container after copying
conftest_recruitment_workflow.py as /app/conftest.py and this file as
/app/test_recruitment_workflow.py:

    docker exec csb-recruitment-workflow python -m pytest /app/test_recruitment_workflow.py -v
"""
import uuid

try:
    from conftest_recruitment_workflow import (
        SEED_RECRUITER_EMAIL,
        SEED_RECRUITER_PASSWORD,
        make_candidate,
    )
except ImportError:
    from conftest import SEED_RECRUITER_EMAIL, SEED_RECRUITER_PASSWORD, make_candidate

_VALID_KEY = "test-service-key"
_JOBS_URL = "/v1/jobs"
_SCORING_CONFIGS_URL = "/v1/scoring-configs"
_LOGIN_URL = "/v1/auth/login"


def _submit_job(client, auth_headers, **overrides) -> dict:
    payload = {
        "title": "Backend Engineer",
        "raw_description": "We need a backend engineer skilled in Python with 2+ years experience.",
        "threshold": 0.5,
        "top_k": 20,
    }
    payload.update(overrides)
    return client.post(_JOBS_URL, json=payload, headers=auth_headers)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "recruitment-workflow"}


# ---------------------------------------------------------------------------
# JD submission -- structuring, persistence, response shape
# ---------------------------------------------------------------------------

def test_submit_job_returns_200_with_structured_jd(client, auth_headers, mock_second_brain):
    mock_second_brain.set_candidates([])
    resp = _submit_job(client, auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Backend Engineer"
    assert body["required_skills"] == ["Python"]
    assert body["preferred_skills"] == ["Docker"]
    assert body["min_years_experience"] == 2.0
    assert body["titles"] == ["Software Engineer"]
    assert body["threshold"] == 0.5
    assert body["scoring_config_version"] == 1
    assert body["matches"] == []


def test_submit_job_persists_job_and_job_description(
    client, auth_headers, db_session, mock_second_brain
):
    mock_second_brain.set_candidates([])
    resp = _submit_job(client, auth_headers, title="Data Scientist")
    job_id = uuid.UUID(resp.json()["job_id"])

    from db import Job, JobDescription

    job = db_session.query(Job).filter(Job.id == job_id).first()
    assert job is not None
    assert job.title == "Data Scientist"

    jd = db_session.query(JobDescription).filter(JobDescription.job_id == job_id).first()
    assert jd is not None
    assert jd.version == 1
    assert jd.required_skills == ["Python"]


def test_submit_job_missing_raw_description_rejected(client, auth_headers):
    resp = client.post(
        _JOBS_URL,
        json={"title": "Backend Engineer"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_submit_job_without_auth_returns_401(client, mock_second_brain):
    mock_second_brain.set_candidates([])
    resp = client.post(_JOBS_URL, json={"title": "x", "raw_description": "y"})
    assert resp.status_code == 401


def test_submit_job_with_service_api_key_only_returns_401(client, mock_second_brain):
    """Recruiter JWT and service API keys are separate mechanisms -- x-api-key alone
    must not satisfy the recruiter-facing JD submission endpoint."""
    mock_second_brain.set_candidates([])
    resp = client.post(
        _JOBS_URL,
        json={"title": "x", "raw_description": "y"},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 401


def test_submit_job_llm_failure_returns_502(client_failing_llm, auth_headers, mock_second_brain):
    resp = client_failing_llm.post(
        _JOBS_URL,
        json={"title": "Backend Engineer", "raw_description": "Needs Python."},
        headers=auth_headers,
    )
    assert resp.status_code == 502
    assert "JD structuring failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Retrieval integration + hybrid scoring
# ---------------------------------------------------------------------------

def test_submit_job_scores_retrieved_candidates(client, auth_headers, mock_second_brain):
    candidate = make_candidate(
        skills=["Python", "Docker"],
        years_of_experience=5.0,
        job_titles=["Software Engineer"],
        similarity_score=0.9,
    )
    mock_second_brain.set_candidates([candidate])

    resp = _submit_job(client, auth_headers)
    assert resp.status_code == 200
    matches = resp.json()["matches"]
    assert len(matches) == 1
    match = matches[0]
    assert match["candidate_id"] == candidate["candidate_id"]
    assert match["semantic_score"] == 0.9
    assert match["skill_score"] == 1.0  # both Python (required) and Docker (preferred) present
    assert match["experience_score"] == 1.0  # 5 >= 2 required
    assert match["hard_gate_passed"] is True
    assert match["qualified"] is True


def test_submit_job_hard_gate_disqualifies_despite_high_semantic_score(
    client, auth_headers, mock_second_brain
):
    candidate = make_candidate(
        skills=["Java"],  # missing required Python
        years_of_experience=10.0,
        similarity_score=0.99,
    )
    mock_second_brain.set_candidates([candidate])

    resp = _submit_job(client, auth_headers, threshold=0.1)
    matches = resp.json()["matches"]
    assert len(matches) == 1
    assert matches[0]["hard_gate_passed"] is False
    assert matches[0]["missing_requirements"] == ["Python"]
    assert matches[0]["qualified"] is False


def test_submit_job_below_threshold_not_qualified(client, auth_headers, mock_second_brain):
    candidate = make_candidate(
        skills=["Python"],
        years_of_experience=0.0,
        job_titles=[],
        similarity_score=0.05,
    )
    mock_second_brain.set_candidates([candidate])

    resp = _submit_job(client, auth_headers, threshold=0.95)
    matches = resp.json()["matches"]
    assert len(matches) == 1
    assert matches[0]["hard_gate_passed"] is True
    assert matches[0]["final_score"] < 0.95
    assert matches[0]["qualified"] is False


def test_submit_job_matches_sorted_by_final_score_desc(client, auth_headers, mock_second_brain):
    strong = make_candidate(skills=["Python", "Docker"], years_of_experience=5.0, similarity_score=0.95)
    weak = make_candidate(skills=["Python"], years_of_experience=2.0, similarity_score=0.3)
    mock_second_brain.set_candidates([weak, strong])

    resp = _submit_job(client, auth_headers, threshold=0.0)
    matches = resp.json()["matches"]
    assert len(matches) == 2
    scores = [m["final_score"] for m in matches]
    assert scores == sorted(scores, reverse=True)
    assert matches[0]["candidate_id"] == strong["candidate_id"]


def test_submit_job_retrieval_failure_returns_502(client, auth_headers, monkeypatch):
    import clients
    from clients import SecondBrainClientError

    async def _failing_retrieve(base_url, api_key, query_text, top_k):
        raise SecondBrainClientError("simulated network failure")

    monkeypatch.setattr(clients, "retrieve_candidates", _failing_retrieve)

    resp = _submit_job(client, auth_headers)
    assert resp.status_code == 502
    assert "Second Brain retrieval failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# MatchResult persistence -- immutable, contains all required components
# ---------------------------------------------------------------------------

def test_match_result_persisted_with_all_components(
    client, auth_headers, db_session, mock_second_brain
):
    candidate = make_candidate(
        skills=["Python", "Docker"], years_of_experience=5.0, similarity_score=0.9
    )
    mock_second_brain.set_candidates([candidate])

    resp = _submit_job(client, auth_headers, threshold=0.4)
    job_id = uuid.UUID(resp.json()["job_id"])

    from db import MatchResult

    row = db_session.query(MatchResult).filter(MatchResult.job_id == job_id).first()
    assert row is not None
    assert row.candidate_id == uuid.UUID(candidate["candidate_id"])
    assert row.candidate_snapshot["skills"] == ["Python", "Docker"]
    assert row.threshold_snapshot == 0.4
    assert row.scoring_config_version == 1
    assert row.embedding_model_version  # non-empty
    assert row.job_description_version == 1
    assert row.matched_requirements == ["Python"]
    assert row.missing_requirements == []
    assert row.qualified is True


# ---------------------------------------------------------------------------
# GET /v1/jobs/{job_id}/matches
# ---------------------------------------------------------------------------

def test_get_job_matches_returns_persisted_matches(client, auth_headers, mock_second_brain):
    candidate = make_candidate(skills=["Python"], similarity_score=0.7)
    mock_second_brain.set_candidates([candidate])

    submit_resp = _submit_job(client, auth_headers)
    job_id = submit_resp.json()["job_id"]

    resp = client.get(f"{_JOBS_URL}/{job_id}/matches", headers=auth_headers)
    assert resp.status_code == 200
    matches = resp.json()
    assert len(matches) == 1
    assert matches[0]["candidate_id"] == candidate["candidate_id"]


def test_get_job_matches_unknown_job_returns_404(client, auth_headers):
    resp = client.get(f"{_JOBS_URL}/{uuid.uuid4()}/matches", headers=auth_headers)
    assert resp.status_code == 404


def test_get_job_matches_without_auth_returns_401(client):
    resp = client.get(f"{_JOBS_URL}/{uuid.uuid4()}/matches")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Scoring config admin -- weight-sum validation
# ---------------------------------------------------------------------------

def test_create_scoring_config_valid_weights(client):
    resp = client.post(
        _SCORING_CONFIGS_URL,
        json={
            "weight_semantic": 0.4,
            "weight_deterministic": 0.6,
            "skill_weight": 0.5,
            "experience_weight": 0.3,
            "title_weight": 0.2,
        },
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["version"] >= 2  # version 1 is the seeded default
    assert body["weight_semantic"] == 0.4


def test_create_scoring_config_hybrid_weights_not_summing_to_one_rejected(client):
    resp = client.post(
        _SCORING_CONFIGS_URL,
        json={
            "weight_semantic": 0.5,
            "weight_deterministic": 0.6,
            "skill_weight": 0.6,
            "experience_weight": 0.2,
            "title_weight": 0.2,
        },
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_create_scoring_config_component_weights_not_summing_to_one_rejected(client):
    resp = client.post(
        _SCORING_CONFIGS_URL,
        json={
            "weight_semantic": 0.5,
            "weight_deterministic": 0.5,
            "skill_weight": 0.5,
            "experience_weight": 0.5,
            "title_weight": 0.5,
        },
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_create_scoring_config_missing_key_returns_401(client):
    resp = client.post(
        _SCORING_CONFIGS_URL,
        json={
            "weight_semantic": 0.5,
            "weight_deterministic": 0.5,
            "skill_weight": 0.6,
            "experience_weight": 0.2,
            "title_weight": 0.2,
        },
    )
    assert resp.status_code == 401


def test_submit_job_can_pin_specific_scoring_config_version(
    client, auth_headers, mock_second_brain
):
    create_resp = client.post(
        _SCORING_CONFIGS_URL,
        json={
            "weight_semantic": 1.0,
            "weight_deterministic": 0.0,
            "skill_weight": 1.0,
            "experience_weight": 0.0,
            "title_weight": 0.0,
        },
        headers={"x-api-key": _VALID_KEY},
    )
    new_version = create_resp.json()["version"]

    mock_second_brain.set_candidates([])
    resp = _submit_job(client, auth_headers, scoring_config_version=new_version)
    assert resp.status_code == 200
    assert resp.json()["scoring_config_version"] == new_version


def test_submit_job_unknown_scoring_config_version_rejected(
    client, auth_headers, mock_second_brain
):
    mock_second_brain.set_candidates([])
    resp = _submit_job(client, auth_headers, scoring_config_version=9999)
    assert resp.status_code == 422


# ===========================================================================
# Recruiter authentication -- POST /v1/auth/login
# ===========================================================================

def test_login_success_returns_token(client):
    resp = client.post(
        _LOGIN_URL,
        json={"email": SEED_RECRUITER_EMAIL, "password": SEED_RECRUITER_PASSWORD},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and len(body["access_token"]) > 0
    assert "expires_at" in body


def test_login_wrong_password_returns_401(client):
    resp = client.post(
        _LOGIN_URL,
        json={"email": SEED_RECRUITER_EMAIL, "password": "wrong-password"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password"


def test_login_unknown_email_returns_401(client):
    resp = client.post(
        _LOGIN_URL,
        json={"email": "nobody@example.com", "password": SEED_RECRUITER_PASSWORD},
    )
    assert resp.status_code == 401


def test_login_never_reveals_password_hash(client):
    resp = client.post(
        _LOGIN_URL,
        json={"email": SEED_RECRUITER_EMAIL, "password": SEED_RECRUITER_PASSWORD},
    )
    assert "password" not in resp.text
    assert "password_hash" not in resp.text


def test_login_rejects_extra_fields(client):
    resp = client.post(
        _LOGIN_URL,
        json={
            "email": SEED_RECRUITER_EMAIL,
            "password": SEED_RECRUITER_PASSWORD,
            "role": "admin",
        },
    )
    assert resp.status_code == 422


# ===========================================================================
# JWT validation on protected endpoints
# ===========================================================================

def test_protected_endpoint_rejects_expired_token(client, db_session):
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone
    from db import get_recruiter_by_email
    from main import settings

    recruiter = get_recruiter_by_email(db_session, SEED_RECRUITER_EMAIL)
    now = datetime.now(timezone.utc)
    expired_payload = {
        "sub": str(recruiter.id),
        "email": recruiter.email,
        "iat": now - timedelta(minutes=60),
        "exp": now - timedelta(minutes=1),
    }
    expired_token = pyjwt.encode(expired_payload, settings.jwt_secret, algorithm="HS256")

    resp = client.get(
        f"{_JOBS_URL}/{uuid.uuid4()}/matches",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp.status_code == 401


def test_protected_endpoint_rejects_token_with_wrong_signature(client):
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone

    bad_token = pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "email": "attacker@example.com",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=45),
        },
        "not-the-real-secret",
        algorithm="HS256",
    )
    resp = client.get(
        f"{_JOBS_URL}/{uuid.uuid4()}/matches",
        headers={"Authorization": f"Bearer {bad_token}"},
    )
    assert resp.status_code == 401


def test_protected_endpoint_rejects_malformed_token(client):
    resp = client.get(
        f"{_JOBS_URL}/{uuid.uuid4()}/matches",
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert resp.status_code == 401


def test_protected_endpoint_without_authorization_header_returns_401(client):
    resp = client.get(f"{_JOBS_URL}/{uuid.uuid4()}/matches")
    assert resp.status_code == 401


def test_protected_endpoint_with_valid_jwt_succeeds(client, auth_headers):
    """Valid recruiter authorization: a well-formed, unexpired, correctly-signed token
    for the seeded recruiter is accepted."""
    resp = client.get(f"{_JOBS_URL}/{uuid.uuid4()}/matches", headers=auth_headers)
    assert resp.status_code == 404  # job doesn't exist, but auth itself succeeded


def test_scoring_configs_endpoint_rejects_jwt_in_place_of_service_key(client, auth_headers):
    """The reverse of test_submit_job_with_service_api_key_only_returns_401: a valid
    recruiter JWT must not satisfy a service-to-service endpoint's x-api-key check."""
    resp = client.post(
        _SCORING_CONFIGS_URL,
        json={
            "weight_semantic": 0.5,
            "weight_deterministic": 0.5,
            "skill_weight": 0.6,
            "experience_weight": 0.2,
            "title_weight": 0.2,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 401


# ===========================================================================
# Candidate selection + recruiter decisions (Application state machine)
# ===========================================================================

def _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain, **overrides):
    """Submits a job with one candidate that qualifies (Python skill, low threshold) and
    returns (job_id, match_id)."""
    candidate = make_candidate(skills=["Python"], years_of_experience=5.0, similarity_score=0.9)
    mock_second_brain.set_candidates([candidate])
    resp = _submit_job(client, auth_headers, threshold=0.1, **overrides)
    body = resp.json()
    return body["job_id"], body["matches"][0]["match_id"]


def test_select_qualified_match_creates_application(client, auth_headers, mock_second_brain):
    job_id, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)

    resp = client.post(f"/v1/matches/{match_id}/select", headers=auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["job_id"] == job_id
    assert body["match_result_id"] == match_id
    assert body["status"] == "selected"
    assert body["decided_at"] is None


def test_select_requires_jwt_not_service_key(client, auth_headers, mock_second_brain):
    _, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)
    resp = client.post(
        f"/v1/matches/{match_id}/select", headers={"x-api-key": _VALID_KEY}
    )
    assert resp.status_code == 401


def test_select_disqualified_match_rejected(client, auth_headers, mock_second_brain):
    candidate = make_candidate(skills=["Java"], similarity_score=0.9)  # missing Python
    mock_second_brain.set_candidates([candidate])
    resp = _submit_job(client, auth_headers, threshold=0.1)
    match_id = resp.json()["matches"][0]["match_id"]
    assert resp.json()["matches"][0]["qualified"] is False

    select_resp = client.post(f"/v1/matches/{match_id}/select", headers=auth_headers)
    assert select_resp.status_code == 422


def test_select_unknown_match_returns_404(client, auth_headers):
    resp = client.post(f"/v1/matches/{uuid.uuid4()}/select", headers=auth_headers)
    assert resp.status_code == 404


def test_select_same_match_twice_returns_409(client, auth_headers, mock_second_brain):
    """Duplicate selection: the second attempt to select the same candidate for the same
    job is rejected via the database's unique constraint, not a check-then-act race."""
    _, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)

    first = client.post(f"/v1/matches/{match_id}/select", headers=auth_headers)
    assert first.status_code == 201

    second = client.post(f"/v1/matches/{match_id}/select", headers=auth_headers)
    assert second.status_code == 409


def test_accept_application_persists_decision(client, auth_headers, db_session, mock_second_brain):
    _, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)
    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]

    resp = client.post(f"/v1/applications/{application_id}/accept", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["decided_at"] is not None

    from db import RecruiterDecision

    decision = (
        db_session.query(RecruiterDecision)
        .filter(RecruiterDecision.application_id == uuid.UUID(application_id))
        .first()
    )
    assert decision is not None
    assert decision.decision == "accept"
    assert decision.previous_status == "selected"
    assert decision.new_status == "accepted"
    assert decision.recruiter_id is not None


def test_reject_application_persists_decision(client, auth_headers, db_session, mock_second_brain):
    _, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)
    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]

    resp = client.post(f"/v1/applications/{application_id}/reject", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"

    from db import RecruiterDecision

    decision = (
        db_session.query(RecruiterDecision)
        .filter(RecruiterDecision.application_id == uuid.UUID(application_id))
        .first()
    )
    assert decision.decision == "reject"
    assert decision.new_status == "rejected"


def test_decide_unknown_application_returns_404(client, auth_headers):
    resp = client.post(f"/v1/applications/{uuid.uuid4()}/accept", headers=auth_headers)
    assert resp.status_code == 404


def test_decide_requires_jwt_not_service_key(client, auth_headers, mock_second_brain):
    _, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)
    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]

    resp = client.post(
        f"/v1/applications/{application_id}/accept", headers={"x-api-key": _VALID_KEY}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Invalid state transitions + concurrent/duplicate transition behavior
#
# transition_application() performs a single conditional UPDATE ... WHERE status =
# 'selected'. Calling accept/reject twice in a row exercises exactly the same code path
# a true concurrent race would hit: only one request can ever see status = 'selected' at
# UPDATE time, so the guarantee these tests check does not depend on request timing.
# ---------------------------------------------------------------------------

def test_accept_twice_second_call_returns_409(client, auth_headers, mock_second_brain):
    _, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)
    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]

    first = client.post(f"/v1/applications/{application_id}/accept", headers=auth_headers)
    assert first.status_code == 200

    second = client.post(f"/v1/applications/{application_id}/accept", headers=auth_headers)
    assert second.status_code == 409
    assert "accepted" in second.json()["detail"]


def test_reject_after_accept_returns_409(client, auth_headers, mock_second_brain):
    _, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)
    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]

    client.post(f"/v1/applications/{application_id}/accept", headers=auth_headers)
    resp = client.post(f"/v1/applications/{application_id}/reject", headers=auth_headers)
    assert resp.status_code == 409


def test_accept_after_reject_returns_409(client, auth_headers, mock_second_brain):
    _, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)
    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]

    client.post(f"/v1/applications/{application_id}/reject", headers=auth_headers)
    resp = client.post(f"/v1/applications/{application_id}/accept", headers=auth_headers)
    assert resp.status_code == 409


def test_reject_twice_second_call_returns_409(client, auth_headers, mock_second_brain):
    _, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)
    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]

    client.post(f"/v1/applications/{application_id}/reject", headers=auth_headers)
    resp = client.post(f"/v1/applications/{application_id}/reject", headers=auth_headers)
    assert resp.status_code == 409


def test_only_one_decision_persisted_after_duplicate_accept_calls(
    client, auth_headers, db_session, mock_second_brain
):
    """Even though two accept requests were made, only the first transition is recorded --
    the audit log must not contain a decision for the rejected duplicate call."""
    _, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)
    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]

    client.post(f"/v1/applications/{application_id}/accept", headers=auth_headers)
    client.post(f"/v1/applications/{application_id}/accept", headers=auth_headers)

    from db import RecruiterDecision

    decisions = (
        db_session.query(RecruiterDecision)
        .filter(RecruiterDecision.application_id == uuid.UUID(application_id))
        .all()
    )
    assert len(decisions) == 1


# ---------------------------------------------------------------------------
# GET /v1/jobs/{job_id}/applications
# ---------------------------------------------------------------------------

def test_get_job_applications_lists_selections(client, auth_headers, mock_second_brain):
    job_id, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)
    client.post(f"/v1/matches/{match_id}/select", headers=auth_headers)

    resp = client.get(f"{_JOBS_URL}/{job_id}/applications", headers=auth_headers)
    assert resp.status_code == 200
    applications = resp.json()
    assert len(applications) == 1
    assert applications[0]["match_result_id"] == match_id
    assert applications[0]["status"] == "selected"


def test_get_job_applications_without_auth_returns_401(client):
    resp = client.get(f"{_JOBS_URL}/{uuid.uuid4()}/applications")
    assert resp.status_code == 401
