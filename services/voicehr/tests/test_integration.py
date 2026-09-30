import json
from datetime import datetime
from unittest.mock import patch, AsyncMock

import pytest

from tests.conftest import TEST_RWS_SERVICE_KEY, create_interview

_AUTH = {"x-api-key": TEST_RWS_SERVICE_KEY}


def test_full_interview_flow(client, db):
    response = client.post("/api/invite", json={
        "candidate_name": "Alice Martin",
        "candidate_email": "alice@example.com",
        "language": "en"
    }, headers=_AUTH)
    assert response.status_code == 200
    invite_data = response.json()
    token = invite_data["link"].split("token=")[1]

    response = client.get(f"/api/interview/{token}")
    assert response.status_code == 200
    interview_data = response.json()
    assert interview_data["candidate_name"] == "Alice Martin"
    assert interview_data["language"] == "en"

    response = client.post(f"/api/interview/{token}/start")
    assert response.status_code == 200
    start_data = response.json()
    assert start_data["ws_url"] == f"ws://testserver/ws/interview/{token}"

    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    assert row["status"] == "active"

    with patch("backend.services.processing.evaluate") as mock_evaluate, \
         patch("backend.services.processing.send_report_email") as mock_send_email:

        mock_evaluate.return_value = {
            "overall_score": 78,
            "cefr_level": "B2",
            "confidence": "high",
            "fluency_score": 80,
            "vocabulary_score": 75,
            "grammar_score": 72,
            "pronunciation_score": 82,
            "comprehension_score": 85,
            "strengths": [{"text": "Excellent fluency", "timestamp": "03:15"}],
            "concerns": [],
            "evidence": [{"timestamp": "03:15", "quote": "I managed the project", "dimension": "Fluency", "note": "Strong"}],
            "candidate_speaking_time": 480,
            "word_count": 1500,
            "evaluation_status": "completed",
        }
        mock_send_email.return_value = True

        from backend.services.processing import process_interview
        import asyncio

        transcript = [
            {"role": "assistant", "text": "Hello Alice", "timestamp": 0},
            # Full answers rather than one-liners: the end-to-end flow this test covers
            # ends in a graded report, and sufficiency is now measured from the
            # candidate's own language rather than the session duration (sufficiency.py).
            {"role": "user", "timestamp": 5, "text":
             "Hi Sarah, I manage the project and I have been leading that team for "
             "roughly three years, mostly coordinating delivery across two time zones."},
            {"role": "user", "timestamp": 60, "text":
             "I resolved a delivery issue last quarter where a dependency slipped, so we "
             "resequenced the remaining work and kept the original launch date."},
            {"role": "user", "timestamp": 120, "text":
             "Remote work suits our team well, provided we write decisions down and keep "
             "a few hours of deliberate overlap for the harder conversations."},
        ]

        asyncio.run(process_interview(token, transcript, "storage/recordings/x.webm", 650, "completed"))

    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    assert row["status"] == "completed"
    assert row["completed_at"] is not None

    report = db.execute("SELECT * FROM reports WHERE interview_id = ?", (row["id"],)).fetchone()
    assert report is not None
    report_token = report["report_token"]

    response = client.get(f"/api/report/{report_token}")
    assert response.status_code == 200
    report_data = response.json()
    assert report_data["candidate_name"] == "Alice Martin"
    assert report_data["overall_score"] == 78
    assert report_data["cefr_level"] == "B2"
    assert report_data["confidence"] == "High"
    assert report_data["inconclusive"] is False
    assert report_data["dimensions"]["fluency"] == 80
    assert len(report_data["strengths"]) == 1
    assert report_data["stats"]["word_count"] == 1500

    mock_send_email.assert_called_once()
    call_args = mock_send_email.call_args
    assert call_args.kwargs["candidate_name"] == "Alice Martin"
    assert call_args.kwargs["admin_email"] == "admin@test.com"


def test_expired_link_blocked(client, db):
    from datetime import timedelta
    expired = (datetime.utcnow() - timedelta(days=1)).isoformat()
    token = create_interview(db, token="expired-token", expires_at=expired)

    response = client.get(f"/api/interview/{token}")
    assert response.status_code == 410

    response = client.post(f"/api/interview/{token}/start")
    assert response.status_code == 410


def test_completed_link_blocked(client, db):
    token = create_interview(db, token="completed-token", status="completed")

    response = client.get(f"/api/interview/{token}")
    assert response.status_code == 409

    response = client.post(f"/api/interview/{token}/start")
    assert response.status_code == 409


def test_invalid_link_blocked(client, db):
    response = client.get("/api/interview/invalid-token")
    assert response.status_code == 404

    response = client.post("/api/interview/invalid-token/start")
    assert response.status_code == 404


def test_admin_can_generate_multiple_links(client, db):
    for i in range(5):
        response = client.post("/api/invite", json={
            "candidate_name": f"User {i}",
            "candidate_email": f"user{i}@example.com",
            "language": "en" if i % 2 == 0 else "fr"
        }, headers=_AUTH)
        assert response.status_code == 200

    rows = db.execute("SELECT * FROM interviews").fetchall()
    assert len(rows) == 5


def test_inconclusive_interview_flow(client, db):
    response = client.post("/api/invite", json={
        "candidate_name": "Bob Test",
        "candidate_email": "bob@example.com",
        "language": "en"
    }, headers=_AUTH)
    token = response.json()["link"].split("token=")[1]

    client.get(f"/api/interview/{token}")
    client.post(f"/api/interview/{token}/start")

    with patch("backend.services.processing.evaluate") as mock_evaluate, \
         patch("backend.services.processing.send_report_email") as mock_send_email:

        mock_evaluate.return_value = {
            "overall_score": 25,
            "cefr_level": "A2",
            "confidence": "low",
            "fluency_score": 20,
            "vocabulary_score": 30,
            "grammar_score": 25,
            "pronunciation_score": 28,
            "comprehension_score": 35,
            "strengths": [],
            "concerns": [{"text": "Too short", "timestamp": "00:00"}],
            "evidence": [],
            "candidate_speaking_time": 90,
            "word_count": 150,
            "evaluation_status": "completed",
        }
        mock_send_email.return_value = True

        from backend.services.processing import process_interview
        import asyncio

        asyncio.run(process_interview(token, [], "storage/recordings/y.webm", 120, "completed"))

    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    assert row["status"] == "inconclusive"

    report = db.execute("SELECT * FROM reports WHERE interview_id = ?", (row["id"],)).fetchone()
    assert report["inconclusive"] == 1
