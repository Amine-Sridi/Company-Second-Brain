"""
Shared observability primitives: structured JSON logging, request correlation IDs,
and external-call latency/failure logging.

Hard rule enforced by construction everywhere this module is used: nothing here ever
logs a request/response body, a header value, a prompt, or free-text content (resume
text, JD text, interview transcripts/assessments). Only identifiers (correlation id,
UUIDs), method/path, status codes, durations, and exception *types* are logged. Callers
must not pass PII, secrets, or content into `extra=` on a logger configured with
JsonFormatter -- there is no automatic redaction, this is a discipline the tests in
tests/unit/test_observability.py exist to catch regressions on.
"""
from __future__ import annotations

import contextvars
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_ID_HEADER = "X-Correlation-ID"

_correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)

# Standard attributes every LogRecord already has -- anything else on the record came
# from an explicit `extra={...}` and should be surfaced in the JSON output.
_STANDARD_RECORD_ATTRS = frozenset(logging.LogRecord(
    "", 0, "", 0, "", (), None
).__dict__.keys()) | {"message", "asctime", "taskName"}


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def get_correlation_id() -> str | None:
    return _correlation_id_var.get()


def set_correlation_id(value: str) -> None:
    _correlation_id_var.set(value)


class JsonFormatter(logging.Formatter):
    """Renders each log record as a single JSON line."""

    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        correlation_id = get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id

        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_json_logging(service_name: str, level: str = "INFO") -> None:
    """
    Replace the root logger's handlers with a single JSON-formatted stream handler.
    Idempotent -- safe to call more than once (e.g. across test runs re-importing a
    service module) without stacking duplicate handlers.
    """
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service_name))
    root.addHandler(handler)
    root.setLevel(level)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Reads X-Correlation-ID from the incoming request, or generates one if absent, and
    makes it available to this request's log records via a contextvar for the
    remainder of the request -- including any FastAPI BackgroundTasks scheduled during
    it, since those run later in the same asyncio task, not a new one. Echoes the id
    back in the response header so a caller that generated one can confirm it, and one
    that didn't can capture it for its own logs.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        incoming = request.headers.get(CORRELATION_ID_HEADER)
        correlation_id = incoming or new_correlation_id()
        set_correlation_id(correlation_id)
        response = await call_next(request)
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response


def log_external_call(
    logger: logging.Logger,
    *,
    target: str,
    operation: str,
    duration_ms: float,
    outcome: str,
    status_code: int | None = None,
    error_type: str | None = None,
) -> None:
    """
    One structured log line per outbound call to another service or provider. Only
    identifiers/metrics -- never the request or response body.
    """
    extra = {
        "external_call": {
            "target": target,
            "operation": operation,
            "duration_ms": duration_ms,
            "outcome": outcome,
            **({"status_code": status_code} if status_code is not None else {}),
            **({"error_type": error_type} if error_type is not None else {}),
        }
    }
    if outcome == "success":
        logger.info("external_call", extra=extra)
    else:
        logger.warning("external_call_failed", extra=extra)


class Timer:
    """
    Tiny elapsed-time helper: `with Timer() as t: ...` then `t.duration_ms`.

    duration_ms is a live property, not a value frozen at __exit__ -- it must be
    readable *inside* the `with` block too (e.g. from an `except` clause that logs and
    re-raises before the block has exited), not only after it completes normally.
    """

    def __enter__(self) -> "Timer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc_info) -> None:
        return None

    @property
    def duration_ms(self) -> float:
        return round((time.monotonic() - self._start) * 1000, 1)
