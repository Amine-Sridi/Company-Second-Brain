"""
Thin HTTP client for Resume Intelligence -> Second Brain.

Only one call exists here: pushing the PII-safe CandidateSummary built after a
resume is successfully extracted and stored. Uses the same
shared.http_client.call_service pattern as every other cross-service call in
this project (RWS -> Second Brain, RWS -> Resume Intelligence, RWS -> VoiceHR)
-- no new HTTP abstraction. Correlation ID propagation is handled entirely by
call_service itself (reads the current request's contextvar), so nothing here
needs to touch it.
"""
from __future__ import annotations

from shared.http_client import ServiceCallError, call_service
from shared.schemas import CandidateSummary


class SecondBrainClientError(Exception):
    """Raised when the Second Brain ingestion call fails at the transport level
    or returns a non-200 response. Callers must not delete or roll back the
    already-stored Resume Intelligence candidate on this error -- see
    api.py's ingest_resume."""


async def send_candidate_summary(
    base_url: str, api_key: str, summary: CandidateSummary
) -> None:
    """POST summary to Second Brain's POST /v1/knowledge/candidates.

    Second Brain upserts by candidate_id, so calling this again with the same
    summary (e.g. a retried resume upload that dedups to the same
    candidate_id) is safe and idempotent -- no dedup/idempotency logic is
    needed here.
    """
    try:
        resp = await call_service(
            "POST",
            f"{base_url}/v1/knowledge/candidates",
            api_key=api_key,
            json=summary.model_dump(mode="json"),
        )
    except ServiceCallError as exc:
        raise SecondBrainClientError(str(exc)) from exc

    if resp.status_code != 200:
        raise SecondBrainClientError(
            f"Second Brain candidate ingestion returned {resp.status_code}"
        )
