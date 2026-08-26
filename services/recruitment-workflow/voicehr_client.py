"""
Thin HTTP client for RWS -> VoiceHR calls.

Only the invitation call exists here. VoiceHR notifies RWS of interview completion via
its own outbound signed webhook (received at POST /v1/webhooks/voicehr in main.py) --
RWS never polls VoiceHR for interview status.
"""
from __future__ import annotations

from shared.http_client import ServiceCallError, call_service


class VoiceHRClientError(Exception):
    """Raised when a VoiceHR call fails at the transport level, or VoiceHR returns an
    unexpected (non-2xx, non-error-shaped) response."""


async def invite_candidate(
    base_url: str,
    api_key: str,
    idempotency_key: str,
    candidate_name: str,
    candidate_email: str,
    language: str,
    external_reference_id: str,
) -> dict:
    """
    Returns {"interview_link": str, "expires_at": str, "external_reference_id": str}.

    idempotency_key is attempt-scoped ("{application_id}:{attempt_number}"): calling
    this twice with the same key is safe and returns the same interview -- see
    record_invite_attempt in db.py for why attempt_number is only persisted after this
    call succeeds, which is what makes a retry after a lost response reuse the same key.
    """
    try:
        resp = await call_service(
            "POST",
            f"{base_url}/api/invite",
            api_key=api_key,
            json={
                "candidate_name": candidate_name,
                "candidate_email": candidate_email,
                "language": language,
                "external_reference_id": external_reference_id,
            },
            extra_headers={"Idempotency-Key": idempotency_key},
        )
    except ServiceCallError as exc:
        raise VoiceHRClientError(str(exc)) from exc

    if resp.status_code != 200:
        raise VoiceHRClientError(f"VoiceHR invite returned {resp.status_code}: {resp.text}")
    return resp.json()
