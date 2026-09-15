import os
import sqlite3
import tempfile

import pytest

from backend.database import init_db


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def test_init_db_creates_file(temp_db, monkeypatch):
    import backend.database
    monkeypatch.setattr(backend.database, "DB_PATH", temp_db)
    init_db()
    assert os.path.exists(temp_db)


def test_init_db_creates_interviews_table(temp_db, monkeypatch):
    import backend.database
    monkeypatch.setattr(backend.database, "DB_PATH", temp_db)
    init_db()
    conn = sqlite3.connect(temp_db)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='interviews'")
    assert cursor.fetchone() is not None
    conn.close()


def test_init_db_creates_recordings_table(temp_db, monkeypatch):
    import backend.database
    monkeypatch.setattr(backend.database, "DB_PATH", temp_db)
    init_db()
    conn = sqlite3.connect(temp_db)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='recordings'")
    assert cursor.fetchone() is not None
    conn.close()


def test_init_db_creates_reports_table(temp_db, monkeypatch):
    import backend.database
    monkeypatch.setattr(backend.database, "DB_PATH", temp_db)
    init_db()
    conn = sqlite3.connect(temp_db)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reports'")
    assert cursor.fetchone() is not None
    conn.close()


def test_interviews_table_schema(temp_db, monkeypatch):
    import backend.database
    monkeypatch.setattr(backend.database, "DB_PATH", temp_db)
    init_db()
    conn = sqlite3.connect(temp_db)
    cursor = conn.execute("PRAGMA table_info(interviews)")
    columns = {row[1] for row in cursor.fetchall()}
    expected = {"id", "token", "candidate_name", "candidate_email", "language", "status",
                "retry_count", "created_at", "expires_at", "started_at", "completed_at",
                "invite_sent", "external_reference_id", "idempotency_key",
                "webhook_delivery_id", "webhook_delivered_at", "job_title",
                "invite_email_sent_at", "invite_email_error",
                "pending_transcript", "pending_duration_seconds", "finalized_at"}
    assert columns == expected
    conn.close()


def test_recordings_table_schema(temp_db, monkeypatch):
    import backend.database
    monkeypatch.setattr(backend.database, "DB_PATH", temp_db)
    init_db()
    conn = sqlite3.connect(temp_db)
    cursor = conn.execute("PRAGMA table_info(recordings)")
    columns = {row[1] for row in cursor.fetchall()}
    expected = {"id", "interview_id", "recording_path", "transcript_path",
                "duration_seconds", "ended_reason", "raw_transcript"}
    assert columns == expected
    conn.close()


def test_reports_table_schema(temp_db, monkeypatch):
    import backend.database
    monkeypatch.setattr(backend.database, "DB_PATH", temp_db)
    init_db()
    conn = sqlite3.connect(temp_db)
    cursor = conn.execute("PRAGMA table_info(reports)")
    columns = {row[1] for row in cursor.fetchall()}
    expected = {"id", "interview_id", "report_token", "overall_score", "cefr_level", "confidence",
                "inconclusive", "fluency_score", "vocabulary_score", "grammar_score",
                "pronunciation_score", "comprehension_score", "interaction_score", "strengths", "concerns",
                "evidence", "candidate_speaking_time", "word_count", "evaluation_status",
                "evaluation_error", "created_at"}
    assert columns == expected
    conn.close()


def test_init_db_idempotent(temp_db, monkeypatch):
    import backend.database
    monkeypatch.setattr(backend.database, "DB_PATH", temp_db)
    init_db()
    init_db()
    conn = sqlite3.connect(temp_db)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "interviews" in tables
    assert "recordings" in tables
    assert "reports" in tables
    conn.close()
