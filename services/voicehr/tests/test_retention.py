"""
tests/test_retention.py
------------------------
`backend.core.retention.sweep` -- age-based removal of candidate PII.

This is the most destructive code in the repository, so the tests are weighted towards
what it must **refuse** to touch rather than what it removes:

  * dry-run must change absolutely nothing (it is the default, and the only thing
    standing between a mistyped `--older-than-days` and irreversible deletion);
  * interviews that are still pending or active must survive regardless of age -- they
    are live work, and one that never completed may belong to a candidate about to sit it;
  * the reports row must survive, because a rejected candidate asking "why?" deserves an
    answer and deleting the decision to protect privacy trades one accountability
    problem for another.

Runs against a real temporary SQLite database, not mocks: the whole point of extracting
this out of the CLI was that an embedded SQL string could not be tested.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import backend.database as database
from backend.core.retention import REDACTED_EMAIL, REDACTED_NAME, sweep

OLD = (datetime.utcnow() - timedelta(days=200)).isoformat()
RECENT = (datetime.utcnow() - timedelta(days=2)).isoformat()
CUTOFF = (datetime.utcnow() - timedelta(days=90)).isoformat()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "retention.db"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    database.init_db()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _add(conn, tmp_path, *, created_at, status="completed", with_files=True):
    """Inserts one interview (+ recording, + files on disk) and returns its id."""
    cur = conn.execute(
        """INSERT INTO interviews
           (token, candidate_name, candidate_email, language, status, created_at,
            expires_at, external_reference_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (f"tok-{created_at}-{status}", "Real Candidate", "real@example.com", "en",
         status, created_at, created_at, "ext-ref"),
    )
    interview_id = cur.lastrowid

    rec_path = tra_path = None
    if with_files:
        rec_path = str(tmp_path / f"rec_{interview_id}.webm")
        tra_path = str(tmp_path / f"tra_{interview_id}.json")
        Path(rec_path).write_bytes(b"audio-bytes-standing-in-for-a-voice")
        Path(tra_path).write_text('{"turns": []}', encoding="utf-8")

    conn.execute(
        """INSERT INTO recordings
           (interview_id, recording_path, transcript_path, duration_seconds,
            ended_reason, raw_transcript)
           VALUES (?,?,?,?,?,?)""",
        (interview_id, rec_path, tra_path, 120, "completed",
         '[{"role":"user","text":"my name is Real Candidate"}]'),
    )
    conn.execute(
        """INSERT INTO reports (interview_id, report_token, overall_score, cefr_level,
                                confidence, evaluation_status, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (interview_id, f"rt-{interview_id}", 78, "B2", "High", "completed", created_at),
    )
    conn.commit()
    return interview_id, rec_path, tra_path


def test_dry_run_changes_nothing(db, tmp_path):
    """The default mode, and the only guard against a mistyped age argument."""
    iid, rec, tra = _add(db, tmp_path, created_at=OLD)

    report = sweep(db, CUTOFF, apply=False)
    assert report["eligible_interviews"] == 1
    assert report["recording_files"] == 1
    assert report["identities"] == 1

    assert Path(rec).exists(), "dry run must not delete files"
    assert Path(tra).exists()
    row = db.execute("SELECT candidate_name, candidate_email FROM interviews WHERE id=?", (iid,)).fetchone()
    assert row["candidate_name"] == "Real Candidate", "dry run must not redact"
    rec_row = db.execute("SELECT raw_transcript FROM recordings WHERE interview_id=?", (iid,)).fetchone()
    assert rec_row["raw_transcript"] is not None


def test_apply_removes_the_raw_pii(db, tmp_path):
    iid, rec, tra = _add(db, tmp_path, created_at=OLD)

    sweep(db, CUTOFF, apply=True)

    assert not Path(rec).exists(), "the recorded voice must be gone"
    assert not Path(tra).exists()
    rec_row = db.execute(
        "SELECT recording_path, transcript_path, raw_transcript FROM recordings WHERE interview_id=?",
        (iid,)).fetchone()
    assert rec_row["raw_transcript"] is None, "verbatim transcript text must be gone"
    assert rec_row["recording_path"] is None, "the row must stop pointing at a deleted file"
    assert rec_row["transcript_path"] is None


def test_apply_redacts_identity_without_nulling_it(db, tmp_path):
    """candidate_name is NOT NULL, and a visible marker distinguishes 'redacted on
    purpose' from 'never captured'."""
    iid, _, _ = _add(db, tmp_path, created_at=OLD)
    sweep(db, CUTOFF, apply=True)

    row = db.execute("SELECT candidate_name, candidate_email FROM interviews WHERE id=?", (iid,)).fetchone()
    assert row["candidate_name"] == REDACTED_NAME
    assert row["candidate_email"] == REDACTED_EMAIL


def test_the_decision_record_survives(db, tmp_path):
    """Deleting the scores to protect privacy would trade one accountability problem for
    another. A rejected candidate asking 'why?' deserves an answer."""
    iid, _, _ = _add(db, tmp_path, created_at=OLD)
    sweep(db, CUTOFF, apply=True)

    report_row = db.execute(
        "SELECT overall_score, cefr_level, evaluation_status FROM reports WHERE interview_id=?",
        (iid,)).fetchone()
    assert report_row["overall_score"] == 78
    assert report_row["cefr_level"] == "B2"
    assert report_row["evaluation_status"] == "completed"

    interview = db.execute(
        "SELECT status, external_reference_id FROM interviews WHERE id=?", (iid,)).fetchone()
    assert interview["status"] == "completed"
    assert interview["external_reference_id"] == "ext-ref", "RWS's link must survive"


def test_recent_interviews_are_untouched(db, tmp_path):
    iid, rec, _ = _add(db, tmp_path, created_at=RECENT)
    report = sweep(db, CUTOFF, apply=True)

    assert report["eligible_interviews"] == 0
    assert Path(rec).exists()
    row = db.execute("SELECT candidate_name FROM interviews WHERE id=?", (iid,)).fetchone()
    assert row["candidate_name"] == "Real Candidate"


@pytest.mark.parametrize("status", ["pending", "active"])
def test_unfinished_interviews_survive_regardless_of_age(db, tmp_path, status):
    """Live work. An old 'pending' row may belong to a candidate about to sit it, and an
    'active' one is mid-interview -- age alone must not make either eligible."""
    iid, rec, _ = _add(db, tmp_path, created_at=OLD, status=status)
    report = sweep(db, CUTOFF, apply=True)

    assert report["eligible_interviews"] == 0
    assert Path(rec).exists()
    row = db.execute("SELECT candidate_name FROM interviews WHERE id=?", (iid,)).fetchone()
    assert row["candidate_name"] == "Real Candidate"


def test_sweeping_twice_is_safe(db, tmp_path):
    """Files are already deleted and identity already redacted; a second run must not
    fail, and must not count the same identity again."""
    _add(db, tmp_path, created_at=OLD)
    sweep(db, CUTOFF, apply=True)

    second = sweep(db, CUTOFF, apply=True)
    assert second["eligible_interviews"] == 1, "the row is still old and finished"
    assert second["identities"] == 0, "already redacted, so nothing left to redact"
    assert second["recording_files"] == 0
    assert second["raw_transcripts"] == 0


def test_a_missing_file_is_reported_not_fatal(db, tmp_path):
    iid, rec, _ = _add(db, tmp_path, created_at=OLD)
    Path(rec).unlink()

    report = sweep(db, CUTOFF, apply=True)
    assert report["missing_files"] >= 1
    row = db.execute("SELECT recording_path FROM recordings WHERE interview_id=?", (iid,)).fetchone()
    assert row["recording_path"] is None, "the dangling pointer is still cleared"


def test_mixed_batch_only_touches_what_is_eligible(db, tmp_path):
    old_id, old_rec, _ = _add(db, tmp_path, created_at=OLD)
    new_id, new_rec, _ = _add(db, tmp_path, created_at=RECENT)
    live_id, live_rec, _ = _add(db, tmp_path, created_at=OLD, status="active")

    report = sweep(db, CUTOFF, apply=True)

    assert report["eligible_interviews"] == 1
    assert not Path(old_rec).exists()
    assert Path(new_rec).exists()
    assert Path(live_rec).exists()
