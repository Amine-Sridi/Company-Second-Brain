"""
Unit tests for shared.http_client.call_service.

Uses httpx's MockTransport (no real network access) via monkeypatching
shared.http_client.httpx.AsyncClient to always use it.
"""
import logging

import httpx
import pytest

import shared.http_client as hc
from shared.observability import CORRELATION_ID_HEADER, set_correlation_id


def _patch_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(hc.httpx.AsyncClient, "__init__", patched_init)


@pytest.mark.asyncio
async def test_call_service_sends_api_key_header(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json={"ok": True})

    _patch_transport(monkeypatch, handler)

    resp = await hc.call_service("GET", "http://svc.test/x", api_key="k-123")

    assert resp.status_code == 200
    assert captured["headers"]["x-api-key"] == "k-123"
    assert "idempotency-key" not in captured["headers"]


@pytest.mark.asyncio
async def test_call_service_merges_extra_headers_with_api_key(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json={"ok": True})

    _patch_transport(monkeypatch, handler)

    resp = await hc.call_service(
        "POST",
        "http://svc.test/x",
        api_key="k-123",
        extra_headers={"Idempotency-Key": "app-1:1"},
    )

    assert resp.status_code == 200
    assert captured["headers"]["x-api-key"] == "k-123"
    assert captured["headers"]["idempotency-key"] == "app-1:1"


@pytest.mark.asyncio
async def test_call_service_wraps_transport_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _patch_transport(monkeypatch, handler)

    with pytest.raises(hc.ServiceCallError):
        await hc.call_service("GET", "http://svc.test/x", api_key="k-123")


# ---------------------------------------------------------------------------
# Correlation ID propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_service_propagates_correlation_id(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json={"ok": True})

    _patch_transport(monkeypatch, handler)
    set_correlation_id("corr-xyz")
    try:
        await hc.call_service("GET", "http://svc.test/x", api_key="k-123")
    finally:
        set_correlation_id(None)

    assert captured["headers"][CORRELATION_ID_HEADER.lower()] == "corr-xyz"


@pytest.mark.asyncio
async def test_call_service_omits_correlation_id_header_when_unset(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json={"ok": True})

    _patch_transport(monkeypatch, handler)
    set_correlation_id(None)

    await hc.call_service("GET", "http://svc.test/x", api_key="k-123")

    assert CORRELATION_ID_HEADER.lower() not in captured["headers"]


# ---------------------------------------------------------------------------
# Latency/failure logging + no secret leakage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_service_logs_latency_on_success(monkeypatch, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    _patch_transport(monkeypatch, handler)

    with caplog.at_level(logging.INFO, logger="shared.http_client"):
        await hc.call_service(
            "GET", "http://second-brain.internal/v1/retrieve/candidates", api_key="super-secret-key"
        )

    records = [r for r in caplog.records if r.name == "shared.http_client"]
    assert len(records) == 1
    call = records[0].external_call
    assert call["outcome"] == "success"
    assert call["status_code"] == 200
    assert isinstance(call["duration_ms"], float)
    assert call["operation"] == "GET /v1/retrieve/candidates"


@pytest.mark.asyncio
async def test_call_service_logs_failure_with_error_type_only(monkeypatch, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _patch_transport(monkeypatch, handler)

    with caplog.at_level(logging.WARNING, logger="shared.http_client"):
        with pytest.raises(hc.ServiceCallError):
            await hc.call_service("GET", "http://svc.test/x", api_key="super-secret-key")

    records = [r for r in caplog.records if r.name == "shared.http_client"]
    assert len(records) == 1
    call = records[0].external_call
    assert call["outcome"] == "error"
    assert call["error_type"] == "ConnectError"


@pytest.mark.asyncio
async def test_call_service_never_logs_api_key(monkeypatch, caplog):
    """The API key must never appear anywhere in the emitted log output, success or
    failure -- log_external_call is only ever given method/path/status/duration/error
    type, never headers, but this asserts that guarantee end-to-end."""
    secret_key = "sk-super-secret-value-12345"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    _patch_transport(monkeypatch, handler)

    with caplog.at_level(logging.INFO, logger="shared.http_client"):
        await hc.call_service(
            "POST", "http://svc.test/v1/knowledge/candidates", api_key=secret_key,
            json={"candidate_id": "11111111-1111-4111-8111-111111111111"},
        )

    full_output = "\n".join(caplog.messages) + "\n".join(
        str(r.__dict__) for r in caplog.records
    )
    assert secret_key not in full_output
