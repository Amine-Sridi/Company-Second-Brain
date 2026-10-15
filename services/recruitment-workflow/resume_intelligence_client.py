"""
Thin HTTP client for RWS -> Resume Intelligence calls.

get_contact: a pre-flight check before inviting a candidate to interview, since
VoiceHR needs a name and email. The response is PII-bearing and must be used
transiently -- RWS passes it straight to VoiceHR and never persists it (see
docs/ARCHITECTURE_DECISIONS.md: ContactLookupResponse is used transiently by RWS, never stored).

upload_resume: forwards a recruiter's uploaded resume file to Resume Intelligence's
own service-key-protected POST /v1/resumes. This is the only way a resume reaches
Resume Intelligence in this architecture -- the recruiter-facing JWT boundary lives
entirely on RWS's own endpoint (see main.py's upload_candidate_resume); Resume
Intelligence itself never sees or validates a recruiter JWT, only this service key.
"""
from __future__ import annotations

import uuid
from typing import Optional

import httpx

from shared.http_client import ServiceCallError, call_service


class ResumeIntelligenceClientError(Exception):
    """Raised when a Resume Intelligence call fails at the transport level, or returns
    an unexpected (non-200, non-404) response."""


# Resume ingestion runs real extraction plus up to 3 bounded Gemini structured-extraction
# attempts (each with its own network round trip) before Second Brain is even called --
# shared.http_client.call_service's default 10s timeout is tuned for a plain JSON API
# call and is routinely too short for this one, so it is overridden here specifically
# rather than raising the shared default for every other call in the codebase.
_UPLOAD_TIMEOUT_SECONDS = 90.0


async def get_contact(
    base_url: str, api_key: str, candidate_id: uuid.UUID
) -> Optional[dict]:
    """Returns {"candidate_id", "name", "email", "phone"}, or None if the candidate has
    no contact record (404) -- callers must not call VoiceHR in that case."""
    try:
        resp = await call_service(
            "GET",
            f"{base_url}/v1/candidates/{candidate_id}/contact",
            api_key=api_key,
        )
    except ServiceCallError as exc:
        raise ResumeIntelligenceClientError(str(exc)) from exc

    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        # Deliberately excludes resp.text: this is the PII-owning service's own
        # contact endpoint, and main.py persists this exception's message into
        # recruitment.interview_rounds.notification_error (see
        # interview_round_mailer._send_email's own docstring for the same
        # constraint). The status code alone is enough to act on.
        raise ResumeIntelligenceClientError(
            f"Resume Intelligence contact lookup returned {resp.status_code}"
        )
    return resp.json()


async def list_candidates(base_url: str, api_key: str) -> list[dict]:
    """Returns Resume Intelligence's newest-first candidate list, each item shaped
    {"candidate_id", "name", "email", "created_at"} -- callers must not persist
    these dicts (see main.py's get_candidates, which has no db dependency at all)."""
    try:
        resp = await call_service("GET", f"{base_url}/v1/candidates", api_key=api_key)
    except ServiceCallError as exc:
        raise ResumeIntelligenceClientError(str(exc)) from exc

    if resp.status_code != 200:
        raise ResumeIntelligenceClientError(
            f"Resume Intelligence candidate list returned {resp.status_code}"
        )
    return resp.json()


async def upload_resume(
    base_url: str, api_key: str, filename: str, content: bytes, content_type: str
) -> httpx.Response:
    """
    Forward a resume file to Resume Intelligence's POST /v1/resumes and return its raw
    response untouched.

    Deliberately does NOT collapse Resume Intelligence's response into a single
    success/failure shape -- callers (main.py's upload_candidate_resume) preserve its
    actual status code and body, since a 422 (unsupported file type -- a problem with
    the upload itself) and a 502 (Gemini/Second Brain integration failure) are
    meaningfully different outcomes for the recruiter client, not one generic error.

    Only raises ResumeIntelligenceClientError for a transport-level failure (Resume
    Intelligence unreachable) -- never for an HTTP-level error response.
    """
    try:
        return await call_service(
            "POST",
            f"{base_url}/v1/resumes",
            api_key=api_key,
            files={"file": (filename, content, content_type)},
            timeout=_UPLOAD_TIMEOUT_SECONDS,
        )
    except ServiceCallError as exc:
        raise ResumeIntelligenceClientError(str(exc)) from exc
