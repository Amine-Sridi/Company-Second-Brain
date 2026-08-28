import os
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def test_db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


TEST_RWS_SERVICE_KEY = "test-rws-service-key"
TEST_RWS_WEBHOOK_HMAC_SECRET = "test-rws-webhook-secret"
TEST_ADMIN_USERNAME = "test-admin"
TEST_ADMIN_PASSWORD = "test-admin-password"


@pytest.fixture
def mock_settings():
    return SimpleNamespace(
        gemini_api_key=None,
        resend_api_key="test_resend_key",
        admin_email="admin@test.com",
        admin_username=TEST_ADMIN_USERNAME,
        admin_password=TEST_ADMIN_PASSWORD,
        base_url="http://testserver",
        rws_service_api_key=TEST_RWS_SERVICE_KEY,
        rws_webhook_hmac_secret=TEST_RWS_WEBHOOK_HMAC_SECRET,
        # None by default: existing tests that don't care about the outbound webhook
        # get the early-return no-op path in notify_rws_interview_result for free.
        rws_webhook_url=None,
    )


@pytest.fixture
def client(monkeypatch, test_db_path, mock_settings):
    import backend.database
    import backend.api.deps
    import backend.api.routes.invite
    import backend.api.routes.interview
    import backend.api.routes.report
    import backend.core.config
    import backend.services.evaluator
    import backend.services.mailer
    import backend.services.rws_client
    import backend.services.processing

    monkeypatch.setattr(backend.database, "DB_PATH", test_db_path)
    monkeypatch.setattr(backend.api.deps, "DB_PATH", test_db_path)

    monkeypatch.setattr(backend.core.config, "settings", mock_settings)
    monkeypatch.setattr(backend.api.deps, "settings", mock_settings)
    monkeypatch.setattr(backend.api.routes.invite, "settings", mock_settings)
    monkeypatch.setattr(backend.api.routes.interview, "settings", mock_settings)
    monkeypatch.setattr(backend.services.evaluator, "settings", mock_settings)
    monkeypatch.setattr(backend.services.mailer, "settings", mock_settings)
    monkeypatch.setattr(backend.services.rws_client, "settings", mock_settings)
    monkeypatch.setattr(backend.services.processing, "settings", mock_settings)

    from backend.main import app
    from backend.api.deps import get_db

    def override_get_db():
        conn = sqlite3.connect(test_db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def db(test_db_path):
    conn = sqlite3.connect(test_db_path)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def create_interview(db, token="test-token", name="Test User", email="test@example.com",
                     language="en", status="pending", expires_at=None, retry_count=0,
                     external_reference_id=None, idempotency_key=None):
    from datetime import datetime, timedelta
    if expires_at is None:
        expires_at = (datetime.utcnow() + timedelta(days=7)).isoformat()

    db.execute(
        """INSERT INTO interviews (token, candidate_name, candidate_email, language, status,
           created_at, expires_at, retry_count, external_reference_id, idempotency_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (token, name, email, language, status, datetime.utcnow().isoformat(), expires_at, retry_count,
         external_reference_id, idempotency_key),
    )
    db.commit()
    return token


def create_report(db, interview_id, report_token="report-token", overall_score=72, cefr_level="B2",
                  confidence="medium", inconclusive=0):
    from datetime import datetime
    import json

    db.execute(
        """INSERT INTO reports (interview_id, report_token, overall_score, cefr_level, confidence,
           inconclusive, fluency_score, vocabulary_score, grammar_score, pronunciation_score,
           comprehension_score, strengths, concerns, evidence, candidate_speaking_time,
           word_count, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (interview_id, report_token, overall_score, cefr_level, confidence, inconclusive,
         70, 75, 68, 74, 80, json.dumps([]), json.dumps([]), json.dumps([]),
         420, 1200, datetime.utcnow().isoformat()),
    )
    db.commit()
    return report_token
