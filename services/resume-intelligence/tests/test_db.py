"""
tests/test_db.py
-------------------
Integration tests for db.py's normalize_email/store_candidate against a real
Postgres -- the normalized-email dedup rule and the no-email-always-inserts
rule are genuine SQL/constraint behavior that no mock can honestly stand in
for, so these tests run against the real resume_intel schema rather than a
fake session.

Skips cleanly if DATABASE_URL is unset or unreachable. On a bare host run in
this project's sandbox that is typically the case (the Postgres container's
mapped host port does not accept the credentials this service's own .env
carries); run these from inside the container instead:

    docker compose -f infra/docker-compose.yml run --rm resume-intelligence \\
        python -m pytest tests/test_db.py -v

Every row this file creates is deleted again in its own fixture teardown, by
the specific id(s) that test created -- never a blanket DELETE.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest
import sqlalchemy

import db


def _db_reachable() -> bool:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return False
    try:
        engine = sqlalchemy.create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        return True
    except Exception:
        return False


_needs_db = pytest.mark.skipif(
    not _db_reachable(),
    reason="DATABASE_URL not set or Postgres not reachable from this test run",
)


@pytest.fixture
def session():
    s = db._get_session_factory()()
    created_ids: list = []
    try:
        yield s, created_ids
    finally:
        for candidate_id in created_ids:
            s.query(db.Candidate).filter(db.Candidate.id == candidate_id).delete()
        s.commit()
        s.close()


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


class TestNormalizeEmail:
    def test_strips_and_lowercases(self):
        assert db.normalize_email("  Jean.Dupont@Example.com  ") == "jean.dupont@example.com"

    def test_none_stays_none(self):
        assert db.normalize_email(None) is None

    def test_empty_string_is_none(self):
        assert db.normalize_email("") is None


@_needs_db
class TestStoreCandidateDedup:
    def test_same_email_different_casing_and_whitespace_dedups_to_one_row(self, session):
        s, created_ids = session
        email = _unique_email()
        first = {"basics": {"first_name": "A", "last_name": "One", "email": email, "phone": None}}
        retry = {"basics": {"first_name": "B", "last_name": "Two", "email": f"  {email.upper()}  ", "phone": None}}

        id1, is_new1 = db.store_candidate(s, first, "raw text 1")
        created_ids.append(id1)
        id2, is_new2 = db.store_candidate(s, retry, "raw text 2")

        assert is_new1 is True
        assert is_new2 is False
        assert id1 == id2

        # dedup never overwrites the existing row's data
        stored = s.query(db.Candidate).filter(db.Candidate.id == id1).one()
        assert stored.name == "A One"

    def test_missing_email_always_creates_a_new_row(self, session):
        s, created_ids = session
        no_email = {"basics": {"first_name": "No", "last_name": "Email", "email": None, "phone": None}}

        id1, is_new1 = db.store_candidate(s, no_email, "raw text 1")
        created_ids.append(id1)
        id2, is_new2 = db.store_candidate(s, no_email, "raw text 2")
        created_ids.append(id2)

        assert is_new1 is True
        assert is_new2 is True
        assert id1 != id2

    def test_stored_candidate_json_round_trips(self, session):
        s, created_ids = session
        email = _unique_email()
        candidate_json = {
            "basics": {"first_name": "Jean", "last_name": "Dupont", "email": email, "phone": "+33612345678"},
            "work": [{"company": "Acme", "position": "Engineer"}],
        }

        candidate_id, is_new = db.store_candidate(s, candidate_json, "raw resume text")
        created_ids.append(candidate_id)

        stored = s.query(db.Candidate).filter(db.Candidate.id == candidate_id).one()
        assert is_new is True
        assert stored.email == email
        assert stored.email_normalized == email.lower()
        assert stored.candidate_json["work"][0]["company"] == "Acme"
        assert stored.raw_text == "raw resume text"
