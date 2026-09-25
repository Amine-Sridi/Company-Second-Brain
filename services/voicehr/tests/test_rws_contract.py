"""
tests/test_rws_contract.py
---------------------------
Contract tests: RWS's real HTTP client against VoiceHR's real ASGI app.

The gap this closes. Everywhere else in this repository, a cross-service call is
tested with the *client module monkeypatched away* -- the repo-root "integration"
suite replaces voicehr_client.invite_candidate / resend_invite /
resend_interview_result wholesale, and VoiceHR's own suite stubs the outbound
webhook. Both sides are therefore well covered in isolation, and nothing at all
checks that they agree: a renamed route, a changed auth header, or a status code
one side stopped returning would pass every existing test and only break live.

So this drives the genuine RWS client (URL construction, `x-api-key` header,
status-code handling in services/recruitment-workflow/voicehr_client.py) against
the genuine VoiceHR routes, with only the network itself replaced by
httpx.ASGITransport. Nothing about either side's logic is stubbed.

Scoped deliberately to the result re-delivery boundary added for the audit's phase
2 -- the newest and least-exercised one. The `_route_through_asgi` helper below is
the pattern to reuse when extending this to the invite/resend-invite/report calls.
"""
import asyncio
import pathlib
import sys

import httpx
import pytest

# RWS is a separate service with its own directory; its thin client (and the shared
# http_client it uses) are not otherwise importable from VoiceHR's own test run.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
for _path in (_REPO_ROOT, _REPO_ROOT / "services" / "recruitment-workflow"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import voicehr_client  # noqa: E402  (needs the sys.path setup above)
from voicehr_client import VoiceHRClientError  # noqa: E402

from tests.conftest import TEST_RWS_SERVICE_KEY, create_interview, create_report  # noqa: E402

# Any absolute base URL works -- ASGITransport dispatches on the path, never the host.
_BASE_URL = "http://voicehr.test"


def _route_through_asgi(app, monkeypatch):
    """Makes shared.http_client.call_service reach `app` in-process instead of the
    network. Only the transport is swapped: the client still builds its own URL,
    headers, and error handling exactly as it does in production."""
    import shared.http_client as http_client

    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.ASGITransport(app=app)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(http_client.httpx, "AsyncClient", _factory)


def _delivered_to_rws(return_value=True):
    """Stubs only the final hop (VoiceHR -> RWS webhook), which is the other side of
    the system and has its own tests; everything up to it stays real."""
    from unittest.mock import AsyncMock, patch

    return patch(
        "backend.services.rws_client.notify_rws_interview_result",
        new=AsyncMock(return_value=return_value),
    )


def test_resend_result_client_and_route_agree(client, db, monkeypatch):
    """The happy path across the real boundary: RWS's client reaches VoiceHR's real
    route, authenticates, and gets a 200 it accepts."""
    create_interview(db, token="contract-ok", external_reference_id="app-contract-ok")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", ("contract-ok",)).fetchone()
    create_report(db, row["id"], report_token="contract-ok-report", overall_score=77, cefr_level="B2")
    _route_through_asgi(client.app, monkeypatch)

    with _delivered_to_rws() as mock_notify:
        asyncio.run(
            voicehr_client.resend_interview_result(
                _BASE_URL, TEST_RWS_SERVICE_KEY, "app-contract-ok"
            )
        )

    # Reaching this point means the path, the x-api-key header name, and the 200
    # contract all line up -- none of which any mocked test can confirm.
    assert mock_notify.call_args.kwargs["assessment"]["cefr_level"] == "B2"


def test_resend_result_rejects_a_wrong_service_key_across_the_boundary(client, db, monkeypatch):
    """Proves the auth header RWS sends is the one VoiceHR actually checks -- a
    mismatch here would otherwise only show up in production."""
    create_interview(db, token="contract-auth", external_reference_id="app-contract-auth")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", ("contract-auth",)).fetchone()
    create_report(db, row["id"], report_token="contract-auth-report")
    _route_through_asgi(client.app, monkeypatch)

    with pytest.raises(VoiceHRClientError):
        asyncio.run(
            voicehr_client.resend_interview_result(
                _BASE_URL, "definitely-not-the-key", "app-contract-auth"
            )
        )


def test_resend_result_surfaces_no_result_yet_across_the_boundary(client, db, monkeypatch):
    """VoiceHR answers 409 when the interview hasn't produced a report; RWS's client
    must treat that as a failure rather than silently succeeding."""
    create_interview(db, token="contract-409", external_reference_id="app-contract-409")
    _route_through_asgi(client.app, monkeypatch)

    with pytest.raises(VoiceHRClientError):
        asyncio.run(
            voicehr_client.resend_interview_result(
                _BASE_URL, TEST_RWS_SERVICE_KEY, "app-contract-409"
            )
        )


def test_resend_result_surfaces_unknown_interview_across_the_boundary(client, db, monkeypatch):
    _route_through_asgi(client.app, monkeypatch)

    with pytest.raises(VoiceHRClientError):
        asyncio.run(
            voicehr_client.resend_interview_result(
                _BASE_URL, TEST_RWS_SERVICE_KEY, "no-such-application"
            )
        )
