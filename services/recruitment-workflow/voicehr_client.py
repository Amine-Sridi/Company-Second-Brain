"""
Thin HTTP client for RWS -> VoiceHR calls.

VoiceHR notifies RWS of interview completion via its own outbound signed webhook
(received at POST /v1/webhooks/voicehr in main.py) -- RWS never polls VoiceHR for
interview status. get_interview_report/get_interview_recording are pull calls the
recruiter UI triggers on demand (Reports pages), not part of that push flow.
"""
from __future__ import annotations

from typing import Optional

import httpx

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
    job_title: str | None = None,
) -> dict:
    """
    Returns {"interview_link": str, "expires_at": str, "external_reference_id": str}.

    idempotency_key is attempt-scoped ("{application_id}:{attempt_number}"): calling
    this twice with the same key is safe and returns the same interview -- see
    record_invite_attempt in db.py for why attempt_number is only persisted after this
    call succeeds, which is what makes a retry after a lost response reuse the same key.

    VoiceHR now emails the candidate their interview link automatically as part of this
    same call, and that delivery is what actually determines success here: a non-200
    response (raised below as VoiceHRClientError) means either the interview couldn't
    be created *or* the email failed to send -- either way, main.py's caller must not
    treat this as a successful invite. job_title is cosmetic only (the candidate
    email's greeting); VoiceHR has no other use for it.
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
                "job_title": job_title,
            },
            extra_headers={"Idempotency-Key": idempotency_key},
        )
    except ServiceCallError as exc:
        raise VoiceHRClientError(str(exc)) from exc

    if resp.status_code != 200:
        raise VoiceHRClientError(f"VoiceHR invite returned {resp.status_code}: {resp.text}")
    return resp.json()


async def resend_invite(base_url: str, api_key: str, external_reference_id: str) -> None:
    """
    Explicit resend: always attempts delivery on the interview VoiceHR already
    created for this application, never creates a new interview or accepts an
    Idempotency-Key -- the resend counterpart to invite_candidate's idempotent
    first-send-and-retry logic. Calls VoiceHR's service-key-gated
    /api/rws/interviews/{external_reference_id}/resend-invite (report.py:
    resend_rws_invite), never the unauthenticated token-keyed
    /interview/{token}/invite route, since RWS never stores that token.
    """
    try:
        resp = await call_service(
            "POST",
            f"{base_url}/api/rws/interviews/{external_reference_id}/resend-invite",
            api_key=api_key,
        )
    except ServiceCallError as exc:
        raise VoiceHRClientError(str(exc)) from exc

    if resp.status_code != 200:
        raise VoiceHRClientError(f"VoiceHR resend returned {resp.status_code}: {resp.text}")


async def get_interview_report(
    base_url: str, api_key: str, external_reference_id: str
) -> Optional[dict]:
    """
    Returns the full report for one interview, or None if no report exists yet
    (404) -- callers must not persist this; every field (including candidate_name/
    candidate_email, which are legitimately VoiceHR's own PII, not Resume
    Intelligence's) is relayed transiently to the recruiter UI and never written to
    an RWS table. Calls VoiceHR's own service-key-gated RWS-facing report endpoint,
    never the Basic-auth admin surface.
    """
    try:
        resp = await call_service(
            "GET",
            f"{base_url}/api/rws/interviews/{external_reference_id}/report",
            api_key=api_key,
        )
    except ServiceCallError as exc:
        raise VoiceHRClientError(str(exc)) from exc

    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise VoiceHRClientError(f"VoiceHR report lookup returned {resp.status_code}: {resp.text}")
    return resp.json()


async def get_interview_recording(
    base_url: str, api_key: str, external_reference_id: str
) -> Optional[httpx.Response]:
    """
    Returns the raw recording response (bytes + content-type), or None if no
    recording exists (404). The caller (main.py) streams this straight back to the
    browser without ever persisting it or revealing VoiceHR's own URL/filename
    structure -- see get_interview_recording's route docstring in main.py.
    """
    try:
        resp = await call_service(
            "GET",
            f"{base_url}/api/rws/interviews/{external_reference_id}/recording",
            api_key=api_key,
        )
    except ServiceCallError as exc:
        raise VoiceHRClientError(str(exc)) from exc

    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise VoiceHRClientError(f"VoiceHR recording lookup returned {resp.status_code}: {resp.text}")
    return resp
