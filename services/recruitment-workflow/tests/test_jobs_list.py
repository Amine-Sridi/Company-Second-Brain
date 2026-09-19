"""
tests/test_jobs_list.py
------------------------------
Auth-boundary and response-shape tests for GET /v1/jobs on RWS.

No real Postgres is required: main.list_jobs_with_match_counts (imported from db.py
into main's namespace) is monkeypatched directly, so these tests exercise the route's
own auth wiring, ordering/shape passthrough and response model -- not db.py's SQL.
db.py's actual GROUP BY/ORDER BY query is SQL, not app logic, and is not exercised
here.
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


def _fake_job(title: str, created_at: datetime, required_language=None, min_language_cefr=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        title=title,
        created_at=created_at,
        required_language=required_language,
        min_language_cefr=min_language_cefr,
    )


# ---------------------------------------------------------------------------
# Auth boundary
# ---------------------------------------------------------------------------

def test_jobs_without_jwt_returns_401(client):
    test_client, _ = client
    resp = test_client.get("/v1/jobs")
    assert resp.status_code == 401


def test_jobs_with_service_api_key_instead_of_jwt_returns_401(client):
    # A service API key must never substitute for a recruiter JWT on this endpoint.
    test_client, _ = client
    resp = test_client.get("/v1/jobs", headers={"x-api-key": main.settings.service_api_key})
    assert resp.status_code == 401


def test_jobs_with_valid_jwt_is_accepted(client, monkeypatch):
    monkeypatch.setattr(main, "list_jobs_with_match_counts", lambda db: [])
    test_client, recruiter_id = client
    resp = test_client.get("/v1/jobs", headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Response shape / content
# ---------------------------------------------------------------------------

def test_empty_database_returns_empty_list(client, monkeypatch):
    monkeypatch.setattr(main, "list_jobs_with_match_counts", lambda db: [])
    test_client, recruiter_id = client
    resp = test_client.get("/v1/jobs", headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_multiple_jobs_newest_first_with_match_counts(client, monkeypatch):
    now = datetime.now(timezone.utc)
    newest = _fake_job("Newest Job", now)
    middle = _fake_job("Middle Job", now - timedelta(days=1))
    oldest = _fake_job("Oldest Job", now - timedelta(days=2))

    # db.py's own query already returns newest-first (ORDER BY created_at DESC); the
    # route must pass that ordering through unchanged, not re-sort or reverse it.
    monkeypatch.setattr(
        main, "list_jobs_with_match_counts",
        lambda db: [(newest, "clean", [], 5), (middle, "clean", [], 0), (oldest, "clean", [], 2)],
    )

    test_client, recruiter_id = client
    resp = test_client.get("/v1/jobs", headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})

    assert resp.status_code == 200
    body = resp.json()
    assert [row["title"] for row in body] == ["Newest Job", "Middle Job", "Oldest Job"]
    assert [row["match_count"] for row in body] == [5, 0, 2]
    assert set(body[0].keys()) == {
        "job_id", "title", "created_at", "match_count", "required_language", "min_language_cefr",
        "extraction_status", "extraction_warnings",
    }


def test_job_with_no_matches_reports_zero(client, monkeypatch):
    job = _fake_job("Lonely Job", datetime.now(timezone.utc))
    monkeypatch.setattr(main, "list_jobs_with_match_counts", lambda db: [(job, "clean", [], 0)])

    test_client, recruiter_id = client
    resp = test_client.get("/v1/jobs", headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})

    assert resp.status_code == 200
    assert resp.json()[0]["match_count"] == 0


def test_job_with_no_language_requirement_returns_null_pair(client, monkeypatch):
    job = _fake_job("No Requirement Job", datetime.now(timezone.utc))
    monkeypatch.setattr(main, "list_jobs_with_match_counts", lambda db: [(job, "clean", [], 0)])

    test_client, recruiter_id = client
    resp = test_client.get("/v1/jobs", headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["required_language"] is None
    assert row["min_language_cefr"] is None


def test_job_with_language_requirement_returns_both_fields(client, monkeypatch):
    job = _fake_job(
        "English B2 Job", datetime.now(timezone.utc),
        required_language="en", min_language_cefr="B2",
    )
    monkeypatch.setattr(main, "list_jobs_with_match_counts", lambda db: [(job, "clean", [], 0)])

    test_client, recruiter_id = client
    resp = test_client.get("/v1/jobs", headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["required_language"] == "en"
    assert row["min_language_cefr"] == "B2"


def test_response_model_forbids_extra_fields_from_leaking(client, monkeypatch):
    # JobSummaryResponse is deliberately narrow (no requirements/threshold/matches);
    # this pins that the route builds JobSummaryResponse rather than e.g. JobResponse.
    job = _fake_job("Some Job", datetime.now(timezone.utc))
    monkeypatch.setattr(main, "list_jobs_with_match_counts", lambda db: [(job, "clean", [], 1)])

    test_client, recruiter_id = client
    resp = test_client.get("/v1/jobs", headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})

    assert resp.status_code == 200
    row = resp.json()[0]
    assert "threshold" not in row
    assert "matches" not in row
    assert "required_skills" not in row


# ---------------------------------------------------------------------------
# extraction_status / extraction_warnings passthrough (jd_validation.py)
# ---------------------------------------------------------------------------

def test_flagged_job_reports_extraction_status_and_warnings(client, monkeypatch):
    job = _fake_job("Flagged Job", datetime.now(timezone.utc))
    warnings = [{"field": "required_skills", "message": "looks like a phrase, not a skill name"}]
    monkeypatch.setattr(main, "list_jobs_with_match_counts", lambda db: [(job, "flagged", warnings, 3)])

    test_client, recruiter_id = client
    resp = test_client.get("/v1/jobs", headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["extraction_status"] == "flagged"
    assert row["extraction_warnings"] == warnings


def test_clean_job_reports_clean_status_and_no_warnings(client, monkeypatch):
    job = _fake_job("Clean Job", datetime.now(timezone.utc))
    monkeypatch.setattr(main, "list_jobs_with_match_counts", lambda db: [(job, "clean", [], 3)])

    test_client, recruiter_id = client
    resp = test_client.get("/v1/jobs", headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["extraction_status"] == "clean"
    assert row["extraction_warnings"] == []


def test_missing_job_description_falls_back_to_clean_defensively(client, monkeypatch):
    # An outer join with no matching JobDescription row (shouldn't happen in practice --
    # create_job_with_description always creates both atomically) still must not 500.
    job = _fake_job("Orphaned Job", datetime.now(timezone.utc))
    monkeypatch.setattr(main, "list_jobs_with_match_counts", lambda db: [(job, None, None, 0)])

    test_client, recruiter_id = client
    resp = test_client.get("/v1/jobs", headers={"Authorization": f"Bearer {_recruiter_jwt(recruiter_id)}"})

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["extraction_status"] == "clean"
    assert row["extraction_warnings"] == []
