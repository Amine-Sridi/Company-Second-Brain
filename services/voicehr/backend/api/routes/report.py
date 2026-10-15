import json
import mimetypes
import sqlite3
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from backend.api.deps import get_db, require_admin_auth
from backend.core.config import settings
from backend.core.rate_limit import RateLimiter, client_ip
from backend.api.routes.interview import deliver_invitation_email
from backend.api.routes.invite import require_rws_service_key
import backend.database as database
from backend.services.processing import retry_report
from backend.services.rws_client import (
    notify_and_record_interview_result,
    scores_from_report_row,
)

router = APIRouter()


# GET /api/report/{token} is unauthenticated by design -- the uuid4 token IS the
# credential -- which makes it the one route here worth guessing at. 122 bits is not
# brute-forceable, but an unlimited endpoint still lets an attacker probe freely and
# costs a DB round-trip per try. Keyed by IP: there is no account to key on.
_report_limiter = RateLimiter(limit=30, window_seconds=60)


@router.get("/api/report/{report_token}")
def get_report_public(request: Request, report_token: str, db=Depends(get_db)):
    """
    The candidate/admin-facing bearer URL. Unauthenticated by design -- the uuid4 token
    is the credential -- so it is the one surface that must expire (audit M10).

    The TTL is checked against reports.created_at with a cheap lookup *before* the
    report is assembled, so an expired request never materialises the candidate's name,
    email or transcript at all. An unknown token stays 404 rather than 410: 410 would
    confirm that a token once existed.
    """
    allowed, retry_after = _report_limiter.check(client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Try again shortly.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    row = db.execute(
        "SELECT created_at FROM reports WHERE report_token = ?", (report_token,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")

    ttl_days = getattr(settings, "report_token_ttl_days", 7)
    if ttl_days > 0 and row["created_at"]:
        try:
            created = datetime.fromisoformat(row["created_at"])
        except (TypeError, ValueError):
            created = None
        if created is not None:
            if datetime.utcnow() - created > timedelta(days=ttl_days):
                # Same status/shape as the interview link's own expiry (api/deps.py),
                # and deliberately free of any candidate detail.
                raise HTTPException(status_code=410, detail="Report link expired")

    return build_report(report_token, db)


def build_report(report_token: str, db):
    """Assembles a report payload. Deliberately NOT a route and deliberately without an
    expiry check: the authenticated admin surface and the RWS service-key lookup both
    call this directly and must keep working for reports of any age. The expiry is a
    property of the public bearer URL, not of the report -- see get_report_public."""
    row = db.execute(
        """SELECT r.*, i.candidate_name, i.candidate_email, i.language, i.status,
                  i.created_at as interview_date,
                  i.started_at, i.completed_at,
                   rec.recording_path, rec.transcript_path, rec.duration_seconds, rec.ended_reason, rec.raw_transcript
           FROM reports r
           JOIN interviews i ON r.interview_id = i.id
           LEFT JOIN recordings rec ON rec.interview_id = r.interview_id
           WHERE r.report_token = ?""",
        (report_token,),
    ).fetchone()

    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Report not found")

    def load_json(value, fallback):
        if not value:
            return fallback
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, type(fallback)) else fallback
        except (TypeError, json.JSONDecodeError):
            return fallback

    recording_path = row["recording_path"]
    recording_filename = Path(recording_path).name if recording_path else None
    transcript = load_json(row["raw_transcript"], [])
    if not transcript and row["transcript_path"]:
        try:
            with open(row["transcript_path"], "r", encoding="utf-8") as transcript_file:
                transcript = json.load(transcript_file)
        except (OSError, json.JSONDecodeError):
            transcript = []

    duration_seconds = row["duration_seconds"]
    if duration_seconds is None and row["started_at"] and row["completed_at"]:
        try:
            duration_seconds = max(
                0,
                int((datetime.fromisoformat(row["completed_at"]) - datetime.fromisoformat(row["started_at"])).total_seconds()),
            )
        except ValueError:
            duration_seconds = None
    confidence = row["confidence"]
    if isinstance(confidence, str):
        confidence = confidence.capitalize()
    evaluation_status = row["evaluation_status"] or (
        "failed" if row["overall_score"] is None else "completed"
    )
    # Explicit, recruiter-UI-friendly form of the same fact evaluation_status already
    # encodes: True once a real CEFR verdict exists, False when the transcript didn't
    # contain enough usable language to grade (never confused with a "failed"
    # technical evaluator error, which is neither "sufficient" nor "insufficient"
    # evidence -- it's simply unknown, so this stays None for that status).
    sufficient_evidence = (
        True if evaluation_status == "completed"
        else False if evaluation_status == "insufficient_evidence"
        else None
    )
    return {
        "candidate_name": row["candidate_name"],
        "candidate_email": row["candidate_email"],
        "language": row["language"],
        "status": row["status"],
        "termination_reason": row["ended_reason"] if row["status"] == "terminated" else None,
        "interview_date": row["interview_date"],
        "assessment_type": "B2-style estimate; not an official language certification",
        "overall_score": row["overall_score"],
        "cefr_level": row["cefr_level"],
        "confidence": confidence,
        "evaluation_status": evaluation_status,
        "sufficient_evidence": sufficient_evidence,
        "evaluation_error": row["evaluation_error"],
        "inconclusive": bool(row["inconclusive"]),
        "dimensions": {
            # interaction and comprehension are now scored as two separate rubric
            # dimensions (see backend/services/evaluator.py) -- interaction_comprehension
            # is kept, aliased to comprehension alone, only so an older stored report
            # (scored before this pass) still renders its one combined number under
            # its original key rather than silently disappearing.
            "interaction_comprehension": row["comprehension_score"],
            "comprehension_relevance": row["comprehension_score"],
            "interaction_spontaneity": row["interaction_score"],
            "fluency_coherence": row["fluency_score"],
            "grammar_language_control": row["grammar_score"],
            "vocabulary_range": row["vocabulary_score"],
            # Never fabricated -- always null, unconditionally, regardless of what
            # this row's own pronunciation_score column happens to contain. The
            # evaluator only ever sees the text transcript, never audio, so there is
            # no reliable evidence to score pronunciation from (see evaluator.py's
            # own comment) -- and a handful of reports scored before that rule
            # existed do still carry a stale non-null value in this column. Reading
            # it back verbatim would let an old row keep claiming a pronunciation
            # assessment this system no longer stands behind; hard-coding None here
            # is what makes "pronunciation is unavailable" a read-time guarantee,
            # not just a write-time habit.
            "pronunciation_intelligibility": None,
            # Keep the short names for existing report consumers.
            "fluency": row["fluency_score"],
            "vocabulary": row["vocabulary_score"],
            "grammar": row["grammar_score"],
            "pronunciation": None,
            "comprehension": row["comprehension_score"],
            "interaction": row["interaction_score"],
        },
        "dimension_weights": {
            "comprehension_relevance": 0.25,
            "fluency_coherence": 0.25,
            "grammar_language_control": 0.20,
            "vocabulary_range": 0.15,
            "interaction_spontaneity": 0.15,
            # Reported for transparency, not fabrication -- 0 weight because it is
            # never scored (see above), not because it doesn't matter.
            "pronunciation_intelligibility": 0.0,
        },
        "strengths": load_json(row["strengths"], []),
        "concerns": load_json(row["concerns"], []),
        "evidence": load_json(row["evidence"], []),
        "stats": {
            "interview_duration_seconds": duration_seconds,
            "duration_seconds": duration_seconds,
            "candidate_speaking_time": row["candidate_speaking_time"],
            "candidate_speaking_duration_seconds": row["candidate_speaking_time"],
            "word_count": row["word_count"],
        },
        "recording": {
            "url": f"/recordings/{recording_filename}" if recording_filename else None,
            "filename": recording_filename,
            "available": bool(recording_filename),
        },
        "recording_url": f"/recordings/{recording_filename}" if recording_filename else None,
        "transcript": transcript,
        "interview_id": row["interview_id"],
        "report_token": row["report_token"],
    }


@router.get("/api/rws/interviews/{external_reference_id}/report")
def get_rws_report(
    external_reference_id: str,
    db=Depends(get_db),
    _auth: None = Depends(require_rws_service_key),
):
    """
    RWS-facing report lookup, keyed by external_reference_id (RWS's own
    application_id) rather than VoiceHR's internal report_token, which RWS never
    sees. Gated by the same X-Api-Key service-key mechanism POST /api/invite
    already uses -- a completely separate auth path from the Basic-auth admin
    surface (require_admin_auth), which this route never touches or delegates to.

    Returns exactly the fields build_report() already returns for the admin surface,
    minus the raw recording path/URL: recording.available (bool) is kept, but the
    admin-only /recordings/{filename} path is stripped here so a raw VoiceHR-
    relative path can never reach RWS (and, from there, the browser) -- a caller
    that wants the actual recording bytes uses the separate endpoint below, which
    RWS proxies through its own URL instead of ever handing out this one.
    """
    interview = db.execute(
        "SELECT id FROM interviews WHERE external_reference_id = ?",
        (external_reference_id,),
    ).fetchone()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    report_row = db.execute(
        "SELECT report_token FROM reports WHERE interview_id = ?", (interview["id"],)
    ).fetchone()
    if not report_row:
        raise HTTPException(status_code=404, detail="Report not available")

    report = build_report(report_row["report_token"], db)
    recording_available = bool(report.get("recording", {}).get("available"))
    report["recording"] = {"available": recording_available}
    report.pop("recording_url", None)
    return report


@router.get("/api/rws/interviews/{external_reference_id}/recording")
def get_rws_recording(
    external_reference_id: str,
    db=Depends(get_db),
    _auth: None = Depends(require_rws_service_key),
):
    """
    RWS-facing recording lookup, keyed by external_reference_id -- the service-key
    counterpart to /recordings/{filename} (which stays Basic-auth-only and keyed by
    filename, untouched by this change). The filename never crosses this boundary:
    resolved server-side from the interview's own recording row, then re-rooted
    through Path(...).name exactly like serve_recording() does, so this can never
    be tricked into serving a path outside storage/recordings/.
    """
    row = db.execute(
        """SELECT rec.recording_path
           FROM interviews i
           JOIN recordings rec ON rec.interview_id = i.id
           WHERE i.external_reference_id = ?""",
        (external_reference_id,),
    ).fetchone()
    if not row or not row["recording_path"]:
        raise HTTPException(status_code=404, detail="Recording not available")

    recording_name = Path(row["recording_path"]).name
    path = Path("storage/recordings") / recording_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Recording not available")
    media_type = "audio/webm" if path.suffix.lower() == ".webm" else (mimetypes.guess_type(str(path))[0] or "audio/wav")
    return FileResponse(path, media_type=media_type)


@router.post("/api/rws/interviews/{external_reference_id}/resend-invite")
async def resend_rws_invite(
    external_reference_id: str,
    db=Depends(get_db),
    _auth: None = Depends(require_rws_service_key),
):
    """
    RWS-facing explicit resend, keyed by external_reference_id like the report/
    recording lookups above -- the service-key-gated counterpart RWS needs to trigger
    a resend, since the existing POST /interview/{token}/invite (interview.py:
    send_invitation_email) is unauthenticated and keyed by the raw token, which RWS
    never stores (see that route's own docstring).

    Always attempts delivery on the interview's *existing* token/row (never creates a
    new interview, unlike POST /api/invite with a fresh Idempotency-Key) via
    deliver_invitation_email -- the same function every other send path (automatic
    first send, automatic retry, the token-keyed explicit resend) already funnels
    through, so composition/sending logic never forks. Returns only whether the send
    succeeded; the interview link/token is never included in the response.
    """
    row = db.execute(
        "SELECT * FROM interviews WHERE external_reference_id = ?",
        (external_reference_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Interview not found")

    await deliver_invitation_email(db, row, row["token"])
    return {"status": "sent"}


@router.post("/api/rws/interviews/{external_reference_id}/resend-result")
async def resend_rws_result(
    external_reference_id: str,
    db=Depends(get_db),
    _auth: None = Depends(require_rws_service_key),
):
    """
    Re-delivers an already-evaluated interview's result to RWS, from stored data --
    the recovery path for a completion webhook RWS never actually received.

    Why this exists: the completion webhook is sent exactly once, best-effort (see
    rws_client.notify_rws_interview_result). If RWS is restarting or unreachable at
    that moment, VoiceHR keeps a perfectly good report while RWS's application stays
    'invited' forever -- its language gate stays 'pending', which blocks accept while
    leaving reject available. That is a silently one-sided outcome with no in-product
    way out. This endpoint is the way out, and is deliberately the whole fix rather
    than a retry queue or a reconciliation poller (both explicitly out of scope per
    docs/ARCHITECTURE_DECISIONS.md): the data is already here, so re-sending it is a single call.

    Costs no Gemini quota -- the assessment is rebuilt from the stored `reports` row
    (rws_client.scores_from_report_row), never re-evaluated. Use the admin retry
    action instead when the *evaluation itself* needs redoing.

    Only meaningful once an interview has actually finished and produced a report;
    409 otherwise. Unlike the automatic completion path, a delivery failure here is
    this request's own 502 -- resending has no other purpose if it doesn't arrive,
    the same relationship resend-invite already has to a failed send.
    """
    interview = db.execute(
        "SELECT * FROM interviews WHERE external_reference_id = ?",
        (external_reference_id,),
    ).fetchone()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    report = db.execute(
        "SELECT * FROM reports WHERE interview_id = ?", (interview["id"],)
    ).fetchone()
    if not report:
        raise HTTPException(
            status_code=409,
            detail=f"Interview has no result to send yet (status: {interview['status']})",
        )

    scores, inconclusive = scores_from_report_row(report)
    delivered = await notify_and_record_interview_result(
        db, interview["id"], external_reference_id, scores, inconclusive,
        language=interview["language"],
    )
    if not delivered:
        raise HTTPException(status_code=502, detail="RWS did not acknowledge the result")
    return {"status": "delivered"}


@router.get("/recordings/{filename}")
async def serve_recording(filename: str, _auth: None = Depends(require_admin_auth)):
    """
    Admin-only: recording filenames are derived from the sequential interview id
    (e.g. "42.webm"), not an unguessable token, so this must not be reachable without
    the same credentials as the rest of the report-management surface.
    """
    recording_name = Path(filename).name
    path = Path("storage/recordings") / recording_name
    media_type = "audio/webm" if filename.lower().endswith(".webm") else (mimetypes.guess_type(filename)[0] or "audio/wav")
    return FileResponse(path, media_type=media_type)


@router.get("/api/admin/reports")
def get_admin_reports(db=Depends(get_db), _auth: None = Depends(require_admin_auth)):
    rows = db.execute(
        """SELECT r.report_token
           FROM reports r JOIN interviews i ON r.interview_id = i.id
           WHERE i.status IN ('completed', 'inconclusive', 'terminated')
           ORDER BY r.created_at DESC"""
    ).fetchall()
    return {
        "reports": [build_report(row["report_token"], db) for row in rows]
    }


@router.post("/api/admin/reports/{report_token}/retry")
async def retry_admin_report(report_token: str, _auth: None = Depends(require_admin_auth)):
    try:
        await retry_report(report_token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # The retry service uses its own connection. Open a fresh connection here
    # because async endpoints and sync dependency connections may use different
    # worker threads.
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return build_report(report_token, conn)
    finally:
        conn.close()


@router.get("/admin/reports", response_class=HTMLResponse, include_in_schema=False)
def admin_reports(db=Depends(get_db), _auth: None = Depends(require_admin_auth)):
    page = Path("frontend/admin.html").read_text(encoding="utf-8")
    rows = db.execute(
        """SELECT i.candidate_name, r.report_token
           FROM interviews i LEFT JOIN reports r ON i.id = r.interview_id
           WHERE i.status IN ('completed', 'inconclusive', 'terminated')"""
    ).fetchall()
    legacy_data = "".join(
        f"<span>{escape(row['candidate_name'] or '')}</span><span>{escape(row['report_token'] or '')}</span>"
        for row in rows
    )
    return HTMLResponse(f"{page}<div hidden>{legacy_data}</div>")


@router.get("/health/detail")
def health_detail(db=Depends(get_db)):
    """
    Richer diagnostics than the top-level /health (main.py): DB connectivity and which
    Gemini models are configured. Kept off the plain /health path so container
    healthchecks stay a single cheap request -- main.py registers its own minimal
    /health first, which would otherwise shadow this route entirely (they can't share
    a path; only the first-registered handler ever runs).
    """
    db.execute("SELECT 1")
    from backend.core.config import settings

    return {
        "status": "ok",
        "db": "connected",
        "evaluator_provider": "gemini",
        "evaluator_model": getattr(
            settings, "gemini_evaluation_model",
            getattr(settings, "gemini_model", "gemini-2.5-flash"),
        ),
        "gemini_live_model": getattr(settings, "gemini_live_model", "gemini-3.1-flash-live-preview"),
        "gemini_configured": bool(getattr(settings, "gemini_api_key", None)),
    }
