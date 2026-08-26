"""
Thin HTTP client for RWS -> Resume Intelligence calls.

Only the contact lookup exists here: a pre-flight check before inviting a candidate to
interview, since VoiceHR needs a name and email. The response is PII-bearing and must
be used transiently -- RWS passes it straight to VoiceHR and never persists it (see
CLAUDE.md: ContactLookupResponse is used transiently by RWS, never stored).
"""
from __future__ import annotations

import uuid
from typing import Optional

from shared.http_client import ServiceCallError, call_service


class ResumeIntelligenceClientError(Exception):
    """Raised when a Resume Intelligence call fails at the transport level, or returns
    an unexpected (non-200, non-404) response."""


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
        raise ResumeIntelligenceClientError(
            f"Resume Intelligence contact lookup returned {resp.status_code}: {resp.text}"
        )
    return resp.json()
