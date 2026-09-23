"""
Integration tests for Recruitment Workflow Service -- JD submission, hybrid scoring,
immutable MatchResult persistence, and scoring config admin.

Run inside the csb-recruitment-workflow container after copying
conftest_recruitment_workflow.py as /app/conftest.py and this file as
/app/test_recruitment_workflow.py:

    docker exec csb-recruitment-workflow python -m pytest /app/test_recruitment_workflow.py -v
"""
import uuid

import pytest

try:
    from conftest_recruitment_workflow import (
        SEED_RECRUITER_EMAIL,
        SEED_RECRUITER_PASSWORD,
        make_candidate,
        make_webhook_payload,
        select_and_invite,
        sign_webhook_body,
    )
except ImportError:
    from conftest import (
        SEED_RECRUITER_EMAIL,
        SEED_RECRUITER_PASSWORD,
        make_candidate,
        make_webhook_payload,
        select_and_invite,
        sign_webhook_body,
    )

_VALID_KEY = "test-service-key"
_JOBS_URL = "/v1/jobs"
_SCORING_CONFIGS_URL = "/v1/scoring-configs"
_LOGIN_URL = "/v1/auth/login"
_WEBHOOK_URL = "/v1/webhooks/voicehr"


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
# Observability: correlation ID propagation + no secrets/PII/interview content in logs
# ---------------------------------------------------------------------------

def test_correlation_id_generated_when_absent(client):
    resp = client.get("/health")
    assert "x-correlation-id" in {k.lower() for k in resp.headers.keys()}
    assert len(resp.headers["x-correlation-id"]) > 0


def test_correlation_id_echoed_when_provided(client):
    resp = client.get("/health", headers={"X-Correlation-ID": "caller-supplied-id-789"})
    assert resp.headers["x-correlation-id"] == "caller-supplied-id-789"


def test_correlation_id_differs_across_requests(client):
    first = client.get("/health").headers["x-correlation-id"]
    second = client.get("/health").headers["x-correlation-id"]
    assert first != second


def test_login_never_logs_password(client, caplog):
    import logging

    secret_password = "UNIQUE_MARKER_password_should_never_appear_in_logs"
    with caplog.at_level(logging.DEBUG):
        client.post(_LOGIN_URL, json={"email": SEED_RECRUITER_EMAIL, "password": secret_password})
    assert secret_password not in caplog.text


def test_login_never_logs_issued_access_token(client, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        resp = client.post(
            _LOGIN_URL, json={"email": SEED_RECRUITER_EMAIL, "password": SEED_RECRUITER_PASSWORD}
        )
    token = resp.json()["access_token"]
    assert len(token) > 20  # sanity: a real JWT was actually issued
    assert token not in caplog.text


def test_service_api_key_never_logged(client, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        client.post(
            _SCORING_CONFIGS_URL,
            json={
                "weight_semantic": 0.5, "weight_deterministic": 0.5,
                "skill_weight": 0.6, "experience_weight": 0.2, "title_weight": 0.2,
            },
            headers={"x-api-key": _VALID_KEY},
        )
    assert _VALID_KEY not in caplog.text


def test_webhook_never_logs_assessment_content(
    client, auth_headers, db_session, mock_second_brain, mock_resume_intelligence, mock_voicehr, caplog
):
    """The webhook handler's own 'webhook_received' log line must carry only
    identifiers (delivery_id, interview_status, application_id) -- never the
    assessment content (scores, CEFR level) it receives, which is treated as
    interview content."""
    import logging

    _, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    marker_cefr_level = "MRKR_LEVEL"  # fits InterviewAssessment's max_length=10
    body, headers = make_webhook_payload(application_id, cefr_level=marker_cefr_level)

    with caplog.at_level(logging.DEBUG):
        resp = client.post(_WEBHOOK_URL, content=body, headers=headers)
    assert resp.status_code == 200

    assert marker_cefr_level not in caplog.text


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
# JD extraction validation (jd_validation.py) -- flagged extractions still work
# ---------------------------------------------------------------------------

def test_submit_job_clean_extraction_reports_clean_status(client, auth_headers, mock_second_brain, mock_llm):
    # mock_llm's default structured JD (Python/Docker/2yrs/Software Engineer) is
    # well-formed and must not be flagged.
    mock_second_brain.set_candidates([])
    resp = _submit_job(client, auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["extraction_status"] == "clean"
    assert body["extraction_warnings"] == []
    # A clean first extraction must never trigger the repair pass -- one Gemini
    # call total, not two.
    assert len(mock_llm.generate_structured_calls) == 1


def test_submit_job_flagged_extraction_still_persists_and_matches_normally(
    client, auth_headers, db_session, mock_second_brain, mock_llm
):
    # A deliberately bad extraction (total under-extraction -- everything empty)
    # must still be saved and matched automatically, not blocked pending review.
    # mock_llm has no repair_structured configured, so the repair attempt's own
    # call returns this same empty structured back -- still flagged after
    # revalidation, so this also covers "the repair call itself doesn't fix
    # anything -> falls back to flagged".
    mock_llm._structured = {
        "required_skills": [],
        "preferred_skills": [],
        "min_years_experience": None,
        "titles": [],
    }
    candidate = make_candidate(skills=["Python", "Docker"], years_of_experience=4.0, similarity_score=0.9)
    mock_second_brain.set_candidates([candidate])

    resp = _submit_job(client, auth_headers, title="Flagged Job")
    assert resp.status_code == 200
    body = resp.json()

    assert body["extraction_status"] == "flagged"
    assert len(body["extraction_warnings"]) == 1
    assert body["extraction_warnings"][0]["field"] == "extraction"
    # Job creation is not blocked and matching still runs against retrieved candidates.
    assert len(body["matches"]) == 1
    # A flagged first extraction must trigger exactly one repair attempt -- the
    # initial structuring call plus one repair call, never a retry loop.
    assert len(mock_llm.generate_structured_calls) == 2

    from db import JobDescription

    job_id = uuid.UUID(body["job_id"])
    jd = db_session.query(JobDescription).filter(JobDescription.job_id == job_id).first()
    assert jd is not None
    assert jd.extraction_status == "flagged"
    assert jd.extraction_warnings == body["extraction_warnings"]


def test_submit_job_repair_pass_fixes_a_flagged_extraction(
    client, auth_headers, db_session, mock_second_brain, mock_llm
):
    # First extraction: a phrase-like required skill (flagged). Repair attempt
    # returns a genuinely fixed extraction that passes deterministic validation
    # cleanly -- final status must be "repaired", not "clean" (a repair did
    # happen) and not "flagged" (the repair actually worked).
    mock_llm._structured = {
        "required_skills": ["building and deploying REST APIs"],
        "preferred_skills": [],
        "min_years_experience": 2.0,
        "titles": ["Backend Engineer"],
    }
    mock_llm._repair_structured = {
        "required_skills": ["REST APIs"],
        "preferred_skills": [],
        "min_years_experience": 2.0,
        "titles": ["Backend Engineer"],
    }
    candidate = make_candidate(skills=["REST APIs"], years_of_experience=3.0, similarity_score=0.9)
    mock_second_brain.set_candidates([candidate])

    resp = _submit_job(client, auth_headers, title="Repaired Job")
    assert resp.status_code == 200
    body = resp.json()

    assert body["extraction_status"] == "repaired"
    assert body["extraction_warnings"] == []
    # The repaired (not the original phrase-like) requirements are what's
    # actually persisted and matched against.
    assert body["required_skills"] == ["REST APIs"]
    assert len(mock_llm.generate_structured_calls) == 2
    # Scoring/matching continues normally against the repaired requirements: the
    # candidate has the exact repaired required skill, so the hard gate passes
    # and a real MatchResult is produced.
    assert len(body["matches"]) == 1
    assert body["matches"][0]["hard_gate_passed"] is True

    from db import JobDescription

    job_id = uuid.UUID(body["job_id"])
    jd = db_session.query(JobDescription).filter(JobDescription.job_id == job_id).first()
    assert jd is not None
    assert jd.extraction_status == "repaired"
    assert jd.required_skills == ["REST APIs"]
    assert jd.extraction_warnings == []


def test_submit_job_repair_pass_still_invalid_falls_back_to_original_flagged(
    client, auth_headers, mock_second_brain, mock_llm
):
    # First extraction: a phrase-like required skill (flagged). The repair
    # attempt returns a *different* phrase-like skill -- still fails validation,
    # so the original (pre-repair) normalized extraction must be what's actually
    # persisted, not the failed repair attempt's own output.
    mock_llm._structured = {
        "required_skills": ["building and deploying REST APIs"],
        "preferred_skills": ["Docker"],
        "min_years_experience": 2.0,
        "titles": ["Backend Engineer"],
    }
    mock_llm._repair_structured = {
        "required_skills": ["designing and maintaining backend systems"],
        "preferred_skills": ["Docker"],
        "min_years_experience": 2.0,
        "titles": ["Backend Engineer"],
    }
    mock_second_brain.set_candidates([])

    resp = _submit_job(client, auth_headers, title="Still Flagged After Repair")
    assert resp.status_code == 200
    body = resp.json()

    assert body["extraction_status"] == "flagged"
    # The original extraction is what's persisted, not the failed repair attempt.
    assert body["required_skills"] == ["building and deploying REST APIs"]
    assert any(w["field"] == "required_skills" for w in body["extraction_warnings"])
    assert len(mock_llm.generate_structured_calls) == 2


def test_submit_job_repair_pass_failure_does_not_block_job_creation(
    client_failing_repair, auth_headers, db_session, mock_second_brain
):
    # The repair call itself fails (LLMProviderError -- unreachable/quota/etc);
    # this must never block job creation. The default _FailingRepairLLMProvider
    # structured extraction is empty (total under-extraction), so the job is
    # created with the original, pre-repair "flagged" extraction persisted.
    candidate = make_candidate(skills=["Python"], years_of_experience=3.0, similarity_score=0.9)
    mock_second_brain.set_candidates([candidate])

    resp = _submit_job(client_failing_repair, auth_headers, title="Repair Call Failed")
    assert resp.status_code == 200
    body = resp.json()

    assert body["extraction_status"] == "flagged"
    assert len(body["extraction_warnings"]) == 1
    # Matching still ran normally despite the repair call failing.
    assert len(body["matches"]) == 1

    # Exactly one repair attempt was made (and it failed) -- no retry loop.
    assert len(client_failing_repair.failing_llm.generate_structured_calls) == 2

    from db import JobDescription

    job_id = uuid.UUID(body["job_id"])
    jd = db_session.query(JobDescription).filter(JobDescription.job_id == job_id).first()
    assert jd is not None
    assert jd.extraction_status == "flagged"


def test_submit_job_phrase_like_required_skill_flags_but_still_creates_job(
    client, auth_headers, db_session, mock_second_brain, mock_llm
):
    # A phrase-like required skill can never exact-match any candidate under the
    # hard gate -- this must be flagged for recruiter visibility, not silently
    # accepted or rejected outright.
    mock_llm._structured = {
        "required_skills": ["building and deploying REST APIs"],
        "preferred_skills": [],
        "min_years_experience": 2.0,
        "titles": ["Backend Engineer"],
    }
    mock_second_brain.set_candidates([])

    resp = _submit_job(client, auth_headers, title="Phrase-like Job")
    assert resp.status_code == 200
    body = resp.json()

    assert body["extraction_status"] == "flagged"
    assert body["required_skills"] == ["building and deploying REST APIs"]
    assert any(w["field"] == "required_skills" for w in body["extraction_warnings"])


# ---------------------------------------------------------------------------
# Post-match JD anomaly detection (match_anomaly.py) -- observational only,
# never rescoring, never touching MatchResult, never calling Gemini
# ---------------------------------------------------------------------------

def test_submit_job_match_anomaly_flags_a_clean_extraction_when_everyone_fails_the_hard_gate(
    client, auth_headers, db_session, mock_second_brain, mock_llm
):
    # "Quantum Flux Optimization" is short and skill-name-shaped enough that
    # jd_validation.py's own phrase-like check never flags it (this stays "clean"
    # at extraction time) -- but it's a Gemini-hallucinated skill no real
    # candidate will ever have. 5 candidates, all genuinely relevant (semantic
    # 0.7) but none with that exact skill -- the real anomaly this feature exists
    # to catch, and it can only be seen after scoring has actually run.
    mock_llm._structured = {
        "required_skills": ["Quantum Flux Optimization"],
        "preferred_skills": [],
        "min_years_experience": None,
        "titles": [],
    }
    candidates = [
        make_candidate(skills=["Python", "Docker"], similarity_score=0.7) for _ in range(5)
    ]
    mock_second_brain.set_candidates(candidates)

    resp = _submit_job(client, auth_headers, title="Anomalous Job", top_k=10)
    assert resp.status_code == 200
    body = resp.json()

    assert body["extraction_status"] == "flagged"
    anomaly_warnings = [w for w in body["extraction_warnings"] if w["field"] == "match_outcomes"]
    assert len(anomaly_warnings) == 1
    assert "0 of 5" in anomaly_warnings[0]["message"]
    assert all(not m["qualified"] for m in body["matches"])
    assert all(not m["hard_gate_passed"] for m in body["matches"])

    from db import JobDescription

    job_id = uuid.UUID(body["job_id"])
    jd = db_session.query(JobDescription).filter(JobDescription.job_id == job_id).first()
    assert jd.extraction_status == "flagged"
    assert any(w["field"] == "match_outcomes" for w in jd.extraction_warnings)
    # The reproducibility-critical fields (what was actually matched against)
    # are untouched by the anomaly check -- only extraction_status/
    # extraction_warnings changed.
    assert jd.required_skills == ["Quantum Flux Optimization"]
    assert jd.raw_text == "We need a backend engineer skilled in Python with 2+ years experience."


def test_submit_job_match_anomaly_does_not_fire_for_low_semantic_relevance(
    client, auth_headers, mock_second_brain, mock_llm
):
    # Same shape (5 candidates, all fail the hard gate, none qualify) but the
    # retrieved pool just isn't relevant -- that alone explains 0 qualified, no
    # JD problem implicated.
    mock_llm._structured = {
        "required_skills": ["Quantum Flux Optimization"],
        "preferred_skills": [],
        "min_years_experience": None,
        "titles": [],
    }
    candidates = [make_candidate(skills=["Python"], similarity_score=0.2) for _ in range(5)]
    mock_second_brain.set_candidates(candidates)

    resp = _submit_job(client, auth_headers, title="Weak Retrieval Job", top_k=10)
    assert resp.status_code == 200
    body = resp.json()

    assert body["extraction_status"] == "clean"
    assert not any(w["field"] == "match_outcomes" for w in body["extraction_warnings"])


def test_submit_job_match_anomaly_does_not_fire_when_a_candidate_passes_the_hard_gate(
    client, auth_headers, mock_second_brain, mock_llm
):
    mock_llm._structured = {
        "required_skills": ["Quantum Flux Optimization"],
        "preferred_skills": [],
        "min_years_experience": None,
        "titles": [],
    }
    candidates = [make_candidate(skills=["Python"], similarity_score=0.7) for _ in range(4)]
    candidates.append(make_candidate(skills=["Quantum Flux Optimization"], similarity_score=0.7))
    mock_second_brain.set_candidates(candidates)

    resp = _submit_job(client, auth_headers, title="One Real Match Job", top_k=10)
    assert resp.status_code == 200
    body = resp.json()

    assert body["extraction_status"] == "clean"
    assert not any(w["field"] == "match_outcomes" for w in body["extraction_warnings"])
    assert any(m["hard_gate_passed"] for m in body["matches"])


def test_submit_job_match_anomaly_does_not_fire_with_too_small_a_pool(
    client, auth_headers, mock_second_brain, mock_llm
):
    mock_llm._structured = {
        "required_skills": ["Quantum Flux Optimization"],
        "preferred_skills": [],
        "min_years_experience": None,
        "titles": [],
    }
    # Only 3 candidates -- below match_anomaly.py's minimum sample size, even
    # though every other condition (relevant, all fail the gate) is met.
    candidates = [make_candidate(skills=["Python"], similarity_score=0.9) for _ in range(3)]
    mock_second_brain.set_candidates(candidates)

    resp = _submit_job(client, auth_headers, title="Small Pool Job", top_k=10)
    assert resp.status_code == 200
    body = resp.json()

    assert body["extraction_status"] == "clean"
    assert not any(w["field"] == "match_outcomes" for w in body["extraction_warnings"])


def test_submit_job_match_anomaly_never_touches_persisted_match_result_scoring_fields(
    client, auth_headers, db_session, mock_second_brain, mock_llm
):
    # The anomaly check runs after every MatchResult below is already persisted --
    # confirms it never rescales/rewrites any of them: every scoring field in the
    # DB matches exactly what the API response (computed by scoring.score_candidate,
    # before match_anomaly.py ever runs) already said.
    mock_llm._structured = {
        "required_skills": ["Quantum Flux Optimization"],
        "preferred_skills": [],
        "min_years_experience": None,
        "titles": [],
    }
    candidates = [
        make_candidate(skills=["Python", "Docker"], similarity_score=0.7) for _ in range(5)
    ]
    mock_second_brain.set_candidates(candidates)

    resp = _submit_job(client, auth_headers, title="Byte For Byte Job", top_k=10)
    assert resp.status_code == 200
    body = resp.json()
    assert body["extraction_status"] == "flagged"  # the anomaly did fire

    from db import MatchResult

    job_id = uuid.UUID(body["job_id"])
    persisted = {
        str(m.candidate_id): m
        for m in db_session.query(MatchResult).filter(MatchResult.job_id == job_id).all()
    }
    assert len(persisted) == len(body["matches"])
    for match in body["matches"]:
        row = persisted[match["candidate_id"]]
        assert row.semantic_score == match["semantic_score"]
        assert row.skill_score == match["skill_score"]
        assert row.experience_score == match["experience_score"]
        assert row.title_score == match["title_score"]
        assert row.deterministic_score == match["deterministic_score"]
        assert row.final_score == match["final_score"]
        assert row.hard_gate_passed == match["hard_gate_passed"]
        assert row.qualified == match["qualified"]
        assert row.matched_requirements == match["matched_requirements"]
        assert row.missing_requirements == match["missing_requirements"]


def test_submit_job_match_anomaly_persist_failure_does_not_turn_success_into_500(
    client, auth_headers, db_session, mock_second_brain, mock_llm, monkeypatch
):
    # Reliability audit finding (MEDIUM): the anomaly-metadata write is a purely
    # advisory, best-effort annotation, attempted inside its own SAVEPOINT (see
    # submit_job's docstring) so its failure never poisons the outer, still-open
    # transaction the job and every MatchResult are pending in. If this one
    # write fails (e.g. a transient DB error), the request must still commit and
    # return 200 with the job's real, actually-persisted state -- never a 500
    # that would make an otherwise fully successful submission look like a
    # total failure (and risk a recruiter resubmitting into a duplicate job).
    import main
    from sqlalchemy.exc import SQLAlchemyError

    def _boom(*args, **kwargs):
        raise SQLAlchemyError("simulated transient database failure")

    monkeypatch.setattr(main, "update_job_description_extraction_metadata", _boom)

    mock_llm._structured = {
        "required_skills": ["Quantum Flux Optimization"],
        "preferred_skills": [],
        "min_years_experience": None,
        "titles": [],
    }
    candidates = [
        make_candidate(skills=["Python", "Docker"], similarity_score=0.7) for _ in range(5)
    ]
    mock_second_brain.set_candidates(candidates)

    resp = _submit_job(client, auth_headers, title="Anomaly Persist Failure Job", top_k=10)
    assert resp.status_code == 200
    body = resp.json()

    # The anomaly write failed, so the response must reflect the PRE-anomaly
    # state actually in the DB -- "clean" (this extraction never trips
    # jd_validation.py's own rules), not a promised-but-unwritten "flagged".
    assert body["extraction_status"] == "clean"
    assert body["extraction_warnings"] == []
    # The job and every MatchResult are still fully created -- the failure was
    # isolated to the one advisory write, nothing upstream of it was affected.
    assert len(body["matches"]) == 5

    from db import JobDescription

    job_id = uuid.UUID(body["job_id"])
    jd = db_session.query(JobDescription).filter(JobDescription.job_id == job_id).first()
    assert jd is not None
    assert jd.extraction_status == "clean"
    assert jd.extraction_warnings == []


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


def test_submit_job_retrieval_failure_returns_502(client, auth_headers, db_session, monkeypatch):
    import clients
    from clients import SecondBrainClientError

    async def _failing_retrieve(base_url, api_key, query_text, top_k):
        raise SecondBrainClientError("simulated network failure")

    monkeypatch.setattr(clients, "retrieve_candidates", _failing_retrieve)

    resp = _submit_job(client, auth_headers, title="Retrieval Failure Job")
    assert resp.status_code == 502
    assert "Second Brain retrieval failed" in resp.json()["detail"]

    # Milestone 40: submit_job is atomic -- Job/JobDescription creation happens
    # before Second Brain retrieval, but is only flushed, not committed, so a
    # retrieval failure must leave no orphaned Job behind. Before this fix, this
    # exact Job would already be durably committed with zero matches.
    #
    # The `client` fixture's own get_db override shares one long-lived db_session
    # across the whole test (unlike the real get_db, which closes -- and thus
    # rolls back anything uncommitted -- after every request), so a flushed-but-
    # uncommitted row is still visible to a query on that *same* session even
    # after the request "fails". The rollback() below simulates exactly what the
    # real per-request session cleanup does in production: only a genuinely
    # committed row survives it. Before this fix, this rollback would be a
    # no-op (the row was already truly committed) and the assertion would fail.
    db_session.rollback()
    from db import Job

    assert db_session.query(Job).filter(Job.title == "Retrieval Failure Job").first() is None


def test_submit_job_mid_loop_candidate_failure_leaves_nothing_committed(
    client, auth_headers, db_session, mock_second_brain, monkeypatch
):
    # Reliability-audit follow-up (milestone 40): a candidate-lookup failure
    # partway through the scoring loop (Second Brain becoming unreachable
    # mid-request) must leave NOTHING committed -- not the Job, not the
    # JobDescription, not any of the MatchResults already scored before the
    # failure. Before this fix, the Job + JobDescription + the first 2
    # MatchResults would already be durably persisted here, with the client's
    # 502 response carrying no job_id to even know a job now exists.
    candidates = [make_candidate(skills=["Python"], similarity_score=0.7) for _ in range(5)]
    mock_second_brain.set_candidates(candidates)

    import clients
    from clients import SecondBrainClientError

    real_get_candidate = clients.get_candidate
    call_count = {"n": 0}

    async def _failing_get_candidate(base_url, api_key, candidate_id):
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise SecondBrainClientError("simulated mid-loop failure")
        return await real_get_candidate(base_url, api_key, candidate_id)

    monkeypatch.setattr(clients, "get_candidate", _failing_get_candidate)

    resp = _submit_job(client, auth_headers, title="Mid-Loop Failure Job", top_k=10)
    assert resp.status_code == 502
    assert call_count["n"] == 3  # confirms the failure genuinely happened mid-loop, not before it

    # See test_submit_job_retrieval_failure_returns_502's comment: this simulates
    # the real per-request session cleanup the `client` fixture's shared session
    # skips, which is what actually discards anything only flushed, never
    # committed. Before this fix, this rollback would be a no-op (the Job and
    # the first 2 MatchResults were already truly committed) and both
    # assertions below would fail.
    db_session.rollback()
    from db import Job, MatchResult

    assert db_session.query(Job).filter(Job.title == "Mid-Loop Failure Job").first() is None
    assert db_session.query(MatchResult).count() == 0


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
# Advisory LLM match explanation -- generated once, cached, never affects scoring
# ---------------------------------------------------------------------------

def test_match_explanation_included_in_get_matches(client, auth_headers, mock_second_brain, mock_llm):
    candidate = make_candidate(skills=["Python"], similarity_score=0.7)
    mock_second_brain.set_candidates([candidate])
    job_id = _submit_job(client, auth_headers).json()["job_id"]

    resp = client.get(f"{_JOBS_URL}/{job_id}/matches", headers=auth_headers)
    assert resp.status_code == 200
    matches = resp.json()
    assert len(matches) == 1
    assert matches[0]["explanation"] == mock_llm.explanation_text
    assert len(mock_llm.generate_text_calls) == 1


def test_match_explanation_not_generated_at_job_submission_time(
    client, auth_headers, mock_second_brain, mock_llm
):
    """Explanation generation happens on read (GET .../matches), not eagerly during
    JD submission -- submit_job's own response must not trigger a Gemini call for it."""
    candidate = make_candidate(skills=["Python"], similarity_score=0.7)
    mock_second_brain.set_candidates([candidate])
    resp = _submit_job(client, auth_headers)
    assert resp.json()["matches"][0]["explanation"] is None
    assert mock_llm.generate_text_calls == []


def test_match_explanation_generated_once_and_reused_on_repeated_reads(
    client, auth_headers, mock_second_brain, mock_llm
):
    candidate = make_candidate(skills=["Python"], similarity_score=0.7)
    mock_second_brain.set_candidates([candidate])
    job_id = _submit_job(client, auth_headers).json()["job_id"]

    first = client.get(f"{_JOBS_URL}/{job_id}/matches", headers=auth_headers).json()
    second = client.get(f"{_JOBS_URL}/{job_id}/matches", headers=auth_headers).json()

    assert first[0]["explanation"] == second[0]["explanation"]
    # Two reads, but only one underlying Gemini generate_text call -- the second read
    # must hit the cached match_explanations row instead of calling Gemini again.
    assert len(mock_llm.generate_text_calls) == 1


def test_match_explanation_persisted_in_match_explanations_table(
    client, auth_headers, db_session, mock_second_brain
):
    candidate = make_candidate(skills=["Python"], similarity_score=0.7)
    mock_second_brain.set_candidates([candidate])
    job_id = _submit_job(client, auth_headers).json()["job_id"]

    client.get(f"{_JOBS_URL}/{job_id}/matches", headers=auth_headers)

    from db import MatchExplanation, MatchResult

    match = db_session.query(MatchResult).filter(MatchResult.job_id == uuid.UUID(job_id)).first()
    row = (
        db_session.query(MatchExplanation)
        .filter(MatchExplanation.match_result_id == match.id)
        .first()
    )
    assert row is not None
    assert row.explanation
    assert row.model
    assert row.prompt_version


def test_match_explanation_generation_failure_does_not_break_matching(
    client_failing_explanation, auth_headers, mock_second_brain
):
    """A Gemini failure on the explanation path must never affect the already-computed
    and persisted scores/qualification -- only `explanation` comes back null."""
    candidate = make_candidate(
        skills=["Java"],  # missing required Python -> disqualified, independent of explanation
        years_of_experience=10.0,
        similarity_score=0.99,
    )
    mock_second_brain.set_candidates([candidate])
    job_id = _submit_job(client_failing_explanation, auth_headers, threshold=0.1).json()["job_id"]

    resp = client_failing_explanation.get(f"{_JOBS_URL}/{job_id}/matches", headers=auth_headers)
    assert resp.status_code == 200
    match = resp.json()[0]
    assert match["hard_gate_passed"] is False
    assert match["missing_requirements"] == ["Python"]
    assert match["qualified"] is False
    assert match["explanation"] is None
    assert len(client_failing_explanation.failing_llm.generate_text_calls) == 1


def test_match_explanation_prompt_never_contains_pii(
    client, auth_headers, mock_second_brain, mock_llm
):
    """The prompt sent to Gemini is built only from candidate_snapshot (skills/years/
    job_titles) + matched/missing requirements + scores -- none of which can ever carry
    a candidate's name, email, or phone number (Second Brain never stores those, and
    main.py's candidate_snapshot only ever copies skills/years_of_experience/job_titles).
    """
    candidate = make_candidate(skills=["Python"], similarity_score=0.7)
    mock_second_brain.set_candidates([candidate])
    job_id = _submit_job(client, auth_headers).json()["job_id"]

    client.get(f"{_JOBS_URL}/{job_id}/matches", headers=auth_headers)

    assert len(mock_llm.generate_text_calls) == 1
    prompt = mock_llm.generate_text_calls[0]
    for marker in ("@", "email", "phone", "Email", "Phone", "candidate_name", "full_name"):
        assert marker not in prompt


def test_match_explanation_unavailable_gemini_key_returns_null_explanation(
    client, auth_headers, mock_second_brain, monkeypatch
):
    """If get_llm_provider_optional can't construct a provider at all (e.g. no Gemini
    key configured), GET .../matches must still succeed with valid scores -- only
    explanation stays null."""
    import main

    candidate = make_candidate(skills=["Python"], similarity_score=0.7)
    mock_second_brain.set_candidates([candidate])
    job_id = _submit_job(client, auth_headers).json()["job_id"]

    main.app.dependency_overrides[main.get_llm_provider_optional] = lambda: None
    resp = client.get(f"{_JOBS_URL}/{job_id}/matches", headers=auth_headers)
    assert resp.status_code == 200
    match = resp.json()[0]
    assert match["qualified"] is True
    assert match["explanation"] is None


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


# ===========================================================================
# Recruiter-decision feedback write-back -- RWS -> Second Brain
# ===========================================================================

def test_accept_triggers_feedback_write_back(client, auth_headers, mock_second_brain):
    """Accepting an application must fire a knowledge feedback write-back to Second
    Brain with the correct PII-free content, and it must have already completed by the
    time the response comes back (BackgroundTasks run within the same ASGI response
    cycle in TestClient, so no polling/sleep is needed here)."""
    candidate = make_candidate(skills=["Python"], years_of_experience=5.0, similarity_score=0.9)
    mock_second_brain.set_candidates([candidate])
    resp = _submit_job(client, auth_headers, title="Backend Engineer", threshold=0.1)
    match_id = resp.json()["matches"][0]["match_id"]
    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]

    accept_resp = client.post(f"/v1/applications/{application_id}/accept", headers=auth_headers)
    assert accept_resp.status_code == 200

    assert len(mock_second_brain.feedback_calls) == 1
    call = mock_second_brain.feedback_calls[0]
    assert call["candidate_id"] == candidate["candidate_id"]
    assert call["application_id"] == application_id
    assert call["decision"] == "accepted"
    assert call["job_title"] == "Backend Engineer"
    assert call["matched_requirements"] == ["Python"]
    assert call["missing_requirements"] == []
    assert 0.0 <= call["final_score"] <= 1.0
    assert call["decided_at"] is not None


def test_reject_triggers_feedback_write_back_with_rejected_decision(
    client, auth_headers, mock_second_brain
):
    _, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)
    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]

    client.post(f"/v1/applications/{application_id}/reject", headers=auth_headers)

    assert len(mock_second_brain.feedback_calls) == 1
    assert mock_second_brain.feedback_calls[0]["decision"] == "rejected"


def test_feedback_write_back_uses_this_matchs_score_not_a_recomputed_one(
    client, auth_headers, mock_second_brain
):
    """The write-back's final_score must come from the actual persisted MatchResult
    for this application, not be recomputed at decision time."""
    candidate = make_candidate(skills=["Python"], years_of_experience=5.0, similarity_score=0.77)
    mock_second_brain.set_candidates([candidate])
    resp = _submit_job(client, auth_headers, threshold=0.1)
    match_final_score = resp.json()["matches"][0]["final_score"]
    match_id = resp.json()["matches"][0]["match_id"]
    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]

    client.post(f"/v1/applications/{application_id}/accept", headers=auth_headers)

    assert mock_second_brain.feedback_calls[0]["final_score"] == match_final_score


def test_accept_succeeds_even_when_second_brain_write_back_fails(
    client, auth_headers, mock_second_brain
):
    """The write-back is fire-and-forget: if Second Brain is unreachable, the recruiter's
    accept/reject must still succeed -- the decision itself is already durably
    persisted before the write-back is even attempted."""
    _, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)
    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]

    mock_second_brain.fail_feedback()

    resp = client.post(f"/v1/applications/{application_id}/accept", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


def test_feedback_write_back_not_sent_on_failed_decision_attempt(
    client, auth_headers, mock_second_brain
):
    """A 409 (already-decided) attempt must not trigger a second write-back -- only a
    transition that actually succeeds does."""
    _, match_id = _submit_and_qualify_one_candidate(client, auth_headers, mock_second_brain)
    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]

    client.post(f"/v1/applications/{application_id}/accept", headers=auth_headers)
    assert len(mock_second_brain.feedback_calls) == 1

    second = client.post(f"/v1/applications/{application_id}/accept", headers=auth_headers)
    assert second.status_code == 409
    assert len(mock_second_brain.feedback_calls) == 1  # unchanged


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


# ===========================================================================
# Interview invitation -- POST /v1/applications/{id}/invite
# ===========================================================================

def _select_one_candidate(client, auth_headers, mock_second_brain, candidate=None) -> tuple[str, str]:
    """Submit a job and select its one qualified candidate. Returns (job_id, application_id)
    with the application left in 'selected' (not yet invited)."""
    candidate = candidate or make_candidate(skills=["Python"], similarity_score=0.9)
    mock_second_brain.set_candidates([candidate])
    resp = _submit_job(client, auth_headers, threshold=0.1)
    job_id = resp.json()["job_id"]
    match_id = resp.json()["matches"][0]["match_id"]
    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]
    return job_id, application_id


def test_invite_application_success(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    candidate = make_candidate(skills=["Python"], similarity_score=0.9)
    _, application_id = _select_one_candidate(client, auth_headers, mock_second_brain, candidate)
    mock_resume_intelligence.set_contact(
        candidate["candidate_id"],
        {"candidate_id": candidate["candidate_id"], "name": "Alice", "email": "alice@example.com", "phone": None},
    )

    resp = client.post(
        f"/v1/applications/{application_id}/invite",
        json={"language": "en"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "invited"
    assert body["invite_attempt_number"] == 1
    assert body["invited_at"] is not None

    assert len(mock_voicehr.calls) == 1
    call = mock_voicehr.calls[0]
    assert call["candidate_name"] == "Alice"
    assert call["candidate_email"] == "alice@example.com"
    assert call["language"] == "en"
    assert call["external_reference_id"] == application_id
    # VoiceHR now emails the candidate their link automatically; RWS still never sees
    # or stores it -- only the job title is passed through, for that email's greeting.
    assert call["job_title"] == "Backend Engineer"
    assert call["idempotency_key"] == f"{application_id}:1"


def test_invite_application_defaults_to_english(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    candidate = make_candidate(skills=["Python"], similarity_score=0.9)
    _, application_id = _select_one_candidate(client, auth_headers, mock_second_brain, candidate)
    mock_resume_intelligence.set_contact(
        candidate["candidate_id"],
        {"candidate_id": candidate["candidate_id"], "name": "Alice", "email": "alice@example.com", "phone": None},
    )

    resp = client.post(f"/v1/applications/{application_id}/invite", json={}, headers=auth_headers)
    assert resp.status_code == 200
    assert mock_voicehr.calls[0]["language"] == "en"


def test_invite_application_missing_contact_returns_422_without_calling_voicehr(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    """Pre-flight contact lookup must gate the VoiceHR call: if Resume Intelligence has
    no contact record, VoiceHR must never be contacted."""
    candidate = make_candidate(skills=["Python"], similarity_score=0.9)
    _, application_id = _select_one_candidate(client, auth_headers, mock_second_brain, candidate)
    # Deliberately not calling mock_resume_intelligence.set_contact -- no record exists.

    resp = client.post(
        f"/v1/applications/{application_id}/invite",
        json={"language": "en"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert mock_voicehr.calls == []


def test_invite_application_contact_without_email_returns_422_without_calling_voicehr(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    """A candidate can genuinely be stored with a contact record but no email --
    resume_intel.candidates.email is nullable and store_candidate explicitly supports
    it (a resume with no extractable address). That is a missing-data condition the
    recruiter can act on, so it must get the same explicit 422 as "no record at all",
    not be passed to VoiceHR as a null address and come back as an opaque 502."""
    candidate = make_candidate(skills=["Python"], similarity_score=0.9)
    _, application_id = _select_one_candidate(client, auth_headers, mock_second_brain, candidate)
    mock_resume_intelligence.set_contact(
        candidate["candidate_id"],
        {"candidate_id": candidate["candidate_id"], "name": "Alice", "email": None, "phone": None},
    )

    resp = client.post(
        f"/v1/applications/{application_id}/invite",
        json={"language": "en"},
        headers=auth_headers,
    )

    assert resp.status_code == 422
    assert "email" in resp.json()["detail"].lower()
    assert mock_voicehr.calls == []


def test_invite_application_voicehr_unavailable_returns_502(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    candidate = make_candidate(skills=["Python"], similarity_score=0.9)
    _, application_id = _select_one_candidate(client, auth_headers, mock_second_brain, candidate)
    mock_resume_intelligence.set_contact(
        candidate["candidate_id"],
        {"candidate_id": candidate["candidate_id"], "name": "Alice", "email": "alice@example.com", "phone": None},
    )
    mock_voicehr.fail()

    resp = client.post(
        f"/v1/applications/{application_id}/invite",
        json={"language": "en"},
        headers=auth_headers,
    )
    assert resp.status_code == 502


def test_reinvite_increments_attempt_number(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    candidate = make_candidate(skills=["Python"], similarity_score=0.9)
    _, application_id = _select_one_candidate(client, auth_headers, mock_second_brain, candidate)
    mock_resume_intelligence.set_contact(
        candidate["candidate_id"],
        {"candidate_id": candidate["candidate_id"], "name": "Alice", "email": "alice@example.com", "phone": None},
    )

    first = client.post(
        f"/v1/applications/{application_id}/invite", json={"language": "en"}, headers=auth_headers
    )
    second = client.post(
        f"/v1/applications/{application_id}/invite", json={"language": "en"}, headers=auth_headers
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["invite_attempt_number"] == 1
    assert second.json()["invite_attempt_number"] == 2

    assert mock_voicehr.calls[0]["idempotency_key"] == f"{application_id}:1"
    assert mock_voicehr.calls[1]["idempotency_key"] == f"{application_id}:2"


def test_invite_unknown_application_returns_404(client, auth_headers):
    resp = client.post(
        f"/v1/applications/{uuid.uuid4()}/invite", json={"language": "en"}, headers=auth_headers
    )
    assert resp.status_code == 404


def test_invite_already_decided_application_returns_409(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    candidate = make_candidate(skills=["Python"], similarity_score=0.9)
    _, application_id = _select_one_candidate(client, auth_headers, mock_second_brain, candidate)
    client.post(f"/v1/applications/{application_id}/reject", headers=auth_headers)

    resp = client.post(
        f"/v1/applications/{application_id}/invite", json={"language": "en"}, headers=auth_headers
    )
    assert resp.status_code == 409
    assert mock_voicehr.calls == []


def test_invite_requires_jwt_not_service_key(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    candidate = make_candidate(skills=["Python"], similarity_score=0.9)
    _, application_id = _select_one_candidate(client, auth_headers, mock_second_brain, candidate)

    resp = client.post(
        f"/v1/applications/{application_id}/invite",
        json={"language": "en"},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 401
    assert mock_voicehr.calls == []


def test_invite_application_failure_leaves_application_selected(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    """A failed initial send (VoiceHR unreachable, or reached but email delivery
    failed -- indistinguishable from here) must never advance the application past
    'selected'. invite_attempt_number also stays at 0: it is only persisted after
    VoiceHR responds successfully (see main.py: invite_application)."""
    candidate = make_candidate(skills=["Python"], similarity_score=0.9)
    job_id, application_id = _select_one_candidate(client, auth_headers, mock_second_brain, candidate)
    mock_resume_intelligence.set_contact(
        candidate["candidate_id"],
        {"candidate_id": candidate["candidate_id"], "name": "Alice", "email": "alice@example.com", "phone": None},
    )
    mock_voicehr.fail()

    resp = client.post(
        f"/v1/applications/{application_id}/invite", json={"language": "en"}, headers=auth_headers
    )
    assert resp.status_code == 502

    applications = client.get(f"{_JOBS_URL}/{job_id}/applications", headers=auth_headers).json()
    application = next(a for a in applications if a["application_id"] == application_id)
    assert application["status"] == "selected"
    assert application["invite_attempt_number"] == 0


def test_retry_after_failed_send_reuses_same_idempotency_key(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr, monkeypatch
):
    """The Retry button on the frontend is just this same POST /invite call again.
    Since a failed attempt never persists an incremented invite_attempt_number, the
    retry recomputes the exact same attempt number and therefore the exact same
    idempotency key -- which is what makes VoiceHR reuse the same interview/token
    instead of creating a duplicate one."""
    candidate = make_candidate(skills=["Python"], similarity_score=0.9)
    _, application_id = _select_one_candidate(client, auth_headers, mock_second_brain, candidate)
    mock_resume_intelligence.set_contact(
        candidate["candidate_id"],
        {"candidate_id": candidate["candidate_id"], "name": "Alice", "email": "alice@example.com", "phone": None},
    )
    mock_voicehr.fail()

    first = client.post(
        f"/v1/applications/{application_id}/invite", json={"language": "en"}, headers=auth_headers
    )
    assert first.status_code == 502

    import voicehr_client as _voicehr_client_module

    async def _succeed(base_url, api_key, idempotency_key, candidate_name, candidate_email,
                        language, external_reference_id, job_title=None):
        mock_voicehr.calls.append({"idempotency_key": idempotency_key})
        return {
            "interview_link": "http://voicehr.test/interview?token=retry",
            "expires_at": "2026-09-01T00:00:00Z",
            "external_reference_id": external_reference_id,
        }

    monkeypatch.setattr(_voicehr_client_module, "invite_candidate", _succeed)

    second = client.post(
        f"/v1/applications/{application_id}/invite", json={"language": "en"}, headers=auth_headers
    )
    assert second.status_code == 200
    assert second.json()["invite_attempt_number"] == 1
    assert mock_voicehr.calls[-1]["idempotency_key"] == f"{application_id}:1"


# ===========================================================================
# Explicit resend -- POST /v1/applications/{id}/resend-invite
#
# Distinct from a normal (re-)invite above: only allowed once already 'invited',
# never creates a new interview/attempt, and a failure must never revert the
# application away from 'invited'.
# ===========================================================================

def test_explicit_resend_sends_intentionally_and_reuses_interview(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    _, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )

    resp = client.post(f"/v1/applications/{application_id}/resend-invite", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "invited"
    # No new interview/attempt: resend never calls invite_candidate again, and
    # invite_attempt_number is untouched (still the original 1 from select_and_invite).
    assert body["invite_attempt_number"] == 1
    assert len(mock_voicehr.calls) == 1  # only the original invite call, no new one

    assert len(mock_voicehr.resend_calls) == 1
    assert mock_voicehr.resend_calls[0]["external_reference_id"] == application_id


def test_explicit_resend_failure_leaves_application_invited(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    job_id, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    mock_voicehr.fail_resend()

    resp = client.post(f"/v1/applications/{application_id}/resend-invite", headers=auth_headers)
    assert resp.status_code == 502

    applications = client.get(f"{_JOBS_URL}/{job_id}/applications", headers=auth_headers).json()
    application = next(a for a in applications if a["application_id"] == application_id)
    assert application["status"] == "invited"  # never reverted


def test_explicit_resend_on_selected_application_returns_409(
    client, auth_headers, mock_second_brain, mock_voicehr
):
    """Resend only makes sense once VoiceHR has already created an interview
    (i.e. 'invited'); a 'selected' application has nothing to resend yet -- use the
    initial invite/retry path instead."""
    candidate = make_candidate(skills=["Python"], similarity_score=0.9)
    _, application_id = _select_one_candidate(client, auth_headers, mock_second_brain, candidate)

    resp = client.post(f"/v1/applications/{application_id}/resend-invite", headers=auth_headers)
    assert resp.status_code == 409
    assert mock_voicehr.resend_calls == []


def test_explicit_resend_on_unknown_application_returns_404(client, auth_headers):
    resp = client.post(f"/v1/applications/{uuid.uuid4()}/resend-invite", headers=auth_headers)
    assert resp.status_code == 404


def test_explicit_resend_requires_jwt_not_service_key(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    _, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )

    resp = client.post(
        f"/v1/applications/{application_id}/resend-invite", headers={"x-api-key": _VALID_KEY}
    )
    assert resp.status_code == 401
    assert mock_voicehr.resend_calls == []


def test_rapid_repeated_resend_is_rate_limited(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    """Guards against an impatient double-click firing two real provider sends in
    quick succession -- a short server-side cooldown per application, not a new
    persisted field or workflow status (see main.py: resend_invite)."""
    _, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )

    first = client.post(f"/v1/applications/{application_id}/resend-invite", headers=auth_headers)
    second = client.post(f"/v1/applications/{application_id}/resend-invite", headers=auth_headers)

    assert first.status_code == 200
    assert second.status_code == 429
    assert len(mock_voicehr.resend_calls) == 1  # only the first click actually sent


# ===========================================================================
# VoiceHR -> RWS webhook -- POST /v1/webhooks/voicehr
# ===========================================================================

def test_webhook_valid_assessment_transitions_to_interviewed(
    client, auth_headers, db_session, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    _, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )

    body, headers = make_webhook_payload(application_id, interview_status="completed")
    resp = client.post(_WEBHOOK_URL, content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"

    from db import Application

    application = (
        db_session.query(Application).filter(Application.id == uuid.UUID(application_id)).first()
    )
    assert application.status == "interviewed"
    assert application.interview_status == "completed"
    assert application.interview_assessment["overall_score"] == 78
    assert application.interview_completed_at is not None


def test_webhook_invalid_signature_returns_401(client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr):
    _, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    body, headers = make_webhook_payload(application_id)
    headers["X-Signature"] = "0" * 64  # well-formed hex, wrong value

    resp = client.post(_WEBHOOK_URL, content=body, headers=headers)
    assert resp.status_code == 401


def test_webhook_missing_signature_returns_401(client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr):
    _, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    body, headers = make_webhook_payload(application_id)
    del headers["X-Signature"]

    resp = client.post(_WEBHOOK_URL, content=body, headers=headers)
    assert resp.status_code == 401


def test_webhook_duplicate_delivery_id_is_idempotent(
    client, auth_headers, db_session, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    _, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    body, headers = make_webhook_payload(application_id)

    first = client.post(_WEBHOOK_URL, content=body, headers=headers)
    second = client.post(_WEBHOOK_URL, content=body, headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate_ignored"

    from db import Application

    application = (
        db_session.query(Application).filter(Application.id == uuid.UUID(application_id)).first()
    )
    assert application.status == "interviewed"  # unaffected by the duplicate


# ---------------------------------------------------------------------------
# Webhook receipt durability. The receipt row and the application state change it
# authorizes are one transaction (see db.insert_webhook_event): the receipt no
# longer commits on its own, so every route path has to commit deliberately. These
# assert each path actually does -- without them, a missing commit would silently
# drop the audit receipt on the no-op paths, and (worse) the previous separate-commit
# design's lost-result window would be easy to reintroduce unnoticed.
# ---------------------------------------------------------------------------

def _webhook_event(db_session, delivery_id: str):
    from db import InterviewWebhookEvent

    return (
        db_session.query(InterviewWebhookEvent)
        .filter(InterviewWebhookEvent.delivery_id == delivery_id)
        .first()
    )


def test_webhook_receipt_and_state_change_are_both_persisted(
    client, auth_headers, db_session, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    _, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    body, headers = make_webhook_payload(application_id, delivery_id="receipt-processed-1")

    resp = client.post(_WEBHOOK_URL, content=body, headers=headers)
    assert resp.json()["status"] == "processed"

    # Only genuinely committed rows survive this (see
    # test_submit_job_retrieval_failure_returns_502's comment) -- which is the point:
    # it is what makes these assertions about *committing* rather than merely flushing.
    db_session.rollback()

    from db import Application

    event = _webhook_event(db_session, "receipt-processed-1")
    application = (
        db_session.query(Application).filter(Application.id == uuid.UUID(application_id)).first()
    )
    assert event is not None
    assert event.processed is True
    assert application.status == "interviewed"


def test_webhook_receipt_is_persisted_even_when_the_application_is_not_invitable(
    client, auth_headers, db_session, mock_second_brain
):
    """A no-op for application state is still a real delivery that must stay
    auditable -- with processed=False recording that it was never applied."""
    candidate = make_candidate(skills=["Python"], similarity_score=0.9)
    _, application_id = _select_one_candidate(client, auth_headers, mock_second_brain, candidate)
    body, headers = make_webhook_payload(application_id, delivery_id="receipt-noop-1")

    resp = client.post(_WEBHOOK_URL, content=body, headers=headers)
    assert resp.json()["status"] == "no_op"  # still 'selected', never invited

    db_session.rollback()  # only a committed receipt survives -- see the test above

    event = _webhook_event(db_session, "receipt-noop-1")
    assert event is not None
    assert event.processed is False


def test_webhook_receipt_is_persisted_for_an_unresolvable_reference(client, db_session):
    """Recording an unresolvable external_reference_id is precisely what this table
    exists for (see db.InterviewWebhookEvent) -- it must survive the commit change."""
    body, headers = make_webhook_payload("not-a-uuid", delivery_id="receipt-unknown-1")

    resp = client.post(_WEBHOOK_URL, content=body, headers=headers)
    assert resp.json()["status"] == "unknown_application"

    db_session.rollback()  # only a committed receipt survives -- see the test above

    event = _webhook_event(db_session, "receipt-unknown-1")
    assert event is not None
    assert event.application_id is None
    assert event.processed is False


def test_webhook_unknown_application_returns_200_noop(client, auth_headers):
    body, headers = make_webhook_payload(str(uuid.uuid4()))
    resp = client.post(_WEBHOOK_URL, content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_op"


def test_webhook_malformed_external_reference_id_returns_200_noop(client, auth_headers):
    body, headers = make_webhook_payload("not-a-uuid")
    resp = client.post(_WEBHOOK_URL, content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "unknown_application"


def test_webhook_missing_delivery_id_returns_422(client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr):
    _, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    body, headers = make_webhook_payload(application_id)
    del headers["X-Delivery-Id"]

    resp = client.post(_WEBHOOK_URL, content=body, headers=headers)
    assert resp.status_code == 422


def test_webhook_invalid_payload_shape_returns_422(client):
    import json as _json
    body = _json.dumps({"not": "a valid webhook payload"}).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Signature": sign_webhook_body(body),
        "X-Delivery-Id": str(uuid.uuid4()),
    }
    resp = client.post(_WEBHOOK_URL, content=body, headers=headers)
    assert resp.status_code == 422


def test_webhook_for_application_not_yet_invited_is_noop(
    client, auth_headers, mock_second_brain
):
    """A webhook referencing an application that is still 'selected' (never invited) --
    e.g. a stale/out-of-order delivery -- must not be treated as an error."""
    _, application_id = _select_one_candidate(client, auth_headers, mock_second_brain)
    body, headers = make_webhook_payload(application_id)

    resp = client.post(_WEBHOOK_URL, content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_op"


def test_webhook_inconclusive_status_persisted(
    client, auth_headers, db_session, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    _, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    body, headers = make_webhook_payload(
        application_id, interview_status="inconclusive", inconclusive=True, overall_score=None
    )
    resp = client.post(_WEBHOOK_URL, content=body, headers=headers)
    assert resp.status_code == 200

    from db import Application

    application = (
        db_session.query(Application).filter(Application.id == uuid.UUID(application_id)).first()
    )
    assert application.interview_status == "inconclusive"
    assert application.interview_assessment["inconclusive"] is True


def test_webhook_unexpected_error_returns_500(
    client, auth_headers, db_session, mock_second_brain, mock_resume_intelligence, mock_voicehr,
    monkeypatch,
):
    """
    A genuine unexpected failure (e.g. a DB error) must propagate rather than be
    silently swallowed into a 200 no-op -- the distinction matters for VoiceHR's
    caller-side observability even though this project doesn't retry on it.

    The shared `client` fixture uses TestClient(raise_server_exceptions=True), which
    re-raises an unhandled exception in the test process instead of converting it to an
    HTTP 500 (a debugging convenience). That re-raise IS the signal this test checks:
    it proves the error is not caught anywhere in the handler and would reach uvicorn's
    default exception handling in production, which returns 500.
    """
    _, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    import main

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated transient database failure")

    monkeypatch.setattr(main, "record_interview_result", _boom)

    body, headers = make_webhook_payload(application_id, delivery_id="boom-1")
    with pytest.raises(RuntimeError, match="simulated transient database failure"):
        client.post(_WEBHOOK_URL, content=body, headers=headers)

    # Simulates the real per-request session cleanup the `client` fixture's shared
    # session skips -- same reasoning as test_submit_job_retrieval_failure_returns_502's
    # own comment above; only a genuinely committed row survives it.
    db_session.rollback()

    # The receipt must NOT survive a failure that prevented the state change: the
    # receipt and the update are one transaction, so the whole thing rolls back and
    # a VoiceHR retry of this same delivery_id can still be applied. Before this fix
    # the receipt was committed on its own, so this rollback would be a no-op and the
    # row would still be here -- and its UNIQUE delivery_id would then make every
    # retry short-circuit to "duplicate_ignored", discarding the result forever.
    assert _webhook_event(db_session, "boom-1") is None


# ===========================================================================
# Decision after interview -- accept/reject can follow 'interviewed', not just 'selected'
# ===========================================================================

def test_accept_application_after_interview(
    client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
):
    _, application_id = select_and_invite(
        client, auth_headers, mock_second_brain, mock_resume_intelligence, mock_voicehr
    )
    body, headers = make_webhook_payload(application_id)
    client.post(_WEBHOOK_URL, content=body, headers=headers)

    resp = client.post(f"/v1/applications/{application_id}/accept", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    assert resp.json()["interview_status"] == "completed"
