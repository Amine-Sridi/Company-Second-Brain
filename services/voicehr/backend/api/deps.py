import secrets
import sqlite3
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from backend.core.config import settings
from backend.database import DB_PATH


def get_db():
    conn = sqlite3.connect(DB_PATH)
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
    """Validate a start request and recover sessions abandoned before connect."""
    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Invalid token")
    if row["expires_at"] < datetime.utcnow().isoformat():
        raise HTTPException(status_code=410, detail="Link expired")

    if row["status"] == "active" and row["started_at"]:
        try:
            started_at = datetime.fromisoformat(row["started_at"])
            if datetime.utcnow() - started_at > timedelta(seconds=30):
                db.execute(
                    "UPDATE interviews SET status = 'pending' WHERE token = ? AND status = 'active'",
                    (token,),
                )
                db.commit()
                row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
        except ValueError:
            pass

    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail="Interview already completed or active")
    return row
