"""
tests/test_api.py
-------------------
FastAPI-level tests for the /v1/resumes ingestion endpoint's failure handling
and its Resume Intelligence -> Second Brain integration.

DATABASE_URL is set to a syntactically valid but unreachable URL before importing
api/db -- db.get_db()'s engine creation is lazy (SQLAlchemy does not connect until a
query is actually issued), and these tests never reach a real query because
store_candidate is mocked out. No live Postgres is required.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost:5432/unused")

from fastapi.testclient import TestClient

import api
import second_brain_client
from pipeline.parsing_agent import ParseResult
from shared.llm import LLMProviderError


class _AlwaysFailLLM:
    async def generate_text(self, prompt, *, model=None):
        raise NotImplementedError

    async def generate_structured(self, prompt, schema, *, model=None):
        raise LLMProviderError("provider down")

    async def embed_text(self, text, *, model=None, dimensions=768):
        raise NotImplementedError


_VALID_CANDIDATE_JSON = {
    "basics": {"first_name": "Jean", "last_name": "Dupont", "email": "jean@example.com", "phone": None},
    "work": [], "education": [], "skills": [], "certifications": [], "spoken_languages": [],
}


def _client() -> TestClient:
    api.app.dependency_overrides[api.get_llm_provider] = lambda: _AlwaysFailLLM()
    return TestClient(api.app)


def _upload(client: TestClient, *, api_key: str | None = ...):
    """Uploads with the correct service key by default. Pass api_key=None to omit the
    header entirely, or any other string to send a wrong one -- both exercise the auth
    boundary tests below."""
    headers = {}
    if api_key is ...:
        headers["x-api-key"] = api.settings.service_api_key
    elif api_key is not None:
        headers["x-api-key"] = api_key
    return client.post(
        "/v1/resumes",
        files={"file": ("resume.pdf", b"not a real pdf, just bytes for the upload", "application/pdf")},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# POST /v1/resumes -- service-key auth boundary
# ---------------------------------------------------------------------------

def test_upload_without_api_key_returns_401_and_never_runs_the_pipeline(monkeypatch):
    pipeline_called = {"n": 0}

    async def fake_parse_resume_text(llm, raw_text, file_meta=None, *, model=None):
        pipeline_called["n"] += 1
        return ParseResult(data=_VALID_CANDIDATE_JSON, success=True, attempts=1)

    monkeypatch.setattr("pipeline.parsing_agent.parse_resume_text", fake_parse_resume_text)

    client = _client()
    try:
        resp = _upload(client, api_key=None)
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 401
    assert pipeline_called["n"] == 0


def test_upload_with_wrong_api_key_returns_401():
    client = _client()
    try:
        resp = _upload(client, api_key="not-the-real-key")
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 401


def test_upload_with_a_recruiter_jwt_shaped_bearer_header_is_still_rejected():
    # Resume Intelligence must never accept a recruiter JWT in place of the service
    # key -- it doesn't understand JWTs at all, only X-Api-Key. A Bearer/Authorization
    # header (what RWS's own recruiter auth uses) has no effect here whatsoever.
    client = _client()
    try:
        resp = client.post(
            "/v1/resumes",
            files={"file": ("resume.pdf", b"bytes", "application/pdf")},
            headers={"Authorization": "Bearer not-a-real-jwt.but.shaped-like-one"},
        )
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 401


def test_upload_with_correct_api_key_succeeds(monkeypatch):
    async def fake_parse_resume_text(llm, raw_text, file_meta=None, *, model=None):
        return ParseResult(data=_VALID_CANDIDATE_JSON, success=True, attempts=1)

    monkeypatch.setattr("pipeline.parsing_agent.parse_resume_text", fake_parse_resume_text)
    monkeypatch.setattr(api, "store_candidate", lambda db, candidate_json, raw_text: (str(uuid.uuid4()), True))

    async def fake_send_candidate_summary(base_url, api_key, summary):
        pass

    monkeypatch.setattr(second_brain_client, "send_candidate_summary", fake_send_candidate_summary)

    client = _client()
    try:
        resp = _upload(client)
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 201


def test_total_parsing_failure_returns_502_and_stores_no_candidate(monkeypatch):
    async def fake_parse_resume_text(llm, raw_text, file_meta=None, *, model=None):
        return ParseResult(data={}, success=False, flags=["PARSING_AGENT_FAILED_ALL_RETRIES"], attempts=3)

    monkeypatch.setattr("pipeline.parsing_agent.parse_resume_text", fake_parse_resume_text)

    store_calls = []
    monkeypatch.setattr(
        api, "store_candidate",
        lambda db, candidate_json, raw_text: store_calls.append(candidate_json) or ("unused", True),
    )

    client = _client()
    try:
        resp = _upload(client)
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 502
    assert store_calls == []


def test_successful_parse_stores_and_pushes_to_second_brain(monkeypatch):
    candidate_id = str(uuid.uuid4())

    async def fake_parse_resume_text(llm, raw_text, file_meta=None, *, model=None):
        return ParseResult(data=_VALID_CANDIDATE_JSON, success=True, attempts=1)

    monkeypatch.setattr("pipeline.parsing_agent.parse_resume_text", fake_parse_resume_text)

    store_calls = []

    def fake_store_candidate(db, candidate_json, raw_text):
        store_calls.append(candidate_json)
        return candidate_id, True

    monkeypatch.setattr(api, "store_candidate", fake_store_candidate)

    second_brain_calls = []

    async def fake_send_candidate_summary(base_url, api_key, summary):
        second_brain_calls.append((base_url, api_key, summary))

    monkeypatch.setattr(second_brain_client, "send_candidate_summary", fake_send_candidate_summary)

    client = _client()
    try:
        resp = _upload(client)
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 201
    assert len(store_calls) == 1
    body = resp.json()
    assert body["candidate_id"] == candidate_id
    assert body["is_new"] is True

    # exactly one Second Brain push, with the exact Resume Intelligence candidate_id
    assert len(second_brain_calls) == 1
    base_url, sb_api_key, summary = second_brain_calls[0]
    assert base_url == api.settings.second_brain_url
    assert sb_api_key == api.settings.second_brain_api_key
    assert str(summary.candidate_id) == candidate_id
    # PII never reaches the summary sent onward
    assert "Jean" not in summary.model_dump_json()
    assert "jean@example.com" not in summary.model_dump_json()


def test_second_brain_failure_returns_502_but_does_not_undo_the_stored_candidate(monkeypatch):
    candidate_id = str(uuid.uuid4())

    async def fake_parse_resume_text(llm, raw_text, file_meta=None, *, model=None):
        return ParseResult(data=_VALID_CANDIDATE_JSON, success=True, attempts=1)

    monkeypatch.setattr("pipeline.parsing_agent.parse_resume_text", fake_parse_resume_text)

    store_calls = []

    def fake_store_candidate(db, candidate_json, raw_text):
        store_calls.append(candidate_json)
        return candidate_id, True

    monkeypatch.setattr(api, "store_candidate", fake_store_candidate)

    async def failing_send_candidate_summary(base_url, api_key, summary):
        raise second_brain_client.SecondBrainClientError("Second Brain candidate ingestion returned 502")

    monkeypatch.setattr(second_brain_client, "send_candidate_summary", failing_send_candidate_summary)

    client = _client()
    try:
        resp = _upload(client)
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 502
    assert candidate_id in resp.json()["detail"]
    # store_candidate DID run and was never "undone" -- db.py has no delete/rollback
    # path for a candidate at all, so the only way to prove nothing was reverted is
    # to confirm the store call actually happened exactly once.
    assert len(store_calls) == 1
    assert store_calls[0] == _VALID_CANDIDATE_JSON


def test_retry_after_second_brain_failure_succeeds_via_existing_dedup(monkeypatch):
    """
    Simulates the safe-retry story end to end: first upload's Second Brain push
    fails (candidate is stored regardless); re-uploading the same resume dedups
    to the same candidate_id in Resume Intelligence (is_new=False, exactly what
    db.store_candidate's existing email-dedup already guarantees) and this time
    Second Brain succeeds. No queue, no retry infrastructure -- just re-running
    the same request.
    """
    candidate_id = str(uuid.uuid4())

    async def fake_parse_resume_text(llm, raw_text, file_meta=None, *, model=None):
        return ParseResult(data=_VALID_CANDIDATE_JSON, success=True, attempts=1)

    monkeypatch.setattr("pipeline.parsing_agent.parse_resume_text", fake_parse_resume_text)

    store_calls = []

    def fake_store_candidate(db, candidate_json, raw_text):
        # First call creates the candidate; every subsequent call with the same
        # data dedups to it (is_new=False), mirroring db.store_candidate's real
        # normalized-email dedup rule.
        is_new = len(store_calls) == 0
        store_calls.append(candidate_json)
        return candidate_id, is_new

    monkeypatch.setattr(api, "store_candidate", fake_store_candidate)

    calls = {"n": 0}

    async def flaky_send_candidate_summary(base_url, api_key, summary):
        calls["n"] += 1
        if calls["n"] == 1:
            raise second_brain_client.SecondBrainClientError("transient failure")
        # second attempt succeeds

    monkeypatch.setattr(second_brain_client, "send_candidate_summary", flaky_send_candidate_summary)

    client = _client()
    try:
        first = _upload(client)
        second = _upload(client)
    finally:
        api.app.dependency_overrides.clear()

    assert first.status_code == 502
    assert second.status_code == 201
    assert second.json() == {"candidate_id": candidate_id, "is_new": False}
    assert len(store_calls) == 2
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# Malformed / unsupported file uploads -> clean 422
# ---------------------------------------------------------------------------

def test_doc_upload_rejected_with_422_not_silently_degraded():
    # .doc is deliberately unsupported (see pipeline/text_extractor.py) -- it
    # must be rejected up front, never silently produce an empty extraction.
    client = _client()
    try:
        resp = client.post(
            "/v1/resumes",
            files={"file": ("resume.doc", b"anything", "application/msword")},
            headers={"x-api-key": api.settings.service_api_key},
        )
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 422
    assert ".doc" in resp.json()["detail"]


def test_unknown_extension_rejected_with_422():
    client = _client()
    try:
        resp = client.post(
            "/v1/resumes",
            files={"file": ("resume.exe", b"anything", "application/octet-stream")},
            headers={"x-api-key": api.settings.service_api_key},
        )
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Real upload happy path -- real extract_text + real parse_resume_text, only
# the LLM call and the Second Brain push are mocked (no network, no Postgres).
# ---------------------------------------------------------------------------

def test_real_upload_happy_path_through_real_extraction_and_parsing(monkeypatch):
    from schemas.candidate_extraction import CandidateBasics, CandidateExtraction, WorkExperience

    async def fake_generate_structured(prompt, schema, *, model=None):
        return CandidateExtraction(
            basics=CandidateBasics(first_name="Amine", last_name="Sridi", email="amine@example.com"),
            work=[WorkExperience(company="Acme", position="Engineer", start_date="2022-01", end_date="2023-01")],
        )

    class _RealLLM:
        generate_structured = staticmethod(fake_generate_structured)

        async def generate_text(self, *a, **k):
            raise NotImplementedError

        async def embed_text(self, *a, **k):
            raise NotImplementedError

    stored = {}

    def fake_store_candidate(db, candidate_json, raw_text):
        stored["candidate_json"] = candidate_json
        stored["raw_text"] = raw_text
        return str(uuid.uuid4()), True

    monkeypatch.setattr(api, "store_candidate", fake_store_candidate)

    async def fake_send_candidate_summary(base_url, api_key, summary):
        pass

    monkeypatch.setattr(second_brain_client, "send_candidate_summary", fake_send_candidate_summary)

    api.app.dependency_overrides[api.get_llm_provider] = lambda: _RealLLM()
    client = TestClient(api.app)
    try:
        sample = ROOT / "tests" / "sample_resumes" / "DEV_LINKEDIN_Jean DUPONT.docx"
        with open(sample, "rb") as f:
            resp = client.post(
                "/v1/resumes",
                files={"file": ("DEV_LINKEDIN_Jean DUPONT.docx", f.read(),
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                headers={"x-api-key": api.settings.service_api_key},
            )
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 201
    # extract_text really ran against the real .docx and produced non-trivial text
    assert len(stored["raw_text"]) > 100
    # parse_resume_text really ran its deterministic post-processing (normalize +
    # find_missing_fields), not just an untouched echo of the fake extraction above
    assert stored["candidate_json"]["basics"]["first_name"] == "Amine"
    assert stored["candidate_json"]["work"][0]["company"] == "Acme"


# ---------------------------------------------------------------------------
# Extraction failure (not a Gemini failure) -> clean 500, no PII leak
# ---------------------------------------------------------------------------

def test_extraction_exception_returns_500_without_leaking_its_message(monkeypatch, caplog):
    def raising_extract_text(path):
        raise ValueError("corrupt PDF for candidate jean.dupont@example.com")

    monkeypatch.setattr("pipeline.text_extractor.extract_text", raising_extract_text)

    client = _client()
    try:
        with caplog.at_level("ERROR"):
            resp = _upload(client)
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 500
    assert "jean.dupont@example.com" not in resp.text
    assert "jean.dupont@example.com" not in caplog.text


def test_store_candidate_exception_message_never_reaches_logs_or_response(monkeypatch, caplog):
    async def fake_parse_resume_text(llm, raw_text, file_meta=None, *, model=None):
        return ParseResult(data=_VALID_CANDIDATE_JSON, success=True, attempts=1)

    monkeypatch.setattr("pipeline.parsing_agent.parse_resume_text", fake_parse_resume_text)

    def raising_store_candidate(db, candidate_json, raw_text):
        raise RuntimeError("duplicate key value violates unique constraint: jean.dupont@example.com")

    monkeypatch.setattr(api, "store_candidate", raising_store_candidate)

    client = _client()
    try:
        with caplog.at_level("ERROR"):
            resp = _upload(client)
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 500
    assert "jean.dupont@example.com" not in resp.text
    assert "jean.dupont@example.com" not in caplog.text


# ---------------------------------------------------------------------------
# GET /v1/candidates/{candidate_id}/contact -- service-key auth
# ---------------------------------------------------------------------------

class _FakeCandidate:
    def __init__(self, id, name, email, phone):
        self.id = id
        self.name = name
        self.email = email
        self.phone = phone


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _FakeSession:
    def __init__(self, result=None):
        self._result = result

    def query(self, model):
        return _FakeQuery(self._result)


def _contact_client(result):
    api.app.dependency_overrides[api.get_db] = lambda: _FakeSession(result)
    return TestClient(api.app)


def test_contact_lookup_without_api_key_returns_401():
    client = _contact_client(None)
    try:
        resp = client.get(f"/v1/candidates/{uuid.uuid4()}/contact")
    finally:
        api.app.dependency_overrides.clear()
    assert resp.status_code == 401


def test_contact_lookup_with_wrong_api_key_returns_401():
    client = _contact_client(None)
    try:
        resp = client.get(f"/v1/candidates/{uuid.uuid4()}/contact", headers={"x-api-key": "wrong-key"})
    finally:
        api.app.dependency_overrides.clear()
    assert resp.status_code == 401


def test_contact_lookup_with_correct_key_and_unknown_id_returns_404():
    client = _contact_client(None)
    try:
        resp = client.get(
            f"/v1/candidates/{uuid.uuid4()}/contact",
            headers={"x-api-key": api.settings.service_api_key},
        )
    finally:
        api.app.dependency_overrides.clear()
    assert resp.status_code == 404


def test_contact_lookup_with_correct_key_and_known_id_returns_contact():
    candidate_id = uuid.uuid4()
    fake = _FakeCandidate(id=candidate_id, name="Jean Dupont", email="jean@example.com", phone="+33612345678")
    client = _contact_client(fake)
    try:
        resp = client.get(
            f"/v1/candidates/{candidate_id}/contact",
            headers={"x-api-key": api.settings.service_api_key},
        )
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate_id"] == str(candidate_id)
    assert body["name"] == "Jean Dupont"
    assert body["email"] == "jean@example.com"
    assert body["phone"] == "+33612345678"


def test_contact_lookup_with_malformed_candidate_id_returns_422_not_500():
    client = _contact_client(None)
    try:
        resp = client.get(
            "/v1/candidates/not-a-uuid/contact",
            headers={"x-api-key": api.settings.service_api_key},
        )
    finally:
        api.app.dependency_overrides.clear()
    assert resp.status_code == 422


class _FakeCandidateRow:
    def __init__(self, id, name, email, phone, raw_text, candidate_json, created_at):
        self.id = id
        self.name = name
        self.email = email
        self.phone = phone
        self.raw_text = raw_text
        self.candidate_json = candidate_json
        self.created_at = created_at


def _candidates_client(rows, monkeypatch):
    # get_candidates calls list_candidates(db) directly (imported by name into api's
    # own namespace) -- overriding get_db alone wouldn't stand in for the real query,
    # so list_candidates itself is monkeypatched, the same boundary
    # test_contact_lookup_* mocks at (db.query(...).filter(...).first()) one level down.
    monkeypatch.setattr(api, "list_candidates", lambda db: rows)
    api.app.dependency_overrides[api.get_db] = lambda: _FakeSession(None)
    return TestClient(api.app)


def test_candidates_list_without_api_key_returns_401(monkeypatch):
    client = _candidates_client([], monkeypatch)
    try:
        resp = client.get("/v1/candidates")
    finally:
        api.app.dependency_overrides.clear()
    assert resp.status_code == 401


def test_candidates_list_with_wrong_api_key_returns_401(monkeypatch):
    client = _candidates_client([], monkeypatch)
    try:
        resp = client.get("/v1/candidates", headers={"x-api-key": "wrong-key"})
    finally:
        api.app.dependency_overrides.clear()
    assert resp.status_code == 401


def test_candidates_list_empty_returns_empty_array(monkeypatch):
    client = _candidates_client([], monkeypatch)
    try:
        resp = client.get("/v1/candidates", headers={"x-api-key": api.settings.service_api_key})
    finally:
        api.app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == []


def test_candidates_list_returns_only_approved_fields_in_provided_order(monkeypatch):
    rows = [
        _FakeCandidateRow(
            id=uuid.uuid4(),
            name="Newer Candidate",
            email="newer@example.com",
            phone="+33600000001",
            raw_text="this must never appear in the response",
            candidate_json={"basics": {"email": "newer@example.com"}},
            created_at="2026-01-02T00:00:00+00:00",
        ),
        _FakeCandidateRow(
            id=uuid.uuid4(),
            name="Older Candidate",
            email=None,
            phone=None,
            raw_text="this must never appear either",
            candidate_json={"basics": {}},
            created_at="2026-01-01T00:00:00+00:00",
        ),
    ]
    client = _candidates_client(rows, monkeypatch)
    try:
        resp = client.get("/v1/candidates", headers={"x-api-key": api.settings.service_api_key})
    finally:
        api.app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    # The endpoint renders list_candidates()'s own order verbatim -- ordering itself
    # (newest-first) is list_candidates()'s responsibility and is covered against a
    # real Postgres in tests/test_db.py::TestListCandidates.
    assert [c["candidate_id"] for c in body] == [str(rows[0].id), str(rows[1].id)]

    first = body[0]
    assert first["name"] == "Newer Candidate"
    assert first["email"] == "newer@example.com"
    assert set(first.keys()) == {"candidate_id", "name", "email", "created_at"}
    assert "phone" not in first
    assert "raw_text" not in first
    assert "candidate_json" not in first

    second = body[1]
    assert second["email"] is None

