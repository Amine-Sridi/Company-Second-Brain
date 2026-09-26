import json
import os
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from tests.conftest import (
    TEST_ADMIN_PASSWORD,
    TEST_ADMIN_USERNAME,
    TEST_RWS_SERVICE_KEY,
    create_interview,
    create_report,
)

_ADMIN_AUTH = (TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD)
_RWS_AUTH = {"x-api-key": TEST_RWS_SERVICE_KEY}


def test_get_report_success(client, db):
    token = create_interview(db, token="test-token", name="John Doe", language="en")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    report_token = create_report(db, row["id"], report_token="report-token", overall_score=72, cefr_level="B2")

    response = client.get(f"/api/report/{report_token}")
    assert response.status_code == 200
    data = response.json()
    assert data["candidate_name"] == "John Doe"
    assert data["language"] == "en"
    assert data["overall_score"] == 72
    assert data["cefr_level"] == "B2"
    assert data["confidence"] == "Medium"
    assert data["inconclusive"] is False
    assert "dimensions" in data
    assert "strengths" in data
    assert "concerns" in data
    assert "evidence" in data
    assert "stats" in data


def test_get_report_not_found(client, db):
    response = client.get("/api/report/nonexistent-token")
    assert response.status_code == 404


def test_get_failed_report_exposes_evaluation_error(client, db):
    token = create_interview(db, token="failed-report-interview", status="completed")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    report_token = create_report(db, row["id"], report_token="failed-report")
    db.execute(
        "UPDATE reports SET overall_score = NULL, evaluation_status = 'failed', evaluation_error = ? "
        "WHERE report_token = ?",
        ("Gemini evaluator unavailable", report_token),
    )
    db.commit()

    response = client.get(f"/api/report/{report_token}")
    assert response.status_code == 200
    assert response.json()["evaluation_status"] == "failed"
    assert response.json()["evaluation_error"] == "Gemini evaluator unavailable"


def test_terminated_interview_keeps_report_and_reason(client, db):
    token = create_interview(db, token="terminated-token", name="Rude Candidate", status="active")
    scores = {
        "overall_score": 31, "cefr_level": "A2", "confidence": "low",
        "fluency_score": 30, "vocabulary_score": 35, "grammar_score": 28,
        "pronunciation_score": 32, "comprehension_score": 30,
        "strengths": [], "concerns": [], "evidence": [],
        "candidate_speaking_time": 18, "word_count": 12,
        "evaluation_status": "completed", "error": None,
    }
    with patch("backend.services.processing.evaluate", new=AsyncMock(return_value=scores)):
        from backend.services.processing import process_interview
        import asyncio
        asyncio.run(process_interview(
            token, [{"role": "user", "text": "I refuse to answer", "timestamp": 1}],
            None, 45, "candidate_refused",
        ))

    row = db.execute(
        "SELECT status FROM interviews WHERE token = ?", (token,)
    ).fetchone()
    recording = db.execute("SELECT ended_reason FROM recordings WHERE interview_id = (SELECT id FROM interviews WHERE token = ?)", (token,)).fetchone()
    assert row["status"] == "terminated"
    assert recording["ended_reason"] == "candidate_refused"

    response = client.get("/api/admin/reports", auth=_ADMIN_AUTH)
    report = response.json()["reports"][0]
    assert report["status"] == "terminated"
    assert report["termination_reason"] == "candidate_refused"


def test_get_report_includes_duration_recording_and_transcript(client, db):
    token = create_interview(db, token="report-details-token", name="Jane Doe")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    transcript = [
        {"role": "assistant", "text": "Tell me about yourself.", "timestamp": 0},
        {"role": "user", "text": "I manage a product team.", "timestamp": 8},
    ]
    db.execute(
        """INSERT INTO recordings
           (interview_id, recording_path, transcript_path, duration_seconds, raw_transcript)
           VALUES (?, ?, ?, ?, ?)""",
        (row["id"], "storage/recordings/42.webm", "storage/transcripts/42.json", 642, json.dumps(transcript)),
    )
    db.commit()
    report_token = create_report(db, row["id"], report_token="details-report-token")

    response = client.get(f"/api/report/{report_token}")
    data = response.json()

    assert data["assessment_type"].startswith("B2-style estimate")
    assert data["stats"]["interview_duration_seconds"] == 642
    assert data["stats"]["candidate_speaking_duration_seconds"] == 420
    assert data["recording"] == {
        "url": "/recordings/42.webm",
        "filename": "42.webm",
        "available": True,
    }
    assert data["transcript"] == transcript


# ---------------------------------------------------------------------------
# Assessment contract (VoiceHR interview quality benchmark + recruiter-facing
# assessment explanation): structured dimension payload, insufficient-evidence
# pass-through, pronunciation always null, no hidden reasoning, backward
# compatibility with reports stored before this rubric pass.
# ---------------------------------------------------------------------------

def test_get_report_structured_dimension_payload_roundtrip(client, db):
    """Every real rubric dimension survives a full write (INSERT via the same
    columns processing.py writes) -> read (build_report) roundtrip, under both its
    long-form rubric key and its short legacy alias."""
    token = create_interview(db, token="dimension-roundtrip-token")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    db.execute(
        """INSERT INTO reports (interview_id, report_token, overall_score, cefr_level, confidence,
           inconclusive, fluency_score, vocabulary_score, grammar_score, pronunciation_score,
           comprehension_score, interaction_score, strengths, concerns, evidence,
           candidate_speaking_time, word_count, evaluation_status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (row["id"], "dimension-report", 78, "B2", "High", 0,
         74, 68, 71, None, 80, 66,
         json.dumps([]), json.dumps([]), json.dumps([]), 300, 500, "completed",
         datetime.utcnow().isoformat()),
    )
    db.commit()

    response = client.get("/api/report/dimension-report")
    assert response.status_code == 200
    dims = response.json()["dimensions"]
    assert dims["fluency_coherence"] == 74 and dims["fluency"] == 74
    assert dims["vocabulary_range"] == 68 and dims["vocabulary"] == 68
    assert dims["grammar_language_control"] == 71 and dims["grammar"] == 71
    assert dims["comprehension_relevance"] == 80 and dims["comprehension"] == 80
    assert dims["interaction_spontaneity"] == 66 and dims["interaction"] == 66


def test_get_report_pronunciation_always_null_even_if_stored_nonnull(client, db):
    """A legacy row that happens to have a stale non-null pronunciation_score (e.g.
    scored before this rubric pass existed) must still render null -- the contract
    ("never assessed without genuine audio evidence") is enforced at read time, not
    just by what the current evaluator happens to write."""
    token = create_interview(db, token="pronunciation-legacy-token")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    # create_report's own default (74) simulates exactly this stale-legacy-value case.
    report_token = create_report(db, row["id"], report_token="pronunciation-legacy-report")

    stored = db.execute(
        "SELECT pronunciation_score FROM reports WHERE report_token = ?", (report_token,)
    ).fetchone()
    assert stored["pronunciation_score"] == 74  # confirms the DB really does hold a stale non-null value

    response = client.get(f"/api/report/{report_token}")
    dims = response.json()["dimensions"]
    assert dims["pronunciation_intelligibility"] is None
    assert dims["pronunciation"] is None


def test_get_report_old_stored_report_is_backward_compatible(client, db):
    """A report row created before interaction_score/evaluation_status existed in
    their current form (create_report's own helper never sets either) must still
    render successfully, with graceful nulls/defaults rather than a KeyError or a
    fabricated value."""
    token = create_interview(db, token="legacy-report-token")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    report_token = create_report(db, row["id"], report_token="legacy-report")

    response = client.get(f"/api/report/{report_token}")
    assert response.status_code == 200
    data = response.json()
    assert data["dimensions"]["interaction_spontaneity"] is None
    assert data["dimensions"]["interaction"] is None
    # evaluation_status defaults to 'completed' at the DB level (schema default) for
    # a row this old -- still a valid, renderable state, not a crash.
    assert data["evaluation_status"] == "completed"
    assert data["sufficient_evidence"] is True


def test_get_report_insufficient_evidence_survives_the_full_pipeline(client, db):
    """process_interview -> reports table -> build_report(), end to end, for an
    insufficient-evidence outcome -- not just a unit test of evaluator.py in
    isolation. Confirms cefr/overall stay null and evaluation_status/
    sufficient_evidence correctly reach the report a recruiter (or RWS, via the
    identical row) actually reads."""
    from backend.services.processing import process_interview
    import asyncio

    token = create_interview(db, token="insufficient-pipeline-token", external_reference_id="app-99")
    scores = {
        "overall_score": None, "cefr_level": None, "confidence": "Low",
        "fluency_score": 40, "vocabulary_score": 38, "grammar_score": 35,
        "pronunciation_score": None, "comprehension_score": 20, "interaction_score": 30,
        "strengths": [], "concerns": [{"text": "Answers were consistently off-topic", "timestamp": "01:00"}],
        "evidence": [], "candidate_speaking_time": 200, "word_count": 300,
        "evaluation_status": "insufficient_evidence",
    }
    with patch("backend.services.processing.evaluate", new=AsyncMock(return_value=scores)), \
         patch("backend.services.processing.notify_and_record_interview_result", new=AsyncMock()) as mock_notify:
        asyncio.run(process_interview(
            token, [{"role": "user", "text": "off topic", "timestamp": 5}] * 5,
            "storage/recordings/x.webm", 420, "completed",
        ))

    row = db.execute(
        "SELECT r.* FROM reports r JOIN interviews i ON r.interview_id = i.id WHERE i.token = ?", (token,)
    ).fetchone()
    assert row["evaluation_status"] == "insufficient_evidence"
    assert row["overall_score"] is None
    assert row["cefr_level"] is None
    assert bool(row["inconclusive"]) is True

    response = client.get(f"/api/report/{row['report_token']}")
    data = response.json()
    assert data["evaluation_status"] == "insufficient_evidence"
    assert data["sufficient_evidence"] is False
    assert data["cefr_level"] is None
    assert data["overall_score"] is None
    mock_notify.assert_called_once()
    # notify_and_record_interview_result(conn, interview_id, external_reference_id,
    # scores, inconclusive, language=...) -- args[4] is the combined inconclusive
    # signal (pre-check OR insufficient_evidence) processing.py derives and passes
    # through to RWS's webhook helper.
    assert mock_notify.call_args.args[4] is True


def test_get_report_never_exposes_a_hidden_reasoning_field(client, db):
    """The response is built from a fixed, explicit key set (report.py: build_report) --
    nothing does a catch-all `**row` spread that could leak an unexpected column."""
    token = create_interview(db, token="no-hidden-fields-token")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    report_token = create_report(db, row["id"], report_token="no-hidden-fields-report")

    response = client.get(f"/api/report/{report_token}")
    data = response.json()
    for disallowed in ("reasoning", "chain_of_thought", "internal_notes", "raw_response", "thinking", "prompt"):
        assert disallowed not in data
        assert disallowed not in data.get("dimensions", {})


def test_admin_reports_empty(client, db):
    response = client.get("/admin/reports", auth=_ADMIN_AUTH)
    assert response.status_code == 200
    assert "Completed Interviews" in response.text


def test_admin_reports_with_data(client, db):
    token = create_interview(db, token="test-token", name="John Doe", status="completed")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    create_report(db, row["id"], report_token="report-token")

    response = client.get("/admin/reports", auth=_ADMIN_AUTH)
    assert response.status_code == 200
    assert "John Doe" in response.text
    assert "report-token" in response.text


def test_admin_reports_api_includes_full_candidate_report(client, db):
    token = create_interview(db, token="admin-api-token", name="Admin Candidate", email="candidate@example.com", status="completed")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    transcript = [
        {"role": "assistant", "text": "Bonjour.", "timestamp": 0},
        {"role": "user", "text": "Je dirige une equipe.", "timestamp": 7},
    ]
    db.execute(
        """INSERT INTO recordings
           (interview_id, recording_path, duration_seconds, raw_transcript)
           VALUES (?, ?, ?, ?)""",
        (row["id"], "storage/recordings/admin-api.webm", 605, json.dumps(transcript)),
    )
    db.commit()
    create_report(db, row["id"], report_token="admin-api-report")

    response = client.get("/api/admin/reports", auth=_ADMIN_AUTH)
    assert response.status_code == 200
    data = response.json()
    assert len(data["reports"]) == 1
    report = data["reports"][0]
    assert report["candidate_name"] == "Admin Candidate"
    assert report["candidate_email"] == "candidate@example.com"
    assert report["overall_score"] == 72
    assert report["dimensions"]["interaction_comprehension"] == 80
    assert report["stats"]["interview_duration_seconds"] == 605
    assert report["recording_url"] == "/recordings/admin-api.webm"
    assert report["transcript"] == transcript


@pytest.mark.asyncio
async def test_retry_failed_report_updates_existing_report(client, db):
    token = create_interview(db, token="retry-report-token", name="Retry Candidate", status="inconclusive")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    transcript = [
        {"role": "user", "text": "I lead a team.", "timestamp": 4},
        {"role": "user", "text": "I solved a problem.", "timestamp": 20},
        {"role": "user", "text": "I prefer hybrid work.", "timestamp": 40},
    ]
    db.execute(
        """INSERT INTO recordings (interview_id, transcript_path, duration_seconds, raw_transcript)
           VALUES (?, ?, ?, ?)""",
        (row["id"], "", 360, json.dumps(transcript)),
    )
    db.commit()
    report_token = create_report(db, row["id"], report_token="retry-report")
    db.execute(
        """UPDATE reports SET overall_score = NULL, cefr_level = NULL, fluency_score = NULL,
           vocabulary_score = NULL, grammar_score = NULL, pronunciation_score = NULL,
           comprehension_score = NULL, evaluation_status = 'failed' WHERE report_token = ?""",
        (report_token,),
    )
    db.commit()

    scores = {
        "overall_score": 74,
        "cefr_level": "B2",
        "confidence": "Medium",
        "fluency_score": 75,
        "vocabulary_score": 70,
        "grammar_score": 72,
        "pronunciation_score": 76,
        "comprehension_score": 78,
        "strengths": [],
        "concerns": [],
        "evidence": [],
        "candidate_speaking_time": 90,
        "word_count": 14,
        "evaluation_status": "completed",
    }
    with patch("backend.services.processing.evaluate", new=AsyncMock(return_value=scores)):
        response = client.post(f"/api/admin/reports/{report_token}/retry", auth=_ADMIN_AUTH)

    assert response.status_code == 200
    data = response.json()
    assert data["evaluation_status"] == "completed"
    assert data["overall_score"] == 74
    updated = db.execute(
        "SELECT overall_score, evaluation_status FROM reports WHERE report_token = ?", (report_token,)
    ).fetchone()
    assert tuple(updated) == (74, "completed")


def test_health_check_detail(client, db):
    response = client.get("/health/detail")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["db"] == "connected"
    assert data["evaluator_provider"] == "gemini"
    assert data["gemini_configured"] is False


def test_health_lightweight_endpoint_is_not_shadowed_by_detail_route(client, db):
    """The plain /health (main.py) must stay a cheap liveness check -- it must not be
    shadowed by, or resolve to, the richer /health/detail diagnostics in report.py."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data == {"status": "ok", "service": "voicehr"}


def test_serve_recording(client, db):
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
        f.write(b"fake audio data")
        temp_path = f.name

    try:
        filename = os.path.basename(temp_path)
        os.makedirs("storage/recordings", exist_ok=True)
        dest = os.path.join("storage/recordings", filename)
        with open(dest, "wb") as f:
            f.write(b"fake audio data")

        response = client.get(f"/recordings/{filename}", auth=_ADMIN_AUTH)
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/webm"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        if os.path.exists(dest):
            os.unlink(dest)


def test_serve_recording_does_not_escape_storage(client, db):
    response = client.get("/recordings/../voicehr.db", auth=_ADMIN_AUTH)
    assert response.status_code in (404, 416)


# ---------------------------------------------------------------------------
# RWS-facing report/recording endpoints (service-key auth, distinct from the
# Basic-auth admin surface above).
# ---------------------------------------------------------------------------

def test_rws_report_without_api_key_returns_401(client, db):
    response = client.get("/api/rws/interviews/app-123/report")
    assert response.status_code == 401


def test_rws_report_with_wrong_api_key_returns_401(client, db):
    response = client.get("/api/rws/interviews/app-123/report", headers={"x-api-key": "wrong-key"})
    assert response.status_code == 401


def test_rws_report_admin_credentials_do_not_satisfy_service_key_auth(client, db):
    # The two auth mechanisms are deliberately independent -- Basic-auth admin
    # credentials must not substitute for the RWS service key here, any more than
    # the service key substitutes for admin Basic auth on /admin/reports.
    response = client.get("/api/rws/interviews/app-123/report", auth=_ADMIN_AUTH)
    assert response.status_code == 401


def test_rws_report_unknown_external_reference_id_returns_404(client, db):
    response = client.get("/api/rws/interviews/unknown-app-id/report", headers=_RWS_AUTH)
    assert response.status_code == 404


def test_rws_report_interview_exists_but_no_report_yet_returns_404(client, db):
    create_interview(db, token="pending-token", external_reference_id="app-pending", status="active")
    response = client.get("/api/rws/interviews/app-pending/report", headers=_RWS_AUTH)
    assert response.status_code == 404


def test_rws_report_success_returns_full_report_without_raw_recording_path(client, db):
    token = create_interview(
        db, token="rws-report-token", name="RWS Candidate", email="rws.candidate@example.com",
        external_reference_id="app-rws-1", status="completed",
    )
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    db.execute(
        "INSERT INTO recordings (interview_id, recording_path) VALUES (?, ?)",
        (row["id"], "storage/recordings/rws-report-token.webm"),
    )
    db.commit()
    create_report(db, row["id"], report_token="rws-report", overall_score=88, cefr_level="C1")

    response = client.get("/api/rws/interviews/app-rws-1/report", headers=_RWS_AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["candidate_name"] == "RWS Candidate"
    assert data["candidate_email"] == "rws.candidate@example.com"
    assert data["overall_score"] == 88
    assert data["cefr_level"] == "C1"
    assert "dimensions" in data
    # The admin surface's raw recording path/URL must never appear here -- only a
    # bare availability flag. This is the exact boundary the RWS-facing endpoint
    # exists to enforce, so pin it precisely rather than just checking status 200.
    assert data["recording"] == {"available": True}
    assert "url" not in data["recording"]
    assert "filename" not in data["recording"]
    assert "recording_url" not in data


def test_rws_report_recording_unavailable_reports_false(client, db):
    token = create_interview(
        db, token="rws-report-no-rec", external_reference_id="app-rws-2", status="completed",
    )
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    create_report(db, row["id"], report_token="rws-report-no-rec-token")

    response = client.get("/api/rws/interviews/app-rws-2/report", headers=_RWS_AUTH)
    assert response.status_code == 200
    assert response.json()["recording"] == {"available": False}


def test_rws_recording_without_api_key_returns_401(client, db):
    response = client.get("/api/rws/interviews/app-123/recording")
    assert response.status_code == 401


def test_rws_recording_unknown_external_reference_id_returns_404(client, db):
    response = client.get("/api/rws/interviews/unknown-app-id/recording", headers=_RWS_AUTH)
    assert response.status_code == 404


def test_rws_recording_success_serves_bytes(client, db, tmp_path):
    token = create_interview(db, token="rws-recording-token", external_reference_id="app-rws-rec")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()

    os.makedirs("storage/recordings", exist_ok=True)
    dest = os.path.join("storage/recordings", "rws-recording-token.webm")
    with open(dest, "wb") as f:
        f.write(b"fake rws audio data")
    db.execute(
        "INSERT INTO recordings (interview_id, recording_path) VALUES (?, ?)",
        (row["id"], dest),
    )
    db.commit()

    try:
        response = client.get("/api/rws/interviews/app-rws-rec/recording", headers=_RWS_AUTH)
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/webm"
        assert response.content == b"fake rws audio data"
    finally:
        if os.path.exists(dest):
            os.unlink(dest)


def test_rws_recording_does_not_escape_storage(client, db):
    token = create_interview(db, token="rws-recording-escape", external_reference_id="app-rws-escape")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    db.execute(
        "INSERT INTO recordings (interview_id, recording_path) VALUES (?, ?)",
        (row["id"], "../../voicehr.db"),
    )
    db.commit()

    response = client.get("/api/rws/interviews/app-rws-escape/recording", headers=_RWS_AUTH)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# RWS-facing explicit resend endpoint (service-key auth, reuses the existing
# interview/token -- never creates a new one, unlike POST /api/invite).
# mock_email_delivery (conftest.py, autouse) makes a plain send below "succeed"
# without touching the network; tests that exercise failure override it again.
# ---------------------------------------------------------------------------

def test_rws_resend_invite_without_api_key_returns_401(client, db):
    response = client.post("/api/rws/interviews/app-123/resend-invite")
    assert response.status_code == 401


def test_rws_resend_invite_wrong_api_key_returns_401(client, db):
    response = client.post(
        "/api/rws/interviews/app-123/resend-invite", headers={"x-api-key": "wrong-key"}
    )
    assert response.status_code == 401


def test_rws_resend_invite_unknown_external_reference_id_returns_404(client, db):
    response = client.post("/api/rws/interviews/unknown-app-id/resend-invite", headers=_RWS_AUTH)
    assert response.status_code == 404


def test_rws_resend_invite_success_reuses_same_interview_and_never_leaks_token(client, db, monkeypatch):
    import backend.api.routes.interview as interview_module

    call_count = {"n": 0}

    async def _count_and_succeed(to, subject, body):
        call_count["n"] += 1

    monkeypatch.setattr(interview_module, "_send_email", _count_and_succeed)

    token = create_interview(
        db, token="resend-token", email="resend@example.com", external_reference_id="app-resend-1"
    )
    db.execute(
        "UPDATE interviews SET invite_email_sent_at = ? WHERE token = ?",
        (datetime.utcnow().isoformat(), token),
    )
    db.commit()

    response = client.post("/api/rws/interviews/app-resend-1/resend-invite", headers=_RWS_AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "sent"}
    assert "token" not in body and "link" not in body and "interview_link" not in body
    assert call_count["n"] == 1

    rows = db.execute(
        "SELECT * FROM interviews WHERE external_reference_id = ?", ("app-resend-1",)
    ).fetchall()
    assert len(rows) == 1  # no new interview was created
    assert rows[0]["token"] == token


def test_rws_resend_invite_sends_even_though_already_delivered(client, db, monkeypatch):
    """An explicit resend always attempts delivery, unlike POST /api/invite's own
    first-send-and-retry-on-failure logic -- mirrors the token-keyed explicit resend
    route's own contract (interview.py: send_invitation_email)."""
    import backend.api.routes.interview as interview_module

    call_count = {"n": 0}

    async def _count_and_succeed(to, subject, body):
        call_count["n"] += 1

    monkeypatch.setattr(interview_module, "_send_email", _count_and_succeed)

    token = create_interview(
        db, token="already-sent-token", email="already@example.com", external_reference_id="app-resend-2"
    )
    db.execute(
        "UPDATE interviews SET invite_email_sent_at = ? WHERE token = ?",
        (datetime.utcnow().isoformat(), token),
    )
    db.commit()

    response = client.post("/api/rws/interviews/app-resend-2/resend-invite", headers=_RWS_AUTH)
    assert response.status_code == 200
    assert call_count["n"] == 1


def test_rws_resend_invite_failure_returns_502(client, db, monkeypatch):
    import backend.api.routes.interview as interview_module

    async def _fail(to, subject, body):
        raise HTTPException(status_code=502, detail="Email send failed: provider rejected request")

    monkeypatch.setattr(interview_module, "_send_email", _fail)

    create_interview(db, token="resend-fail-token", external_reference_id="app-resend-3")

    response = client.post("/api/rws/interviews/app-resend-3/resend-invite", headers=_RWS_AUTH)
    assert response.status_code == 502
