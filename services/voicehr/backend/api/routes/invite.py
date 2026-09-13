from datetime import datetime, timedelta
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field

from backend.api.deps import get_db
from backend.api.routes.interview import deliver_invitation_email
from backend.core.config import settings
from backend.core.security import generate_token

router = APIRouter()


class InviteRequest(BaseModel):
    candidate_name: str = Field(min_length=2, max_length=100)
    candidate_email: EmailStr
    language: Literal["en", "fr"]
    # RWS's application_id, stored and echoed back in the outbound completion webhook.
    # Optional so this endpoint still works for non-RWS callers (e.g. the admin UI).
    external_reference_id: Optional[str] = Field(default=None, max_length=100)
    # RWS's Job.title -- used only for the candidate email's greeting (see
    # interview.py: _build_email_en/_build_email_fr). Optional for the same reason
    # external_reference_id is.
    job_title: Optional[str] = Field(default=None, max_length=200)


def require_rws_service_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if x_api_key != settings.rws_service_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing service API key")


@router.post("/invite")
async def create_invite(
    payload: InviteRequest,
    db=Depends(get_db),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    _auth: None = Depends(require_rws_service_key),
):
    """
    Create an interview invitation and email the candidate their secure link.

    If Idempotency-Key is provided and matches an existing interview, that interview
    (same token, same link) is reused instead of creating a new one -- this makes a
    retried invite call (e.g. after RWS never received the response to a call VoiceHR
    actually processed) safe to repeat with the same key. A genuine re-invitation (a
    new attempt number from RWS) gets a new key and therefore a new interview, as
    intended.

    Email delivery is what actually makes this endpoint succeed or fail. It is
    attempted synchronously, in this same request, via deliver_invitation_email
    (interview.py) -- skipped only when this exact interview's invite_email_sent_at is
    already set (a prior call on the same Idempotency-Key already delivered it; no
    automatic resend). On failure, deliver_invitation_email raises, and this endpoint
    returns that same non-2xx response instead of the success body below -- RWS's own
    invite_candidate then sees a non-200 and never calls record_invite_attempt, so a
    failed delivery can never look like a successful invite (see main.py in the RWS
    service). Retrying with the same key finds this same row again (invite_email_sent_at
    still NULL) and retries delivery on it -- never creates a second interview.
    """
    row = None
    if idempotency_key:
        row = db.execute(
            "SELECT * FROM interviews WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()

    if row is None:
        token = generate_token()
        now = datetime.utcnow()
        expires_at = now + timedelta(days=7)
        db.execute(
            """INSERT INTO interviews
               (token, candidate_name, candidate_email, language, status, created_at, expires_at,
                external_reference_id, idempotency_key, job_title)
               VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
            (
                token, payload.candidate_name, payload.candidate_email, payload.language,
                now.isoformat(), expires_at.isoformat(), payload.external_reference_id,
                idempotency_key, payload.job_title,
            ),
        )
        db.commit()
        row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    else:
        token = row["token"]

    if not row["invite_email_sent_at"]:
        await deliver_invitation_email(db, row, token)

    link = f"{settings.base_url}/interview?token={token}"
    return {
        "link": link,
        "interview_link": link,
        "expires_at": row["expires_at"],
        "external_reference_id": row["external_reference_id"],
    }
