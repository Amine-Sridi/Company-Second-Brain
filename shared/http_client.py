# Shared service-to-service HTTP client (API-key auth, timeout).
#
# First real caller: Recruitment Workflow Service calling Second Brain's retrieval and
# candidate-lookup endpoints. Kept intentionally thin — one async function, no retry/circuit
# breaker machinery — per the bootstrap's "boring over clever" rule; add real retry logic only
# if a concrete failure mode in this project demonstrates the need for it.
from __future__ import annotations

import httpx

DEFAULT_TIMEOUT_SECONDS = 10.0


class ServiceCallError(Exception):
    """Raised when a service-to-service HTTP call fails at the transport level (network,
    timeout, connection refused). Non-2xx HTTP responses are NOT raised as errors here —
    callers inspect response.status_code themselves, since a 404 or 422 is often a normal,
    meaningful outcome (e.g. "candidate not found") rather than a failure."""


async def call_service(
    method: str,
    url: str,
    *,
    api_key: str,
    json: dict | None = None,
    params: dict | None = None,
    extra_headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> httpx.Response:
    """Make an authenticated service-to-service HTTP call and return the raw response.

    extra_headers is merged on top of the x-api-key header -- e.g. an Idempotency-Key
    for calls that need attempt-scoped idempotency (it cannot itself be the auth
    mechanism, so it is always sent alongside, never instead of, x-api-key).
    """
    headers = {"x-api-key": api_key, **(extra_headers or {})}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(
                method, url, json=json, params=params, headers=headers
            )
    except httpx.HTTPError as exc:
        raise ServiceCallError(
            f"{method} {url} failed: {type(exc).__name__}: {exc}"
        ) from exc
