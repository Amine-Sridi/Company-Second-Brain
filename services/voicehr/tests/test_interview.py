from datetime import datetime, timedelta
from unittest.mock import patch

from tests.conftest import create_interview


def test_get_interview_success(client, db):
    token = create_interview(db, token="test-token", name="John Doe", language="en")
    response = client.get(f"/api/interview/{token}")
    assert response.status_code == 200
    data = response.json()
    assert data["candidate_name"] == "John Doe"
    assert data["language"] == "en"


def test_get_interview_invalid_token(client, db):
    response = client.get("/api/interview/invalid-token")
    assert response.status_code == 404


def test_get_interview_expired(client, db):
    expired = (datetime.utcnow() - timedelta(days=1)).isoformat()
    token = create_interview(db, token="expired-token", expires_at=expired)
    response = client.get(f"/api/interview/{token}")
    assert response.status_code == 410


def test_get_interview_completed(client, db):
    token = create_interview(db, token="completed-token", status="completed")
    response = client.get(f"/api/interview/{token}")
    assert response.status_code == 409


def test_start_interview_en(client, db):
    token = create_interview(db, token="test-token", language="en")
    response = client.post(f"/api/interview/{token}/start")
    assert response.status_code == 200
    data = response.json()
    assert data["ws_url"] == "ws://testserver/ws/interview/test-token"
    # mock_settings.gemini_api_key is None -- live_audio honestly reflects that.
    assert data["live_audio"] is False

    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    assert row["status"] == "active"
    assert row["started_at"] is not None


def test_start_interview_fr(client, db):
    token = create_interview(db, token="test-token", language="fr")
    response = client.post(f"/api/interview/{token}/start")
    assert response.status_code == 200
    data = response.json()
    assert data["ws_url"] == "ws://testserver/ws/interview/test-token"


def test_start_interview_live_audio_true_when_gemini_configured(client, db):
    token = create_interview(db, token="live-token", language="en")
    with patch("backend.api.routes.interview.settings.gemini_api_key", "test-key"):
        response = client.post(f"/api/interview/{token}/start")
    assert response.status_code == 200
    assert response.json()["live_audio"] is True


def test_prepare_interview_returns_ready(client, db):
    """Gemini Live needs no separate warm-up step -- /prepare just confirms the
    token is valid."""
    token = create_interview(db, token="prepare-token", language="fr")
    response = client.post(f"/api/interview/{token}/prepare")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "language": "fr", "live": True}


def test_start_interview_invalid_token(client, db):
    response = client.post("/api/interview/invalid-token/start")
    assert response.status_code == 404


def test_start_interview_expired(client, db):
    expired = (datetime.utcnow() - timedelta(days=1)).isoformat()
    token = create_interview(db, token="expired-token", expires_at=expired)
    response = client.post(f"/api/interview/{token}/start")
    assert response.status_code == 410


def test_start_interview_already_active(client, db):
    token = create_interview(db, token="active-token", status="active")
    response = client.post(f"/api/interview/{token}/start")
    assert response.status_code == 409


def test_start_interview_recovers_stale_active_session(client, db):
    token = create_interview(db, token="stale-token", status="active")
    db.execute(
        "UPDATE interviews SET started_at = ? WHERE token = ?",
        ((datetime.utcnow() - timedelta(minutes=2)).isoformat(), token),
    )
    db.commit()

    response = client.post(f"/api/interview/{token}/start")
    assert response.status_code == 200
    assert db.execute(
        "SELECT status FROM interviews WHERE token = ?", (token,)
    ).fetchone()["status"] == "active"


def test_complete_interview(client, db):
    token = create_interview(db, token="test-token", status="active")
    response = client.post(f"/api/interview/{token}/complete")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    assert row["completed_at"] is not None


def test_complete_interview_invalid_token(client, db):
    response = client.post("/api/interview/invalid-token/complete")
    assert response.status_code == 404


def test_complete_interview_not_started(client, db):
    token = create_interview(db, token="pending-token")
    response = client.post(f"/api/interview/{token}/complete")
    assert response.status_code == 409


def test_complete_interview_is_idempotent(client, db):
    token = create_interview(db, token="completed-token", status="completed")
    response = client.post(f"/api/interview/{token}/complete")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
