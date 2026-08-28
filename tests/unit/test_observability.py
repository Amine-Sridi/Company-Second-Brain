"""
Unit tests for shared.observability: JSON log formatting, correlation ID propagation,
and the external-call logging helper.
"""
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.observability import (
    CORRELATION_ID_HEADER,
    CorrelationIdMiddleware,
    JsonFormatter,
    Timer,
    get_correlation_id,
    log_external_call,
    new_correlation_id,
    set_correlation_id,
)


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------

def _make_record(msg="hello", level=logging.INFO, **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger", level=level, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_produces_valid_json():
    formatter = JsonFormatter("my-service")
    output = formatter.format(_make_record())
    parsed = json.loads(output)  # must not raise
    assert parsed["message"] == "hello"
    assert parsed["service"] == "my-service"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test.logger"
    assert "timestamp" in parsed


def test_json_formatter_includes_correlation_id_when_set():
    set_correlation_id("corr-abc-123")
    try:
        formatter = JsonFormatter("my-service")
        parsed = json.loads(formatter.format(_make_record()))
        assert parsed["correlation_id"] == "corr-abc-123"
    finally:
        set_correlation_id(None)


def test_json_formatter_omits_correlation_id_when_unset():
    set_correlation_id(None)
    formatter = JsonFormatter("my-service")
    parsed = json.loads(formatter.format(_make_record()))
    assert "correlation_id" not in parsed


def test_json_formatter_includes_extra_fields():
    formatter = JsonFormatter("my-service")
    parsed = json.loads(formatter.format(_make_record(candidate_id="c-1", duration_ms=12.3)))
    assert parsed["candidate_id"] == "c-1"
    assert parsed["duration_ms"] == 12.3


def test_json_formatter_nested_extra_dict_round_trips():
    formatter = JsonFormatter("my-service")
    parsed = json.loads(
        formatter.format(_make_record(external_call={"target": "second-brain", "duration_ms": 5.0}))
    )
    assert parsed["external_call"]["target"] == "second-brain"


def test_json_formatter_handles_exception_info():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = logging.LogRecord(
            name="test.logger", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="failed", args=(), exc_info=sys.exc_info(),
        )
    formatter = JsonFormatter("my-service")
    parsed = json.loads(formatter.format(record))
    assert "ValueError" in parsed["exception"]
    assert "boom" in parsed["exception"]


# ---------------------------------------------------------------------------
# Correlation ID contextvar
# ---------------------------------------------------------------------------

def test_new_correlation_id_generates_unique_values():
    a = new_correlation_id()
    b = new_correlation_id()
    assert a != b
    assert len(a) > 0


def test_get_set_correlation_id_roundtrip():
    set_correlation_id("my-id")
    try:
        assert get_correlation_id() == "my-id"
    finally:
        set_correlation_id(None)


# ---------------------------------------------------------------------------
# CorrelationIdMiddleware
# ---------------------------------------------------------------------------

@pytest.fixture
def app_client():
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/echo")
    def echo():
        return {"correlation_id": get_correlation_id()}

    return TestClient(app)


def test_middleware_generates_id_when_absent(app_client):
    resp = app_client.get("/echo")
    assert resp.status_code == 200
    header_id = resp.headers[CORRELATION_ID_HEADER]
    assert header_id
    assert resp.json()["correlation_id"] == header_id


def test_middleware_echoes_incoming_id(app_client):
    resp = app_client.get("/echo", headers={CORRELATION_ID_HEADER: "caller-supplied-id"})
    assert resp.headers[CORRELATION_ID_HEADER] == "caller-supplied-id"
    assert resp.json()["correlation_id"] == "caller-supplied-id"


def test_middleware_generates_different_ids_for_different_requests(app_client):
    first = app_client.get("/echo").headers[CORRELATION_ID_HEADER]
    second = app_client.get("/echo").headers[CORRELATION_ID_HEADER]
    assert first != second


# ---------------------------------------------------------------------------
# Timer
# ---------------------------------------------------------------------------

def test_timer_duration_is_nonnegative():
    with Timer() as timer:
        pass
    assert timer.duration_ms >= 0.0


def test_timer_duration_readable_inside_block():
    with Timer() as timer:
        first = timer.duration_ms
        second = timer.duration_ms
    assert first >= 0.0
    assert second >= first


# ---------------------------------------------------------------------------
# log_external_call
# ---------------------------------------------------------------------------

def test_log_external_call_success_logs_at_info(caplog):
    logger = logging.getLogger("test.external_call.success")
    with caplog.at_level(logging.INFO, logger="test.external_call.success"):
        log_external_call(
            logger, target="second-brain", operation="POST /v1/retrieve/candidates",
            duration_ms=42.0, outcome="success", status_code=200,
        )
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelname == "INFO"
    assert record.external_call["target"] == "second-brain"
    assert record.external_call["duration_ms"] == 42.0
    assert record.external_call["status_code"] == 200


def test_log_external_call_failure_logs_at_warning(caplog):
    logger = logging.getLogger("test.external_call.failure")
    with caplog.at_level(logging.WARNING, logger="test.external_call.failure"):
        log_external_call(
            logger, target="voicehr", operation="POST /api/invite",
            duration_ms=5000.0, outcome="error", error_type="ConnectTimeout",
        )
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelname == "WARNING"
    assert record.external_call["error_type"] == "ConnectTimeout"
    assert "status_code" not in record.external_call
