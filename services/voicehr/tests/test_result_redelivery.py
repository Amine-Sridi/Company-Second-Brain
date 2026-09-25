"""
tests/test_result_redelivery.py
--------------------------------
Getting an interview result to RWS *after* the first attempt didn't land.

Why this capability exists: the completion webhook is sent exactly once,
best-effort (backend/services/rws_client.py). If RWS is restarting or unreachable
at that moment, VoiceHR keeps a complete, correct report while RWS's application
stays 'invited' -- its language gate stays 'pending', which blocks accept while
leaving reject available. That is a silently one-sided outcome with no in-product
way out. Two paths now lead out of it, and both are covered here:

  * POST /api/rws/interviews/{external_reference_id}/resend-result -- re-delivers
    the stored result, costing no Gemini quota.
  * retry_report -- re-evaluates AND re-notifies, so an admin correction reaches
    RWS instead of silently diverging from it.

Only the outbound HTTP call is stubbed; the real scores_from_report_row /
build_assessment_payload shaping runs.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from tests.conftest import TEST_RWS_SERVICE_KEY, create_interview, create_report

_RWS_AUTH = {"x-api-key": TEST_RWS_SERVICE_KEY}


def _delivered(return_value=True):
    """Patches only the outbound HTTP call inside rws_client, so
    notify_and_record_interview_result and the payload shaping stay real."""
    return patch(
        "backend.services.rws_client.notify_rws_interview_result",
        new=AsyncMock(return_value=return_value),
    )


def _interview_with_report(db, *, token, external_reference_id, report_token, **report_kwargs):
    create_interview(db, token=token, external_reference_id=external_reference_id)
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    create_report(db, row["id"], report_token=report_token, **report_kwargs)
    return row["id"]


# ---------------------------------------------------------------------------
# Auth + preconditions
# ---------------------------------------------------------------------------

def test_resend_result_without_api_key_returns_401(client, db):
    response = client.post("/api/rws/interviews/app-123/resend-result")
    assert response.status_code == 401


def test_resend_result_wrong_api_key_returns_401(client, db):
    response = client.post(
        "/api/rws/interviews/app-123/resend-result", headers={"x-api-key": "wrong-key"}
    )
    assert response.status_code == 401


def test_resend_result_unknown_external_reference_id_returns_404(client, db):
    response = client.post("/api/rws/interviews/unknown-app/resend-result", headers=_RWS_AUTH)
    assert response.status_code == 404


def test_resend_result_before_a_report_exists_returns_409(client, db):
    """Nothing to re-deliver until the interview has actually produced a result."""
    create_interview(db, token="resend-noreport", external_reference_id="app-noreport")

    response = client.post("/api/rws/interviews/app-noreport/resend-result", headers=_RWS_AUTH)

    assert response.status_code == 409
    assert "no result" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Delivery behaviour
# ---------------------------------------------------------------------------

def test_resend_result_delivers_the_stored_assessment(client, db):
    _interview_with_report(
        db, token="resend-ok", external_reference_id="app-resend-ok",
        report_token="resend-ok-report", overall_score=81, cefr_level="C1",
    )

    with _delivered() as mock_notify:
        response = client.post("/api/rws/interviews/app-resend-ok/resend-result", headers=_RWS_AUTH)

    assert response.status_code == 200
    assert response.json()["status"] == "delivered"
    assessment = mock_notify.call_args.kwargs["assessment"]
    assert assessment["overall_score"] == 81
    assert assessment["cefr_level"] == "C1"
    assert assessment["language"] == "en"
    assert mock_notify.call_args.kwargs["interview_status"] == "completed"


def test_resend_result_never_re_evaluates(client, db):
    """Re-delivery must cost zero Gemini quota -- it re-sends already-stored data.
    (The admin retry action is the path for when the evaluation itself needs redoing.)"""
    _interview_with_report(
        db, token="resend-noeval", external_reference_id="app-noeval",
        report_token="resend-noeval-report",
    )

    with _delivered(), patch(
        "backend.services.processing.evaluate", new=AsyncMock()
    ) as mock_evaluate:
        response = client.post("/api/rws/interviews/app-noeval/resend-result", headers=_RWS_AUTH)

    assert response.status_code == 200
    mock_evaluate.assert_not_called()


def test_resend_result_never_resends_a_stale_pronunciation_score(client, db):
    """Reports scored before pronunciation stopped being assessed still carry a
    non-null value in that column. Re-sending it would let an old row keep claiming
    an assessment this system no longer stands behind -- the same read-time
    guarantee GET /api/report already enforces."""
    interview_id = _interview_with_report(
        db, token="resend-pron", external_reference_id="app-pron",
        report_token="resend-pron-report",
    )
    stored = db.execute(
        "SELECT pronunciation_score FROM reports WHERE interview_id = ?", (interview_id,)
    ).fetchone()
    assert stored["pronunciation_score"] is not None  # the precondition this guards

    with _delivered() as mock_notify:
        client.post("/api/rws/interviews/app-pron/resend-result", headers=_RWS_AUTH)

    assert mock_notify.call_args.kwargs["assessment"]["pronunciation_score"] is None


def test_resend_result_uses_a_fresh_delivery_id_each_time(client, db):
    """RWS dedups by delivery_id, so reusing the original one would make every
    re-delivery a no-op on its side -- precisely the stuck state this exists to fix."""
    _interview_with_report(
        db, token="resend-ids", external_reference_id="app-ids", report_token="resend-ids-report",
    )

    with _delivered() as mock_notify:
        client.post("/api/rws/interviews/app-ids/resend-result", headers=_RWS_AUTH)
        client.post("/api/rws/interviews/app-ids/resend-result", headers=_RWS_AUTH)

    delivery_ids = [call.kwargs["delivery_id"] for call in mock_notify.call_args_list]
    assert len(set(delivery_ids)) == 2


def test_resend_result_surfaces_a_delivery_failure_as_502(client, db):
    """Unlike the automatic completion path (best-effort by design), an explicit
    resend's whole purpose is the delivery -- the same relationship resend-invite
    already has to a failed send."""
    _interview_with_report(
        db, token="resend-fail", external_reference_id="app-resend-fail",
        report_token="resend-fail-report",
    )

    with _delivered(return_value=False):
        response = client.post(
            "/api/rws/interviews/app-resend-fail/resend-result", headers=_RWS_AUTH
        )

    assert response.status_code == 502
    row = db.execute(
        "SELECT webhook_delivered_at FROM interviews WHERE token = ?", ("resend-fail",)
    ).fetchone()
    assert row["webhook_delivered_at"] is None


# ---------------------------------------------------------------------------
# retry_report must also re-notify (the admin correction path)
# ---------------------------------------------------------------------------

def test_retry_report_re_notifies_rws_with_the_corrected_result(client, db):
    """Without this, a retry silently diverged the two systems: VoiceHR showed the
    new, good report while RWS kept the old failed assessment -- and RWS's language
    gate decides on *its* copy, so a recruiter could be blocked from accepting a
    candidate whose report visibly passes."""
    from backend.services.processing import retry_report

    interview_id = _interview_with_report(
        db, token="retry-notify", external_reference_id="app-retry-notify",
        report_token="retry-notify-report", overall_score=None,
    )
    db.execute(
        "UPDATE reports SET evaluation_status = 'failed' WHERE report_token = ?",
        ("retry-notify-report",),
    )
    db.execute(
        "INSERT INTO recordings (interview_id, raw_transcript, duration_seconds) VALUES (?, ?, ?)",
        (
            interview_id,
            json.dumps([{"role": "user", "text": "a genuinely real answer", "timestamp": 5}] * 5),
            600,
        ),
    )
    db.commit()

    corrected = {
        "overall_score": 84, "cefr_level": "C1", "confidence": "High",
        "fluency_score": 85, "vocabulary_score": 82, "grammar_score": 80,
        "pronunciation_score": None, "comprehension_score": 88, "interaction_score": 84,
        "strengths": [], "concerns": [], "evidence": [],
        "candidate_speaking_time": 400, "word_count": 900,
        "evaluation_status": "completed",
    }
    with patch("backend.services.processing.evaluate", new=AsyncMock(return_value=corrected)), \
         patch(
             "backend.services.processing.notify_and_record_interview_result", new=AsyncMock()
         ) as mock_notify:
        asyncio.run(retry_report("retry-notify-report"))

    mock_notify.assert_called_once()
    # (conn, interview_id, external_reference_id, scores, inconclusive, language=...)
    assert mock_notify.call_args.args[2] == "app-retry-notify"
    assert mock_notify.call_args.args[3]["overall_score"] == 84
    assert mock_notify.call_args.args[4] is False
