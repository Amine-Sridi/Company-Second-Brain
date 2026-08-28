"""
Tests for the interview-completion path: backend/services/processing.py, which is
what session.py's WebSocket handler calls when a candidate finishes an interview.
"""
from unittest.mock import patch, AsyncMock

import pytest

from backend.services.processing import process_interview
from tests.conftest import create_interview


@pytest.fixture
def mock_evaluate():
    with patch("backend.services.processing.evaluate") as mock:
        mock.return_value = {
            "overall_score": 72,
            "cefr_level": "B2",
            "confidence": "medium",
            "fluency_score": 70,
            "vocabulary_score": 75,
            "grammar_score": 68,
            "pronunciation_score": 74,
            "comprehension_score": 80,
            "strengths": [{"text": "Good fluency", "timestamp": "02:14"}],
            "concerns": [],
            "evidence": [],
            "candidate_speaking_time": 420,
            "word_count": 1200,
            "evaluation_status": "completed",
        }
        yield mock


@pytest.fixture
def mock_send_email():
    with patch("backend.services.processing.send_report_email") as mock:
        mock.return_value = True
        yield mock


@pytest.fixture
def mock_notify_rws():
    """Patches the network call inside rws_client.notify_and_record_interview_result,
    which processing.process_interview calls once an interview completes."""
    with patch(
        "backend.services.rws_client.notify_rws_interview_result", new_callable=AsyncMock
    ) as mock:
        mock.return_value = True
        yield mock


# Three candidate turns and >=300s duration -> not inconclusive by either rule in
# processing.process_interview, so a "completed" (not "inconclusive") status is exercised.
_TRANSCRIPT = [
    {"role": "assistant", "text": "Tell me about yourself.", "timestamp": 0},
    {"role": "user", "text": "I've worked in backend engineering for six years.", "timestamp": 5},
    {"role": "assistant", "text": "Tell me about a challenge you faced.", "timestamp": 20},
    {"role": "user", "text": "I once resolved a major production outage under pressure.", "timestamp": 25},
    {"role": "assistant", "text": "What's your opinion on remote work?", "timestamp": 40},
    {"role": "user", "text": "I think remote work is effective when done well.", "timestamp": 45},
]


@pytest.mark.asyncio
async def test_local_completion_path_notifies_rws_when_external_reference_id_set(
    client, db, mock_evaluate, mock_send_email, mock_notify_rws
):
    """This is the real trigger session.py uses to end an interview -- the fix under test."""
    token = create_interview(db, token="test-token", status="active", external_reference_id="app-42")

    await process_interview(token, _TRANSCRIPT, "storage/recordings/x.webm", 600, "completed")

    mock_notify_rws.assert_called_once()
    call_kwargs = mock_notify_rws.call_args.kwargs
    assert call_kwargs["external_reference_id"] == "app-42"
    assert call_kwargs["interview_status"] == "completed"
    assert call_kwargs["assessment"]["overall_score"] == 72
    assert call_kwargs["assessment"]["inconclusive"] is False

    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    assert row["status"] == "completed"
    assert row["webhook_delivery_id"] is not None
    assert row["webhook_delivered_at"] is not None


@pytest.mark.asyncio
async def test_local_completion_path_short_interview_notifies_rws_as_inconclusive(
    client, db, mock_evaluate, mock_send_email, mock_notify_rws
):
    token = create_interview(db, token="test-token", status="active", external_reference_id="app-42")

    # Under 300 seconds -> inconclusive, per processing.process_interview's own rule.
    await process_interview(token, [], "storage/recordings/x.webm", 60, "completed")

    call_kwargs = mock_notify_rws.call_args.kwargs
    assert call_kwargs["interview_status"] == "inconclusive"
    assert call_kwargs["assessment"]["inconclusive"] is True

    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    assert row["status"] == "inconclusive"


@pytest.mark.asyncio
async def test_local_completion_path_terminated_interview_notifies_rws(
    client, db, mock_evaluate, mock_send_email, mock_notify_rws
):
    """A candidate-refused/abusive-language termination is still a terminal outcome RWS
    needs to know about -- VoiceHR's 'terminated' status maps to RWS's 'inconclusive'
    interview_status bucket (RWS has no separate 'terminated' concept)."""
    token = create_interview(db, token="test-token", status="active", external_reference_id="app-42")

    await process_interview(token, _TRANSCRIPT, "storage/recordings/x.webm", 600, "candidate_refused")

    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    assert row["status"] == "terminated"
    mock_notify_rws.assert_called_once()
    assert mock_notify_rws.call_args.kwargs["interview_status"] == "inconclusive"


@pytest.mark.asyncio
async def test_local_completion_path_evaluation_failure_notifies_rws_with_failed_status(
    client, db, mock_send_email, mock_notify_rws
):
    token = create_interview(db, token="test-token", status="active", external_reference_id="app-42")

    with patch("backend.services.processing.evaluate") as mock_eval:
        mock_eval.return_value = {
            "overall_score": None,
            "evaluation_status": "failed",
            "error": "provider unavailable",
        }
        await process_interview(token, _TRANSCRIPT, "storage/recordings/x.webm", 600, "completed")

    call_kwargs = mock_notify_rws.call_args.kwargs
    assert call_kwargs["interview_status"] == "failed"
    assert call_kwargs["assessment"]["evaluation_status"] == "failed"


@pytest.mark.asyncio
async def test_local_completion_path_manual_interview_does_not_notify_rws(
    client, db, mock_evaluate, mock_send_email, mock_notify_rws
):
    """Interviews created outside the RWS flow (external_reference_id is NULL) must
    never trigger the outbound webhook -- there is nothing on the RWS side to notify."""
    token = create_interview(db, token="test-token", status="active", external_reference_id=None)

    await process_interview(token, _TRANSCRIPT, "storage/recordings/x.webm", 600, "completed")

    mock_notify_rws.assert_not_called()


@pytest.mark.asyncio
async def test_local_completion_path_rws_unreachable_does_not_break_report_email(
    client, db, mock_evaluate, mock_send_email, mock_notify_rws
):
    """A failed RWS delivery must not prevent VoiceHR's own report email -- the two are
    independent, and VoiceHR's internal flow must survive RWS being down."""
    mock_notify_rws.return_value = False
    token = create_interview(db, token="test-token", status="active", external_reference_id="app-42")

    await process_interview(token, _TRANSCRIPT, "storage/recordings/x.webm", 600, "completed")

    mock_send_email.assert_called_once()
    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    assert row["webhook_delivery_id"] is not None
    assert row["webhook_delivered_at"] is None
