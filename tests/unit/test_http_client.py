"""
Unit tests for shared.http_client.call_service.

Uses httpx's MockTransport (no real network access) via monkeypatching
shared.http_client.httpx.AsyncClient to always use it.
"""
import httpx
import pytest

import shared.http_client as hc


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
