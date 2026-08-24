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
    from conftest_recruitment_workflow import make_candidate
except ImportError:
    from conftest import make_candidate

_VALID_KEY = "test-service-key"
_JOBS_URL = "/v1/jobs"
_SCORING_CONFIGS_URL = "/v1/scoring-configs"


def _submit_job(client, **overrides) -> dict:
    payload = {
        "title": "Backend Engineer",
        "raw_description": "We need a backend engineer skilled in Python with 2+ years experience.",
        "threshold": 0.5,
        "top_k": 20,
    }
    payload.update(overrides)
    return client.post(_JOBS_URL, json=payload, headers={"x-api-key": _VALID_KEY})


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

def test_submit_job_returns_200_with_structured_jd(client, mock_second_brain):
    mock_second_brain.set_candidates([])
    resp = _submit_job(client)
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


def test_submit_job_persists_job_and_job_description(client, db_session, mock_second_brain):
    mock_second_brain.set_candidates([])
    resp = _submit_job(client, title="Data Scientist")
    job_id = uuid.UUID(resp.json()["job_id"])

    from db import Job, JobDescription

    job = db_session.query(Job).filter(Job.id == job_id).first()
    assert job is not None
    assert job.title == "Data Scientist"

    jd = db_session.query(JobDescription).filter(JobDescription.job_id == job_id).first()
    assert jd is not None
    assert jd.version == 1
    assert jd.required_skills == ["Python"]


def test_submit_job_missing_raw_description_rejected(client):
    resp = client.post(
        _JOBS_URL,
        json={"title": "Backend Engineer"},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_submit_job_missing_key_returns_401(client, mock_second_brain):
    mock_second_brain.set_candidates([])
    resp = client.post(_JOBS_URL, json={"title": "x", "raw_description": "y"})
    assert resp.status_code == 401


def test_submit_job_llm_failure_returns_502(client_failing_llm, mock_second_brain):
    resp = client_failing_llm.post(
        _JOBS_URL,
        json={"title": "Backend Engineer", "raw_description": "Needs Python."},
        headers={"x-api-key": _VALID_KEY},
    )
    assert resp.status_code == 502
    assert "JD structuring failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Retrieval integration + hybrid scoring
# ---------------------------------------------------------------------------

def test_submit_job_scores_retrieved_candidates(client, mock_second_brain):
    candidate = make_candidate(
        skills=["Python", "Docker"],
        years_of_experience=5.0,
        job_titles=["Software Engineer"],
        similarity_score=0.9,
    )
    mock_second_brain.set_candidates([candidate])

    resp = _submit_job(client)
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


def test_submit_job_hard_gate_disqualifies_despite_high_semantic_score(client, mock_second_brain):
    candidate = make_candidate(
        skills=["Java"],  # missing required Python
        years_of_experience=10.0,
        similarity_score=0.99,
    )
    mock_second_brain.set_candidates([candidate])

    resp = _submit_job(client, threshold=0.1)
    matches = resp.json()["matches"]
    assert len(matches) == 1
    assert matches[0]["hard_gate_passed"] is False
    assert matches[0]["missing_requirements"] == ["Python"]
    assert matches[0]["qualified"] is False


def test_submit_job_below_threshold_not_qualified(client, mock_second_brain):
    candidate = make_candidate(
        skills=["Python"],
        years_of_experience=0.0,
        job_titles=[],
        similarity_score=0.05,
    )
    mock_second_brain.set_candidates([candidate])

    resp = _submit_job(client, threshold=0.95)
    matches = resp.json()["matches"]
    assert len(matches) == 1
    assert matches[0]["hard_gate_passed"] is True
    assert matches[0]["final_score"] < 0.95
    assert matches[0]["qualified"] is False


def test_submit_job_matches_sorted_by_final_score_desc(client, mock_second_brain):
    strong = make_candidate(skills=["Python", "Docker"], years_of_experience=5.0, similarity_score=0.95)
    weak = make_candidate(skills=["Python"], years_of_experience=2.0, similarity_score=0.3)
    mock_second_brain.set_candidates([weak, strong])

    resp = _submit_job(client, threshold=0.0)
    matches = resp.json()["matches"]
    assert len(matches) == 2
    scores = [m["final_score"] for m in matches]
    assert scores == sorted(scores, reverse=True)
    assert matches[0]["candidate_id"] == strong["candidate_id"]


def test_submit_job_retrieval_failure_returns_502(client, monkeypatch):
    import clients
    from clients import SecondBrainClientError

    async def _failing_retrieve(base_url, api_key, query_text, top_k):
        raise SecondBrainClientError("simulated network failure")

    monkeypatch.setattr(clients, "retrieve_candidates", _failing_retrieve)

    resp = _submit_job(client)
    assert resp.status_code == 502
    assert "Second Brain retrieval failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# MatchResult persistence -- immutable, contains all required components
# ---------------------------------------------------------------------------

def test_match_result_persisted_with_all_components(client, db_session, mock_second_brain):
    candidate = make_candidate(
        skills=["Python", "Docker"], years_of_experience=5.0, similarity_score=0.9
    )
    mock_second_brain.set_candidates([candidate])

    resp = _submit_job(client, threshold=0.4)
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

def test_get_job_matches_returns_persisted_matches(client, mock_second_brain):
    candidate = make_candidate(skills=["Python"], similarity_score=0.7)
    mock_second_brain.set_candidates([candidate])

    submit_resp = _submit_job(client)
    job_id = submit_resp.json()["job_id"]

    resp = client.get(f"{_JOBS_URL}/{job_id}/matches", headers={"x-api-key": _VALID_KEY})
    assert resp.status_code == 200
    matches = resp.json()
    assert len(matches) == 1
    assert matches[0]["candidate_id"] == candidate["candidate_id"]


def test_get_job_matches_unknown_job_returns_404(client):
    resp = client.get(f"{_JOBS_URL}/{uuid.uuid4()}/matches", headers={"x-api-key": _VALID_KEY})
    assert resp.status_code == 404


def test_get_job_matches_missing_key_returns_401(client):
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


def test_submit_job_can_pin_specific_scoring_config_version(client, mock_second_brain):
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
    resp = _submit_job(client, scoring_config_version=new_version)
    assert resp.status_code == 200
    assert resp.json()["scoring_config_version"] == new_version


def test_submit_job_unknown_scoring_config_version_rejected(client, mock_second_brain):
    mock_second_brain.set_candidates([])
    resp = _submit_job(client, scoring_config_version=9999)
    assert resp.status_code == 422
