"""
tests/test_webhook_delivery_id_binding.py
------------------------------------------
Audit finding M5: the webhook dedup key was taken from the **unsigned** `X-Delivery-Id`
header while `WebhookPayload.delivery_id` -- the same value, inside the HMAC-signed body
-- was parsed and then ignored.

The practical impact was small (the body cannot be altered, so a replay re-applies an
identical transition), but the security property was weaker than it looked: the value
that decides "have I already applied this?" was the one part of the request an attacker
could change freely. These tests pin the stronger property -- the dedup key is bound to
the signature -- so it cannot quietly regress.

Same no-real-Postgres monkeypatch pattern as the rest of this suite.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main

WEBHOOK_URL = "/v1/webhooks/voicehr"


def _sign(body: bytes) -> str:
    return hmac.new(
        main.settings.webhook_hmac_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


def _payload(delivery_id: str, external_reference_id: str | None = None) -> dict:
    return {
        "delivery_id": delivery_id,
        "external_reference_id": external_reference_id or str(uuid.uuid4()),
        "interview_status": "completed",
        # InterviewAssessment is extra="forbid" and deliberately narrow -- it carries
        # scores only, never transcript text, strengths/concerns or evidence quotes.
        "assessment": {
            "overall_score": 78,
            "cefr_level": "B2",
            "fluency_score": 80,
            "vocabulary_score": 75,
            "grammar_score": 72,
            "pronunciation_score": 82,
            "comprehension_score": 85,
            "inconclusive": False,
            "evaluation_status": "completed",
        },
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def _post(client: TestClient, body_dict: dict, header_delivery_id: str | None):
    body = json.dumps(body_dict).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Signature": _sign(body)}
    if header_delivery_id is not None:
        headers["X-Delivery-Id"] = header_delivery_id
    return client.post(WEBHOOK_URL, content=body, headers=headers)


@pytest.fixture
def client(monkeypatch):
    """Webhook-only harness: the endpoint authenticates by signature alone, so no
    recruiter/JWT plumbing is needed -- only a DB stand-in."""
    recorded: dict = {}

    def fake_insert_webhook_event(db, **kwargs):
        recorded.update(kwargs)
        return SimpleNamespace(id=uuid.uuid4(), **kwargs)

    monkeypatch.setattr(main, "insert_webhook_event", fake_insert_webhook_event)
    # The external_reference_id is a random UUID with no matching application, so the
    # handler returns "no_op" after recording the receipt -- which is all these tests
    # need. record_interview_result is stubbed so no real DB work is attempted.
    monkeypatch.setattr(main, "record_interview_result", lambda *a, **k: None)

    def fake_get_db():
        yield SimpleNamespace(
            commit=lambda: None, rollback=lambda: None, flush=lambda: None,
            add=lambda *a, **k: None, refresh=lambda *a, **k: None,
        )

    main.app.dependency_overrides[main.get_db] = fake_get_db
    with TestClient(main.app) as c:
        c.recorded = recorded
        yield c
    main.app.dependency_overrides.clear()


def test_matching_delivery_id_is_accepted(client):
    """The real sender (VoiceHR) puts the same value in the body and the header."""
    did = str(uuid.uuid4())
    resp = _post(client, _payload(did), header_delivery_id=did)
    assert resp.status_code == 200, resp.text


def test_dedup_key_comes_from_the_signed_body_not_the_header(client):
    """The value persisted as the dedup key must be the one covered by the HMAC."""
    did = str(uuid.uuid4())
    resp = _post(client, _payload(did), header_delivery_id=did)
    assert resp.status_code == 200, resp.text
    assert client.recorded["delivery_id"] == did


def test_mismatched_delivery_id_is_rejected(client):
    """An attacker can rewrite the header freely -- the body is signed and cannot be
    rewritten. If the two disagree, the request is not what VoiceHR sent."""
    resp = _post(client, _payload("body-value"), header_delivery_id="header-value")
    assert resp.status_code == 422, resp.text
    assert "delivery_id" in resp.text.lower()
    assert client.recorded == {}, "nothing may be recorded for a mismatched delivery"


def test_missing_header_is_still_rejected(client):
    """Unchanged behaviour: the header remains required, so existing senders that omit
    it keep failing loudly rather than silently losing dedup."""
    resp = _post(client, _payload(str(uuid.uuid4())), header_delivery_id=None)
    assert resp.status_code == 422, resp.text


def test_body_without_delivery_id_is_rejected(client):
    """delivery_id is required by WebhookPayload; a body lacking it cannot be deduped."""
    payload = _payload(str(uuid.uuid4()))
    del payload["delivery_id"]
    resp = _post(client, payload, header_delivery_id="anything")
    assert resp.status_code == 422, resp.text
