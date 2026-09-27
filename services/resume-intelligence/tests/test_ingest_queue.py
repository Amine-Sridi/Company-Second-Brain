"""
tests/test_ingest_queue.py
---------------------------
The queued ingestion path (`POST /v1/resumes/jobs`, `GET /v1/resumes/jobs/{id}`).

The properties pinned here are the ones that make a queue safe to put in front of a
hiring pipeline, rather than the ones that merely make it work on a good day:

  * a CV is never lost because Redis was down -- the durable row is written and
    committed BEFORE anything is enqueued;
  * only an opaque job id crosses into Redis -- never the file, never any extracted
    field (CLAUDE.md's PII boundary);
  * job state is read from Postgres, not from the broker;
  * the queue is at-least-once, so a redelivered job must not re-pay for a Gemini call.

No Redis and no Postgres are required: the queue client and the DB layer are both
substituted.
"""
from __future__ import annotations

import io
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api  # noqa: E402


class _FakePool:
    """Stands in for the ARQ redis pool, recording exactly what was enqueued."""

    enqueued: list[tuple] = []
    fail = False
    closed = False

    async def enqueue_job(self, name, *args):
        if _FakePool.fail:
            raise ConnectionError("redis down")
        _FakePool.enqueued.append((name, args))
        return SimpleNamespace(job_id="fake")

    async def close(self):
        _FakePool.closed = True


@pytest.fixture
def client(monkeypatch, tmp_path):
    _FakePool.enqueued = []
    _FakePool.fail = False
    created: dict = {}

    monkeypatch.setattr(api.settings, "upload_dir", str(tmp_path / "uploads"))

    def fake_create_ingest_job(db, upload_path, original_filename):
        job = SimpleNamespace(
            id=uuid.uuid4(), status="queued", upload_path=upload_path,
            original_filename=original_filename, candidate_id=None, is_new=None,
            error=None, attempts=0,
        )
        created["job"] = job
        return job

    def fake_get_ingest_job(db, job_id):
        job = created.get("job")
        return job if job is not None and job.id == job_id else None

    import db as db_module
    monkeypatch.setattr(db_module, "create_ingest_job", fake_create_ingest_job)
    monkeypatch.setattr(db_module, "get_ingest_job", fake_get_ingest_job)

    async def fake_create_pool(*a, **k):
        return _FakePool()

    # Patch the attribute on the real package, not the package itself: api.py also does
    # `from arq.connections import RedisSettings`, and replacing sys.modules["arq"] with
    # a stand-in leaves no real submodule for that import to resolve.
    import arq as arq_module
    monkeypatch.setattr(arq_module, "create_pool", fake_create_pool)

    def fake_get_db():
        yield SimpleNamespace(commit=lambda: None, rollback=lambda: None,
                              add=lambda *a, **k: None, refresh=lambda *a, **k: None)

    api.app.dependency_overrides[api.get_db] = fake_get_db
    with TestClient(api.app) as c:
        c.created = created
        yield c
    api.app.dependency_overrides.clear()


HEADERS = {"X-Api-Key": api.settings.service_api_key}


def _upload(client, name="cv.pdf", content=b"%PDF-1.4 fake"):
    return client.post(
        "/v1/resumes/jobs",
        files={"file": (name, io.BytesIO(content), "application/pdf")},
        headers=HEADERS,
    )


def test_upload_is_accepted_and_queued(client):
    resp = _upload(client)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["candidate_id"] is None
    assert uuid.UUID(body["job_id"])


def test_only_the_job_id_reaches_redis(client):
    """The PII boundary: the file and everything extracted from it stay in Resume
    Intelligence. Redis sees an opaque id and nothing else."""
    resp = _upload(client, content=b"%PDF-1.4 Jane Doe jane@example.com +33 1 23 45 67 89")
    job_id = resp.json()["job_id"]

    assert len(_FakePool.enqueued) == 1
    name, args = _FakePool.enqueued[0]
    assert name == "ingest_resume_job"
    assert args == (job_id,), "only the opaque job id may be enqueued"
    payload = repr(_FakePool.enqueued)
    for pii in ("Jane", "jane@example.com", "+33", "cv.pdf"):
        assert pii not in payload


def test_the_file_is_written_before_enqueue(client):
    """The worker reads the upload from the shared volume, so it must exist by the time
    the job is visible to any worker."""
    resp = _upload(client, content=b"%PDF-1.4 payload")
    job = client.created["job"]
    assert Path(job.upload_path).exists()
    assert Path(job.upload_path).read_bytes() == b"%PDF-1.4 payload"


def test_a_dead_queue_does_not_lose_the_cv(client):
    """Redis being down must leave a durable, re-enqueueable record -- not a lost upload.
    This is the whole reason the row is committed before the enqueue."""
    _FakePool.fail = True
    resp = _upload(client)
    assert resp.status_code == 503, resp.text
    job = client.created["job"]
    assert job is not None, "the durable row must exist even though enqueue failed"
    assert job.status == "queued"
    assert str(job.id) in resp.json()["detail"], "the caller is told which job to retry"
    assert Path(job.upload_path).exists(), "the file must survive for the retry"


def test_unsupported_file_type_is_rejected_before_any_work(client):
    resp = client.post(
        "/v1/resumes/jobs",
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
        headers=HEADERS,
    )
    assert resp.status_code == 422, resp.text
    assert _FakePool.enqueued == [], "nothing may be queued for a rejected file"


def test_status_is_read_from_postgres(client):
    resp = _upload(client)
    job_id = resp.json()["job_id"]

    job = client.created["job"]
    job.status = "succeeded"
    job.candidate_id = uuid.uuid4()
    job.is_new = True

    got = client.get(f"/v1/resumes/jobs/{job_id}", headers=HEADERS)
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "succeeded"
    assert got.json()["candidate_id"] == str(job.candidate_id)


def test_unknown_job_is_404(client):
    resp = client.get(f"/v1/resumes/jobs/{uuid.uuid4()}", headers=HEADERS)
    assert resp.status_code == 404


def test_endpoints_require_the_service_key(client):
    assert _upload_without_key(client).status_code == 401
    assert client.get(f"/v1/resumes/jobs/{uuid.uuid4()}").status_code == 401


def _upload_without_key(client):
    return client.post(
        "/v1/resumes/jobs",
        files={"file": ("cv.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )


def test_redelivered_job_does_not_rerun_a_succeeded_ingestion(monkeypatch, tmp_path):
    """ARQ is at-least-once. A duplicate delivery of a job that already succeeded must
    short-circuit rather than pay for another Gemini extraction."""
    import asyncio

    import jobs as jobs_module

    job_id = uuid.uuid4()
    done = SimpleNamespace(
        id=job_id, status="succeeded", upload_path=str(tmp_path / "x.pdf"),
        candidate_id=uuid.uuid4(), is_new=True, error=None, attempts=1,
    )

    import db as db_module
    monkeypatch.setattr(db_module, "get_ingest_job", lambda s, j: done)
    monkeypatch.setattr(db_module, "_get_session_factory", lambda: (lambda: SimpleNamespace(close=lambda: None)))

    called = {"ran": False}

    async def must_not_run(*a, **k):
        called["ran"] = True
        raise AssertionError("ingestion re-ran for an already-succeeded job")

    monkeypatch.setattr(jobs_module, "run_ingestion", must_not_run)

    result = asyncio.run(jobs_module.ingest_resume_job({}, str(job_id)))
    assert result["status"] == "already_succeeded"
    assert called["ran"] is False
