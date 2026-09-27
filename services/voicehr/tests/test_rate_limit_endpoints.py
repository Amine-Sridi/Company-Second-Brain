"""
tests/test_rate_limit_endpoints.py
-----------------------------------
Rate limits on VoiceHR's two unauthenticated routes.

Both use a token as the credential, which is sound -- a uuid4 is 122 bits and not
guessable. The gap was that an unlimited endpoint still lets an attacker probe freely,
and each probe costs a database round-trip. `/start` additionally *mutates* state
(pending -> active), so a hit there is not merely a read.

These assert the limit trips and, just as importantly, that a limited endpoint still
answers legitimate traffic normally below the threshold.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta

import pytest

from backend.api import deps as api_deps
from backend.api.routes import report as report_routes


@pytest.fixture(autouse=True)
def _clear_limiters():
    """Module-level singletons -- reset around every test so order cannot matter."""
    report_routes._report_limiter.reset()
    api_deps._token_probe_limiter.reset()
    yield
    report_routes._report_limiter.reset()
    api_deps._token_probe_limiter.reset()


def _seed_interview(db_path: str, status: str = "pending") -> str:
    token = str(uuid.uuid4())
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO interviews
               (token, candidate_name, candidate_email, language, status, created_at,
                expires_at, external_reference_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (token, "Rate Limit Candidate", "rl@example.com", "en", status,
             now.isoformat(), (now + timedelta(days=7)).isoformat(), str(uuid.uuid4())),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def test_report_endpoint_serves_normal_traffic(client):
    """Below the threshold nothing changes -- an unknown token is still a plain 404."""
    for _ in range(5):
        resp = client.get(f"/api/report/{uuid.uuid4()}")
        assert resp.status_code == 404, resp.text


def test_report_endpoint_blocks_probing(client):
    """An attacker enumerating report tokens gets cut off rather than given unlimited
    cheap attempts."""
    limit = report_routes._report_limiter.limit
    for _ in range(limit):
        client.get(f"/api/report/{uuid.uuid4()}")

    resp = client.get(f"/api/report/{uuid.uuid4()}")
    assert resp.status_code == 429, resp.text
    assert int(resp.headers["Retry-After"]) > 0


def test_report_limit_is_per_client(client):
    """One noisy client must not lock out everyone else."""
    limit = report_routes._report_limiter.limit
    for _ in range(limit + 1):
        client.get(f"/api/report/{uuid.uuid4()}", headers={"X-Forwarded-For": "198.51.100.1"})

    other = client.get(f"/api/report/{uuid.uuid4()}", headers={"X-Forwarded-For": "203.0.113.9"})
    assert other.status_code == 404, "a different client should be unaffected"


def test_interview_start_blocks_probing(client):
    limit = api_deps._token_probe_limiter.limit
    for _ in range(limit):
        client.post(f"/api/interview/{uuid.uuid4()}/start")

    resp = client.post(f"/api/interview/{uuid.uuid4()}/start")
    assert resp.status_code == 429, resp.text
    assert int(resp.headers["Retry-After"]) > 0


def test_a_real_interview_still_starts(client, test_db_path):
    """The gate must not break the actual candidate flow."""
    token = _seed_interview(test_db_path)
    resp = client.post(f"/api/interview/{token}/start")
    assert resp.status_code == 200, resp.text
    assert resp.json()["ws_url"].endswith(f"/ws/interview/{token}")
