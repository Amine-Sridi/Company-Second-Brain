"""
tests/test_report_token_expiry.py
----------------------------------
Audit finding M10(2): `GET /api/report/{report_token}` is an **unauthenticated bearer
URL** that returns candidate_name, candidate_email, every score and the full transcript.
The token is a uuid4 (genuinely unguessable -- not the problem), but it never expired and
could not be revoked, and it is emailed to ADMIN_EMAIL on completion. A mailbox breach
years later still yielded a live PII endpoint.

These tests pin two things that must hold together:
  1. the public bearer URL expires, consistent with the interview link's 7 days;
  2. the *authenticated* surfaces (admin Basic auth, and the RWS service-key lookup)
     are NOT subject to that expiry -- a recruiter must still be able to read an old
     report. The expiry is a property of the bearer URL, not of the report.

Point 2 is the one worth guarding: the obvious implementation (putting the check inside
the shared report builder) silently breaks the admin list for every older report.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient


# Uses the suite's own `client` fixture (tests/conftest.py), which patches DB_PATH in
# BOTH backend.database and backend.api.deps -- deps.py imports the value, so patching
# only the former leaves the app talking to the real database.


def _db_path(client) -> str:
    import backend.api.deps

    return backend.api.deps.DB_PATH


def _seed_report(db_path: str, *, report_age_days: float) -> str:
    """Inserts one completed interview + report whose report row is `report_age_days` old."""
    token = str(uuid.uuid4())
    now = datetime.utcnow()
    created = (now - timedelta(days=report_age_days)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO interviews
               (token, candidate_name, candidate_email, language, status, created_at,
                expires_at, external_reference_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), "Benchmark Candidate", "candidate.benchmark@example.com",
             "en", "completed", created,
             (now + timedelta(days=7)).isoformat(), str(uuid.uuid4())),
        )
        interview_id = cur.lastrowid
        cur.execute(
            """INSERT INTO reports
               (interview_id, report_token, overall_score, cefr_level, confidence,
                evaluation_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (interview_id, token, 78, "B2", "High", "completed", created),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def test_fresh_report_token_is_served(client):
    token = _seed_report(_db_path(client), report_age_days=1)
    resp = client.get(f"/api/report/{token}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["cefr_level"] == "B2"


def test_expired_report_token_is_rejected(client):
    """8 days old, against a 7-day TTL."""
    token = _seed_report(_db_path(client), report_age_days=8)
    resp = client.get(f"/api/report/{token}")
    assert resp.status_code == 410, resp.text
    assert "expired" in resp.text.lower()


def test_expired_report_leaks_no_pii_in_the_error(client):
    """A refusal must not become a disclosure channel."""
    token = _seed_report(_db_path(client), report_age_days=30)
    resp = client.get(f"/api/report/{token}")
    assert resp.status_code == 410
    body = resp.text.lower()
    assert "candidate.benchmark@example.com" not in body
    assert "benchmark candidate" not in body


def test_unknown_token_is_404_not_410(client):
    """An unknown token must stay 404 -- 410 would confirm the token once existed."""
    resp = client.get(f"/api/report/{uuid.uuid4()}")
    assert resp.status_code == 404, resp.text


def test_admin_surface_still_serves_an_expired_report(client):
    """The expiry governs the bearer URL, not the report. A recruiter with real
    credentials must still be able to read an old interview."""
    from backend.core.config import settings

    _seed_report(_db_path(client), report_age_days=400)
    resp = client.get(
        "/api/admin/reports", auth=(settings.admin_username, settings.admin_password)
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["reports"]) == 1, "an aged report vanished from the admin list"
