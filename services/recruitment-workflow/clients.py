"""
Thin HTTP client for RWS -> Second Brain calls.

Retrieval is recall only: it returns candidate_id + similarity_score and nothing else.
RWS then fetches each candidate's skills/experience/titles via a second call to compute
the deterministic score. RWS performs all qualification decisions itself.

send_candidate_feedback is the recruiter-decision write-back: fire-and-forget from a
FastAPI BackgroundTask, never awaited on the recruiter's accept/reject response path.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from shared.http_client import ServiceCallError, call_service


class SecondBrainClientError(Exception):
    """Raised when a Second Brain call fails at the transport level."""


async def retrieve_candidates(
    base_url: str, api_key: str, query_text: str, top_k: int
) -> list[dict]:
    """Returns a list of {"candidate_id": str, "similarity_score": float}."""
    try:
        resp = await call_service(
            "POST",
            f"{base_url}/v1/retrieve/candidates",
            api_key=api_key,
            json={"query_text": query_text, "top_k": top_k},
        )
    except ServiceCallError as exc:
        raise SecondBrainClientError(str(exc)) from exc

    if resp.status_code != 200:
        raise SecondBrainClientError(
            f"Second Brain retrieval returned {resp.status_code}: {resp.text}"
        )
    return resp.json()["matches"]


async def get_candidate(
    base_url: str, api_key: str, candidate_id: uuid.UUID
) -> Optional[dict]:
    """Returns candidate knowledge dict, or None if the candidate does not exist."""
    try:
        resp = await call_service(
            "GET",
            f"{base_url}/v1/knowledge/candidates/{candidate_id}",
            api_key=api_key,
        )
    except ServiceCallError as exc:
        raise SecondBrainClientError(str(exc)) from exc

    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise SecondBrainClientError(
            f"Second Brain candidate lookup returned {resp.status_code}: {resp.text}"
        )
    return resp.json()


async def send_candidate_feedback(
    base_url: str,
    api_key: str,
    candidate_id: uuid.UUID,
    application_id: uuid.UUID,
    decision: str,
    job_title: str,
    matched_requirements: list[str],
    missing_requirements: list[str],
    final_score: float,
    decided_at: datetime,
) -> None:
    """
    Write back a recruiter's accept/reject decision as PII-free knowledge feedback.
    Raises SecondBrainClientError on any transport failure or non-200 response --
    callers running this as a background task must catch it themselves (see
    main.py's _send_candidate_feedback_background), since there is no HTTP response
    left to report failure to by the time this runs.
    """
    try:
        resp = await call_service(
            "POST",
            f"{base_url}/v1/knowledge/candidates/{candidate_id}/feedback",
            api_key=api_key,
            json={
                "application_id": str(application_id),
                "decision": decision,
                "job_title": job_title,
                "matched_requirements": matched_requirements,
                "missing_requirements": missing_requirements,
                "final_score": final_score,
                "decided_at": decided_at.isoformat(),
            },
        )
    except ServiceCallError as exc:
        raise SecondBrainClientError(str(exc)) from exc

    if resp.status_code != 200:
        raise SecondBrainClientError(
            f"Second Brain feedback write-back returned {resp.status_code}: {resp.text}"
        )
