"""
Tests for the interview-completion path: backend/services/processing.py, which is
what session.py's WebSocket handler calls when a candidate finishes an interview.
"""
import asyncio
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


# A transcript carrying enough candidate language to be gradeable, so these tests
# exercise the "completed" (not "inconclusive") status.
#
# The answers are long on purpose. This fixture used to be three one-sentence replies
# (~26 candidate words) and still reached "completed", because the old gate asked only
# whether the *session* lasted 300s. Sufficiency is now measured from the candidate's
# own language (backend/services/sufficiency.py), and 26 words across three short
# sentences is genuinely too thin to support a CEFR verdict -- so the fixture was
# unrealistic rather than the threshold being wrong. Lengthening it keeps every test
# below testing what it is actually about (RWS notification, claiming, redelivery)
# instead of depending on a lenient gate.
_TRANSCRIPT = [
    {"role": "assistant", "text": "Tell me about yourself.", "timestamp": 0},
    {"role": "user", "timestamp": 5, "text":
        "I have worked in backend engineering for about six years now, mostly building "
        "and maintaining REST APIs in Python with FastAPI and PostgreSQL behind them."},
    {"role": "assistant", "text": "Tell me about a challenge you faced.", "timestamp": 20},
    {"role": "user", "timestamp": 25, "text":
        "I once resolved a major production outage under a lot of pressure. The queue "
        "had backed up overnight and we had to drain it carefully without losing work."},
    {"role": "assistant", "text": "What's your opinion on remote work?", "timestamp": 40},
    {"role": "user", "timestamp": 45, "text":
        "I think remote work is effective when it is done well, which usually means "
        "writing things down properly and being deliberate about how the team overlaps."},
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
    # The job-specific language eligibility gate (RWS: language_gate.py) compares this
    # against a job's required_language -- it must be the interview's own conducted
    # language (interviews.language), not something the evaluator invents.
    assert call_kwargs["assessment"]["language"] == "en"

    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    assert row["status"] == "completed"
    assert row["webhook_delivery_id"] is not None
    assert row["webhook_delivered_at"] is not None


@pytest.mark.asyncio
async def test_local_completion_path_reports_the_interviews_own_language(
    client, db, mock_evaluate, mock_send_email, mock_notify_rws
):
    """A French interview must report language="fr" in the outbound assessment, not a
    hardcoded default -- confirms the value is threaded from the interview row, not
    the evaluator's own (language-agnostic) scoring output."""
    token = create_interview(db, token="test-token", status="active", language="fr", external_reference_id="app-42")

    await process_interview(token, _TRANSCRIPT, "storage/recordings/x.webm", 600, "completed")

    assert mock_notify_rws.call_args.kwargs["assessment"]["language"] == "fr"


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


# ---------------------------------------------------------------------------
# Completion idempotency (VoiceHR autonomous interview session robustness)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_interview_concurrent_calls_finalize_exactly_once(
    client, db, mock_send_email, mock_notify_rws
):
    """
    The real race this guards against: two calls to process_interview for the same
    interview overlapping in time (e.g. a WebSocket close finalizing normally while
    something else also reaches this function for the same token). evaluate() is
    given a real await point (asyncio.sleep) so the two calls' execution genuinely
    interleaves under asyncio.gather -- without the finalized_at claim landing before
    that await point, both calls would pass the old plain status check, both evaluate,
    and both send a webhook with a *different* delivery_id each (rws_client.py mints a
    fresh one per call), which RWS's own delivery-id dedup cannot catch as a
    duplicate. Exactly one evaluation/report/webhook must result.
    """
    token = create_interview(db, token="test-token", status="active", external_reference_id="app-42")

    async def slow_evaluate(*args, **kwargs):
        await asyncio.sleep(0.05)
        return {
            "overall_score": 72, "cefr_level": "B2", "confidence": "medium",
            "fluency_score": 70, "vocabulary_score": 75, "grammar_score": 68,
            "pronunciation_score": 74, "comprehension_score": 80,
            "strengths": [], "concerns": [], "evidence": [],
            "candidate_speaking_time": 420, "word_count": 1200,
            "evaluation_status": "completed",
        }

    with patch("backend.services.processing.evaluate", new=slow_evaluate):
        await asyncio.gather(
            process_interview(token, _TRANSCRIPT, "storage/recordings/x.webm", 600, "completed"),
            process_interview(token, _TRANSCRIPT, "storage/recordings/x.webm", 600, "completed"),
        )

    mock_notify_rws.assert_called_once()
    interview_id = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()["id"]
    report_count = db.execute(
        "SELECT COUNT(*) AS n FROM reports WHERE interview_id = ?", (interview_id,)
    ).fetchone()["n"]
    recording_count = db.execute(
        "SELECT COUNT(*) AS n FROM recordings WHERE interview_id = ?", (interview_id,)
    ).fetchone()["n"]
    assert report_count == 1
    assert recording_count == 1


@pytest.mark.asyncio
async def test_process_interview_repeated_sequential_calls_run_evaluation_once(
    client, db, mock_evaluate, mock_send_email, mock_notify_rws
):
    """The simpler, sequential case: once an interview has finalized, a second call
    (e.g. a duplicate finalization attempt after the fact) must be a pure no-op."""
    token = create_interview(db, token="test-token", status="active", external_reference_id="app-42")

    await process_interview(token, _TRANSCRIPT, "storage/recordings/x.webm", 600, "completed")
    await process_interview(token, _TRANSCRIPT, "storage/recordings/x.webm", 600, "completed")

    mock_evaluate.assert_called_once()
    mock_notify_rws.assert_called_once()


@pytest.mark.asyncio
async def test_process_interview_clears_pending_reconnect_columns_on_finalize(
    client, db, mock_evaluate, mock_send_email, mock_notify_rws
):
    """Once genuinely finalized, the reconnect-recovery scratch columns are cleared --
    nothing will ever read them again for this row."""
    token = create_interview(db, token="test-token", status="active", external_reference_id="app-42")
    db.execute(
        "UPDATE interviews SET pending_transcript = ?, pending_duration_seconds = ? WHERE token = ?",
        ('[{"role": "user", "text": "leftover"}]', 42.0, token),
    )
    db.commit()

    await process_interview(token, _TRANSCRIPT, "storage/recordings/x.webm", 600, "completed")

    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    assert row["pending_transcript"] is None
    assert row["pending_duration_seconds"] is None
