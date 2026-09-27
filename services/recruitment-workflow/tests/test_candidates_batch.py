"""
tests/test_candidates_batch.py
-------------------------------
`clients.get_candidates_batch` -- the single call that replaced submit_job's
per-candidate loop (audit M2: N sequential cross-service round-trips, made while this
request's DB transaction was already open).

Pinned here because the batch call has to preserve the *tolerances* the old loop had, not
just its happy path. The loop skipped a candidate Second Brain returned 404 for, since
semantic retrieval can name a candidate deleted moments later; the batch endpoint
expresses that as an omitted entry, and the caller must treat the two identically. A
regression that turned a stale id into a failed job submission would be a real outage
triggered by ordinary data churn.

No network: `call_service` is substituted.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

import clients
from clients import SecondBrainClientError


def _resp(status_code: int, body=None, text=""):
    return SimpleNamespace(
        status_code=status_code, json=lambda: (body if body is not None else {}), text=text
    )


def _run(coro):
    return asyncio.run(coro)


def test_returns_a_mapping_keyed_by_candidate_id(monkeypatch):
    a, b = uuid.uuid4(), uuid.uuid4()
    captured = {}

    async def fake_call(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _resp(200, {"candidates": [
            {"candidate_id": str(a), "skills": ["Python"], "years_of_experience": 5.0,
             "job_titles": ["Backend Engineer"], "raw_text": "x", "source_version": "1"},
            {"candidate_id": str(b), "skills": ["Go"], "years_of_experience": 2.0,
             "job_titles": ["SRE"], "raw_text": "y", "source_version": "1"},
        ]})

    monkeypatch.setattr(clients, "call_service", fake_call)
    out = _run(clients.get_candidates_batch("http://sb", "key", [a, b]))

    assert set(out) == {a, b}
    assert out[a]["skills"] == ["Python"]
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/knowledge/candidates/batch")
    assert captured["json"] == {"candidate_ids": [str(a), str(b)]}


def test_one_call_is_made_for_many_candidates(monkeypatch):
    """The entire point of the change: N candidates, one round-trip."""
    ids = [uuid.uuid4() for _ in range(20)]
    calls = {"n": 0}

    async def fake_call(method, url, **kwargs):
        calls["n"] += 1
        return _resp(200, {"candidates": [
            {"candidate_id": str(i), "skills": [], "years_of_experience": None,
             "job_titles": [], "raw_text": "x", "source_version": "1"} for i in ids
        ]})

    monkeypatch.setattr(clients, "call_service", fake_call)
    out = _run(clients.get_candidates_batch("http://sb", "key", ids))

    assert len(out) == 20
    assert calls["n"] == 1, "20 candidates must cost exactly one HTTP round-trip"


def test_missing_ids_are_omitted_not_errors(monkeypatch):
    """Retrieval can name a candidate deleted a moment later. The old loop skipped it on
    404; the batch path must skip it too rather than fail the whole submission."""
    present, deleted = uuid.uuid4(), uuid.uuid4()

    async def fake_call(method, url, **kwargs):
        return _resp(200, {"candidates": [
            {"candidate_id": str(present), "skills": ["Python"], "years_of_experience": 1.0,
             "job_titles": [], "raw_text": "x", "source_version": "1"},
        ]})

    monkeypatch.setattr(clients, "call_service", fake_call)
    out = _run(clients.get_candidates_batch("http://sb", "key", [present, deleted]))

    assert present in out
    assert deleted not in out, "an unknown id must simply be absent"


def test_empty_input_makes_no_call(monkeypatch):
    async def fake_call(method, url, **kwargs):
        raise AssertionError("no request should be made for an empty id list")

    monkeypatch.setattr(clients, "call_service", fake_call)
    assert _run(clients.get_candidates_batch("http://sb", "key", [])) == {}


def test_non_200_raises_second_brain_error(monkeypatch):
    async def fake_call(method, url, **kwargs):
        return _resp(500, text="boom")

    monkeypatch.setattr(clients, "call_service", fake_call)
    with pytest.raises(SecondBrainClientError):
        _run(clients.get_candidates_batch("http://sb", "key", [uuid.uuid4()]))


def test_a_malformed_row_does_not_sink_the_whole_batch(monkeypatch):
    """One bad row degrades to 'that candidate is skipped', which the caller already
    handles -- rather than failing a submission that had 19 good candidates."""
    good = uuid.uuid4()

    async def fake_call(method, url, **kwargs):
        return _resp(200, {"candidates": [
            {"candidate_id": "not-a-uuid", "skills": []},
            {"skills": ["no id at all"]},
            {"candidate_id": str(good), "skills": ["Python"], "years_of_experience": 3.0,
             "job_titles": [], "raw_text": "x", "source_version": "1"},
        ]})

    monkeypatch.setattr(clients, "call_service", fake_call)
    out = _run(clients.get_candidates_batch("http://sb", "key", [good]))
    assert set(out) == {good}
