"""
tests/test_candidates_jobs_graph.py
------------------------------
Auth-boundary and response-shape tests for GET /v1/graph/candidates-jobs on RWS.

Architecture under test: this route reads RWS's own already-persisted MatchResult/
Job rows (via db.list_all_match_results, mirroring db.list_all_applications' exact
JOIN shape) -- no Second Brain call, no new persistence, no scoring recomputation.
No real Postgres is required: main.list_all_match_results (imported from db.py into
main's namespace) is monkeypatched directly, same style as
test_applications_global.py -- these tests exercise the route's own auth wiring and
shape passthrough, not db.py's SQL.
"""

from __future__ import annotations

import uuid
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


def _fake_match(*, job_id, candidate_id, final_score, qualified, hard_gate_passed,
                 matched_requirements=None, missing_requirements=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        job_id=job_id,
        candidate_id=candidate_id,
        final_score=final_score,
        qualified=qualified,
        hard_gate_passed=hard_gate_passed,
        matched_requirements=matched_requirements or [],
        missing_requirements=missing_requirements or [],
    )


# ---------------------------------------------------------------------------
# Auth boundary
# ---------------------------------------------------------------------------

def test_graph_without_jwt_returns_401(client, monkeypatch):
    calls = {"n": 0}

    monkeypatch.setattr(main, "list_all_match_results", lambda db: calls.__setitem__("n", calls["n"] + 1) or [])

    test_client, _ = client
    resp = test_client.get("/v1/graph/candidates-jobs")

    assert resp.status_code == 401
    assert calls["n"] == 0


def test_graph_with_service_api_key_instead_of_jwt_returns_401(client):
    test_client, _ = client
    resp = test_client.get(
        "/v1/graph/candidates-jobs", headers={"x-api-key": main.settings.service_api_key}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Response shape / content
# ---------------------------------------------------------------------------

def test_empty_graph_returns_empty_jobs_and_edges(client, monkeypatch):
    monkeypatch.setattr(main, "list_all_match_results", lambda db: [])

    test_client, recruiter_id = client
    resp = test_client.get("/v1/graph/candidates-jobs", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    assert resp.json() == {"jobs": [], "edges": []}


def test_multiple_jobs_and_candidates_produce_correct_nodes_and_edges(client, monkeypatch):
    job_a_id = uuid.uuid4()
    job_b_id = uuid.uuid4()
    candidate_1 = uuid.uuid4()
    candidate_2 = uuid.uuid4()
    candidate_3 = uuid.uuid4()

    rows = [
        (_fake_match(job_id=job_a_id, candidate_id=candidate_1, final_score=0.9, qualified=True, hard_gate_passed=True), "Backend Engineer"),
        (_fake_match(job_id=job_a_id, candidate_id=candidate_2, final_score=0.4, qualified=False, hard_gate_passed=False), "Backend Engineer"),
        (_fake_match(job_id=job_b_id, candidate_id=candidate_1, final_score=0.7, qualified=True, hard_gate_passed=True), "Data Analyst"),
        (_fake_match(job_id=job_b_id, candidate_id=candidate_3, final_score=0.6, qualified=True, hard_gate_passed=True), "Data Analyst"),
    ]
    monkeypatch.setattr(main, "list_all_match_results", lambda db: rows)

    test_client, recruiter_id = client
    resp = test_client.get("/v1/graph/candidates-jobs", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    body = resp.json()

    # Correct job-title join: each job appears exactly once, with the right title,
    # even though job_a appears twice in the source rows.
    assert len(body["jobs"]) == 2
    jobs_by_id = {job["job_id"]: job["title"] for job in body["jobs"]}
    assert jobs_by_id[str(job_a_id)] == "Backend Engineer"
    assert jobs_by_id[str(job_b_id)] == "Data Analyst"

    # Every match becomes exactly one edge -- no deduplication or filtering of edges.
    assert len(body["edges"]) == 4
    candidate_ids_seen = {edge["candidate_id"] for edge in body["edges"]}
    assert candidate_ids_seen == {str(candidate_1), str(candidate_2), str(candidate_3)}


def test_qualified_and_disqualified_edges_both_present(client, monkeypatch):
    job_id = uuid.uuid4()
    qualified_match = _fake_match(
        job_id=job_id, candidate_id=uuid.uuid4(), final_score=0.95, qualified=True, hard_gate_passed=True,
        matched_requirements=["Python", "FastAPI"], missing_requirements=[],
    )
    disqualified_match = _fake_match(
        job_id=job_id, candidate_id=uuid.uuid4(), final_score=0.6, qualified=False, hard_gate_passed=False,
        matched_requirements=["Python"], missing_requirements=["PostgreSQL"],
    )
    monkeypatch.setattr(
        main, "list_all_match_results",
        lambda db: [(qualified_match, "Backend Engineer"), (disqualified_match, "Backend Engineer")],
    )

    test_client, recruiter_id = client
    resp = test_client.get("/v1/graph/candidates-jobs", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    edges = resp.json()["edges"]
    assert len(edges) == 2
    qualified_states = {edge["qualified"] for edge in edges}
    assert qualified_states == {True, False}
    # The disqualified match must not be dropped or filtered out -- the graph is a
    # read of the full audit trail, qualified and disqualified alike.
    disqualified_edge = next(e for e in edges if e["qualified"] is False)
    assert disqualified_edge["hard_gate_passed"] is False


def test_persisted_score_and_requirement_fields_pass_through_unchanged(client, monkeypatch):
    job_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    match = _fake_match(
        job_id=job_id, candidate_id=candidate_id, final_score=0.8347, qualified=True, hard_gate_passed=True,
        matched_requirements=["Python", "Docker"], missing_requirements=["Kubernetes"],
    )
    monkeypatch.setattr(main, "list_all_match_results", lambda db: [(match, "Backend Engineer")])

    test_client, recruiter_id = client
    resp = test_client.get("/v1/graph/candidates-jobs", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    edge = resp.json()["edges"][0]
    assert edge["match_id"] == str(match.id)
    assert edge["candidate_id"] == str(candidate_id)
    assert edge["job_id"] == str(job_id)
    # Not rounded, not recomputed -- the exact persisted value.
    assert edge["final_score"] == 0.8347
    assert edge["matched_requirements"] == ["Python", "Docker"]
    assert edge["missing_requirements"] == ["Kubernetes"]


def test_response_has_no_candidates_array_and_no_extra_fields(client, monkeypatch):
    job_id = uuid.uuid4()
    match = _fake_match(job_id=job_id, candidate_id=uuid.uuid4(), final_score=0.5, qualified=True, hard_gate_passed=True)
    monkeypatch.setattr(main, "list_all_match_results", lambda db: [(match, "Backend Engineer")])

    test_client, recruiter_id = client
    resp = test_client.get("/v1/graph/candidates-jobs", headers=_auth_header(recruiter_id))

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"jobs", "edges"}
    assert set(body["jobs"][0].keys()) == {"job_id", "title"}
    assert set(body["edges"][0].keys()) == {
        "match_id", "candidate_id", "job_id", "final_score",
        "qualified", "hard_gate_passed", "matched_requirements", "missing_requirements",
    }


# ---------------------------------------------------------------------------
# No new persistence
# ---------------------------------------------------------------------------

def test_route_makes_no_db_write(client, monkeypatch):
    # Unlike get_candidate_identity/get_candidates/get_candidate_knowledge (which
    # take no `db` at all, since they proxy another service), this endpoint
    # genuinely reads RWS's own Postgres -- so it does take `db`. The relevant proof
    # here is that it never writes: no db.add()/db.commit() anywhere in its body.
    import inspect

    source = inspect.getsource(main.get_candidates_jobs_graph)
    assert "db.add(" not in source
    assert "db.commit(" not in source
