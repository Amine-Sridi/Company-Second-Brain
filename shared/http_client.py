# Shared service-to-service HTTP client (API-key auth, timeout).
#
# First real caller: Recruitment Workflow Service calling Second Brain's retrieval and
# candidate-lookup endpoints. Kept intentionally thin — one async function, no retry/circuit
# breaker machinery — per the bootstrap's "boring over clever" rule; add real retry logic only
# if a concrete failure mode in this project demonstrates the need for it.
#
# `files`/`data` support multipart forwarding (e.g. RWS forwarding a recruiter's uploaded
# resume file to Resume Intelligence) using this same client rather than a second, bespoke
# HTTP abstraction just for uploads.
#
# Every call is logged (method, target host+path, status/outcome, duration_ms) via
# shared.observability.log_external_call, and propagates the current request's
# correlation id downstream via the X-Correlation-ID header. Never logs headers, the
# request/response body (including any file content), or the API key.
from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx

from shared.observability import CORRELATION_ID_HEADER, Timer, get_correlation_id, log_external_call

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10.0


class ServiceCallError(Exception):
    """Raised when a service-to-service HTTP call fails at the transport level (network,
    timeout, connection refused). Non-2xx HTTP responses are NOT raised as errors here —
    callers inspect response.status_code themselves, since a 404 or 422 is often a normal,
    meaningful outcome (e.g. "candidate not found") rather than a failure."""


def _target_and_path(url: str) -> tuple[str, str]:
    """Splits a URL into (host, path) for logging -- never includes the query string,
    which on these internal APIs could in principle carry identifiers we don't want to
    assume are safe to log."""
    parts = urlsplit(url)
    return parts.netloc, parts.path


async def call_service(
    method: str,
    url: str,
    *,
    api_key: str,
    json: dict | None = None,
    params: dict | None = None,
    files: dict | None = None,
    data: dict | None = None,
    extra_headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> httpx.Response:
    """Make an authenticated service-to-service HTTP call and return the raw response.

    extra_headers is merged on top of the x-api-key header -- e.g. an Idempotency-Key
    for calls that need attempt-scoped idempotency (it cannot itself be the auth
    mechanism, so it is always sent alongside, never instead of, x-api-key).

    files/data forward a multipart/form-data body (e.g. {"file": (filename, content,
    content_type)}) -- mutually exclusive with json in practice, same as httpx itself.
    The multipart Content-Type/boundary header is set by httpx automatically; nothing
    here needs to touch it.
    """
    headers = {"x-api-key": api_key}
    correlation_id = get_correlation_id()
    if correlation_id:
        headers[CORRELATION_ID_HEADER] = correlation_id
    headers.update(extra_headers or {})

    target, path = _target_and_path(url)

    with Timer() as timer:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method, url, json=json, params=params,
                    files=files, data=data, headers=headers,
                )
        except httpx.HTTPError as exc:
            log_external_call(
                logger,
                target=target,
                operation=f"{method} {path}",
                duration_ms=timer.duration_ms,
                outcome="error",
                error_type=type(exc).__name__,
            )
            raise ServiceCallError(
                f"{method} {url} failed: {type(exc).__name__}: {exc}"
            ) from exc

    log_external_call(
        logger,
        target=target,
        operation=f"{method} {path}",
        duration_ms=timer.duration_ms,
        outcome="success" if response.status_code < 500 else "server_error",
        status_code=response.status_code,
    )
    return response
