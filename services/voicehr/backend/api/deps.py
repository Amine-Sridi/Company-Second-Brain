import secrets
import sqlite3
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from backend.core.config import settings
from backend.database import DB_PATH, MAX_INTERVIEW_RECONNECTS


def get_db():
    # check_same_thread=False: this sync generator dependency is itself resolved in a
    # threadpool worker thread (FastAPI's standard handling of sync dependencies), but
    # POST /api/invite (create_invite) is an async route that awaits email delivery --
    # its own body runs on the event loop thread, a different one. The connection is
    # still only ever used sequentially within one request, never concurrently from two
    # threads at once, which is exactly what this flag is for (the standard fix for
    # this FastAPI async-route + sync-sqlite3-dependency combination).
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def validate_interview_token(token: str, db=Depends(get_db)):
    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Invalid token")
    if row["expires_at"] < datetime.utcnow().isoformat():
        raise HTTPException(status_code=410, detail="Link expired")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail="Interview already completed or active")
    return row


_admin_security = HTTPBasic()


def require_admin_auth(credentials: HTTPBasicCredentials = Depends(_admin_security)) -> None:
    """
    Minimal HTTP Basic auth gate for the admin/report-management surface (candidate
    PII, transcripts, recordings, scores, and report retry actions) -- deliberately
    not a user database, session system, or JWT: one shared username/password pair
    from settings. Browsers handle the credential prompt and cache it for the
    session natively via the WWW-Authenticate: Basic challenge, so no frontend
    JavaScript, localStorage, or URL ever needs to carry these credentials.

    Both comparisons run unconditionally (combined with `&`, not `and`) and via
    secrets.compare_digest so neither which check failed nor how far a partial match
    got is observable from response timing -- the standard pattern for HTTP Basic
    auth (matches FastAPI's own documented recommendation for this exact case).
    """
    username_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"), settings.admin_username.encode("utf-8")
    )
    password_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"), settings.admin_password.encode("utf-8")
    )
    if not (username_ok & password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def validate_interview_start(token: str, db=Depends(get_db)):
    """
    Validate a start request and recover a session that looks abandoned before a real
    WebSocket connect (or after one dropped) landed -- e.g. the candidate closed the
    tab right after POST /start succeeded, or their WebSocket was cut and they're now
    reloading instead of clicking the in-page Reconnect button.

    Bounded by the SAME MAX_INTERVIEW_RECONNECTS budget api/routes/session.py's own
    WebSocket-disconnect handler enforces (see database.py) -- this used to be an
    independent, uncapped fallback (any reconnect attempted more than 30 seconds after
    the most recent /start call, which is true of almost every real disconnect, would
    silently reset status regardless of retry_count), which made session.py's own
    "one automatic retry" cap meaningless in practice. Once the shared budget is
    exhausted, this interview simply stops offering a fresh /start (falls through to
    the 409 below) -- it is intentionally not auto-finalized here (no scheduler, no
    forced evaluation of a possibly-nonsensical transcript); it just stays 'active'
    and unreachable, which already satisfies "never looks like a successful
    assessment" (no report is ever generated for it) without new async plumbing in
    this otherwise-sync dependency.
    """
    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Invalid token")
    if row["expires_at"] < datetime.utcnow().isoformat():
        raise HTTPException(status_code=410, detail="Link expired")

    if row["status"] == "active" and row["started_at"]:
        try:
            started_at = datetime.fromisoformat(row["started_at"])
            if (
                datetime.utcnow() - started_at > timedelta(seconds=30)
                and row["retry_count"] < MAX_INTERVIEW_RECONNECTS
            ):
                db.execute(
                    "UPDATE interviews SET status = 'pending' WHERE token = ? AND status = 'active'",
                    (token,),
                )
                db.commit()
                row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
        except ValueError:
            pass

    if row["status"] != "pending":
        if row["status"] == "active" and row["retry_count"] >= MAX_INTERVIEW_RECONNECTS:
            raise HTTPException(
                status_code=409,
                detail="This interview could not be resumed after multiple attempts. "
                       "Please contact the recruitment team.",
            )
        raise HTTPException(status_code=409, detail="Interview already completed or active")
    return row
