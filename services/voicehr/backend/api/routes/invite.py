from datetime import datetime, timedelta
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field

from backend.api.deps import get_db
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


def require_rws_service_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if x_api_key != settings.rws_service_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing service API key")


@router.post("/invite")
def create_invite(
    payload: InviteRequest,
    db=Depends(get_db),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    _auth: None = Depends(require_rws_service_key),
):
    """
    Create an interview invitation.

    If Idempotency-Key is provided and matches an existing interview, that interview's
    link is returned unchanged instead of creating a new one -- this makes a retried
    invite call (e.g. after RWS never received the response to a call VoiceHR actually
    processed) safe to repeat with the same key. A genuine re-invitation (a new attempt
    number from RWS) gets a new key and therefore a new interview, as intended.
    """
    if idempotency_key:
        existing = db.execute(
            "SELECT * FROM interviews WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing:
            link = f"{settings.base_url}/interview?token={existing['token']}"
            return {
                "link": link,
                "interview_link": link,
                "expires_at": existing["expires_at"],
                "external_reference_id": existing["external_reference_id"],
            }

    token = generate_token()
    now = datetime.utcnow()
    expires_at = now + timedelta(days=7)

    db.execute(
        """INSERT INTO interviews
           (token, candidate_name, candidate_email, language, status, created_at, expires_at,
            external_reference_id, idempotency_key)
           VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
        (
            token, payload.candidate_name, payload.candidate_email, payload.language,
            now.isoformat(), expires_at.isoformat(), payload.external_reference_id, idempotency_key,
        ),
    )
    db.commit()

    link = f"{settings.base_url}/interview?token={token}"
    return {
        "link": link,
        "interview_link": link,
        "expires_at": expires_at.isoformat(),
        "external_reference_id": payload.external_reference_id,
    }
