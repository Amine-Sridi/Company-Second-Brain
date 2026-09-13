from datetime import datetime, timedelta

from fastapi import HTTPException

from tests.conftest import TEST_RWS_SERVICE_KEY, create_interview

_AUTH = {"x-api-key": TEST_RWS_SERVICE_KEY}

# mock_email_delivery (conftest.py, autouse) makes every POST /api/invite call below
# "succeed" at sending without touching the network by default. Tests that
# specifically exercise delivery failure override it by monkeypatching
# backend.api.routes.interview._send_email again inside the test body.


def test_create_invite_en(client, db):
    response = client.post("/api/invite", json={
        "candidate_name": "Alice Martin",
        "candidate_email": "alice@example.com",
        "language": "en"
    }, headers=_AUTH)
    assert response.status_code == 200
    data = response.json()
    assert "link" in data
    assert "expires_at" in data
    assert data["link"].startswith("http://testserver/interview?token=")

    row = db.execute("SELECT * FROM interviews WHERE candidate_email = ?", ("alice@example.com",)).fetchone()
    assert row is not None
    assert row["candidate_name"] == "Alice Martin"
    assert row["language"] == "en"
    assert row["status"] == "pending"


def test_create_invite_fr(client, db):
    response = client.post("/api/invite", json={
        "candidate_name": "Jean Dupont",
        "candidate_email": "jean@example.com",
        "language": "fr"
    }, headers=_AUTH)
    assert response.status_code == 200
    data = response.json()
    assert "link" in data

    row = db.execute("SELECT * FROM interviews WHERE candidate_email = ?", ("jean@example.com",)).fetchone()
    assert row is not None
    assert row["language"] == "fr"


def test_create_invite_name_too_short(client, db):
    response = client.post("/api/invite", json={
        "candidate_name": "A",
        "candidate_email": "test@example.com",
        "language": "en"
    }, headers=_AUTH)
    assert response.status_code == 422


def test_create_invite_invalid_email(client, db):
    response = client.post("/api/invite", json={
        "candidate_name": "Test User",
        "candidate_email": "not-an-email",
        "language": "en"
    }, headers=_AUTH)
    assert response.status_code == 422


def test_create_invite_invalid_language(client, db):
    response = client.post("/api/invite", json={
        "candidate_name": "Test User",
        "candidate_email": "test@example.com",
        "language": "es"
    }, headers=_AUTH)
    assert response.status_code == 422


def test_create_invite_missing_fields(client, db):
    response = client.post("/api/invite", json={}, headers=_AUTH)
    assert response.status_code == 422


def test_create_invite_generates_unique_tokens(client, db):
    response1 = client.post("/api/invite", json={
        "candidate_name": "User One",
        "candidate_email": "user1@example.com",
        "language": "en"
    }, headers=_AUTH)
    response2 = client.post("/api/invite", json={
        "candidate_name": "User Two",
        "candidate_email": "user2@example.com",
        "language": "en"
    }, headers=_AUTH)

    token1 = response1.json()["link"].split("token=")[1]
    token2 = response2.json()["link"].split("token=")[1]
    assert token1 != token2


def test_create_invite_expires_in_7_days(client, db):
    response = client.post("/api/invite", json={
        "candidate_name": "Test User",
        "candidate_email": "test@example.com",
        "language": "en"
    }, headers=_AUTH)
    expires_at = datetime.fromisoformat(response.json()["expires_at"])
    now = datetime.utcnow()
    delta = expires_at - now
    assert timedelta(days=6, hours=23) < delta < timedelta(days=7, minutes=1)


# ---------------------------------------------------------------------------
# Service-key auth (RWS -> VoiceHR)
# ---------------------------------------------------------------------------

def test_create_invite_without_api_key_returns_401(client, db):
    response = client.post("/api/invite", json={
        "candidate_name": "Test User",
        "candidate_email": "test@example.com",
        "language": "en",
    })
    assert response.status_code == 401


def test_create_invite_wrong_api_key_returns_401(client, db):
    response = client.post(
        "/api/invite",
        json={"candidate_name": "Test User", "candidate_email": "test@example.com", "language": "en"},
        headers={"x-api-key": "wrong-key"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# external_reference_id persistence
# ---------------------------------------------------------------------------

def test_create_invite_persists_external_reference_id(client, db):
    response = client.post(
        "/api/invite",
        json={
            "candidate_name": "Test User",
            "candidate_email": "test@example.com",
            "language": "en",
            "external_reference_id": "app-123",
        },
        headers=_AUTH,
    )
    assert response.status_code == 200
    assert response.json()["external_reference_id"] == "app-123"
    assert response.json()["interview_link"] == response.json()["link"]

    row = db.execute("SELECT * FROM interviews WHERE candidate_email = ?", ("test@example.com",)).fetchone()
    assert row["external_reference_id"] == "app-123"


def test_create_invite_without_external_reference_id_still_works(client, db):
    """Non-RWS callers (e.g. the admin UI) never send this field."""
    response = client.post(
        "/api/invite",
        json={"candidate_name": "Manual User", "candidate_email": "manual@example.com", "language": "en"},
        headers=_AUTH,
    )
    assert response.status_code == 200
    assert response.json()["external_reference_id"] is None


# ---------------------------------------------------------------------------
# Idempotency-Key handling: duplicate invitation vs. re-invitation
# ---------------------------------------------------------------------------

def test_duplicate_invitation_same_idempotency_key_returns_same_interview(client, db):
    payload = {
        "candidate_name": "Test User",
        "candidate_email": "test@example.com",
        "language": "en",
        "external_reference_id": "app-1",
    }
    headers = {**_AUTH, "Idempotency-Key": "app-1:1"}

    first = client.post("/api/invite", json=payload, headers=headers)
    second = client.post("/api/invite", json=payload, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["link"] == second.json()["link"]

    rows = db.execute(
        "SELECT * FROM interviews WHERE idempotency_key = ?", ("app-1:1",)
    ).fetchall()
    assert len(rows) == 1  # no duplicate row was created


def test_reinvitation_with_incremented_attempt_number_creates_new_interview(client, db):
    payload = {
        "candidate_name": "Test User",
        "candidate_email": "test@example.com",
        "language": "en",
        "external_reference_id": "app-1",
    }

    first = client.post(
        "/api/invite", json=payload, headers={**_AUTH, "Idempotency-Key": "app-1:1"}
    )
    second = client.post(
        "/api/invite", json=payload, headers={**_AUTH, "Idempotency-Key": "app-1:2"}
    )

    assert first.json()["link"] != second.json()["link"]

    rows = db.execute(
        "SELECT * FROM interviews WHERE external_reference_id = ?", ("app-1",)
    ).fetchall()
    assert len(rows) == 2


def test_invite_without_idempotency_key_always_creates_new_interview(client, db):
    """Backward compatible: callers that never send the header (e.g. the admin UI) keep
    creating a new interview every call, exactly as before this feature existed."""
    payload = {"candidate_name": "Test User", "candidate_email": "test@example.com", "language": "en"}
    first = client.post("/api/invite", json=payload, headers=_AUTH)
    second = client.post("/api/invite", json=payload, headers=_AUTH)
    assert first.json()["link"] != second.json()["link"]


# ---------------------------------------------------------------------------
# Automatic invitation email delivery
# ---------------------------------------------------------------------------

def test_create_invite_sends_email_with_candidate_name_and_link(client, db, monkeypatch):
    import backend.api.routes.interview as interview_module

    sent = {}

    async def _capture(to, subject, body):
        sent["to"] = to
        sent["subject"] = subject
        sent["body"] = body

    monkeypatch.setattr(interview_module, "_send_email", _capture)

    response = client.post("/api/invite", json={
        "candidate_name": "Alice Martin",
        "candidate_email": "alice@example.com",
        "language": "en",
    }, headers=_AUTH)

    assert response.status_code == 200
    assert sent["to"] == "alice@example.com"
    assert "Alice Martin" in sent["body"]
    token = response.json()["link"].split("token=")[1]
    assert token in sent["body"]

    row = db.execute("SELECT * FROM interviews WHERE candidate_email = ?", ("alice@example.com",)).fetchone()
    assert row["invite_email_sent_at"] is not None
    assert row["invite_email_error"] is None


def test_create_invite_email_includes_job_title(client, db, monkeypatch):
    import backend.api.routes.interview as interview_module

    sent = {}

    async def _capture(to, subject, body):
        sent["body"] = body

    monkeypatch.setattr(interview_module, "_send_email", _capture)

    client.post("/api/invite", json={
        "candidate_name": "Alice Martin",
        "candidate_email": "alice@example.com",
        "language": "en",
        "job_title": "Backend Engineer",
    }, headers=_AUTH)

    assert "Backend Engineer" in sent["body"]


def test_create_invite_email_omits_job_title_when_not_provided(client, db, monkeypatch):
    import backend.api.routes.interview as interview_module

    sent = {}

    async def _capture(to, subject, body):
        sent["body"] = body

    monkeypatch.setattr(interview_module, "_send_email", _capture)

    client.post("/api/invite", json={
        "candidate_name": "Alice Martin",
        "candidate_email": "alice@example.com",
        "language": "en",
    }, headers=_AUTH)

    # Falls back to the original generic wording -- no "None"/"null" ever appears.
    assert "None" not in sent["body"]
    assert "null" not in sent["body"]


def test_create_invite_email_language_matches_interview_language(client, db, monkeypatch):
    import backend.api.routes.interview as interview_module

    sent = {}

    async def _capture(to, subject, body):
        sent["body"] = body

    monkeypatch.setattr(interview_module, "_send_email", _capture)

    client.post("/api/invite", json={
        "candidate_name": "Jean Dupont",
        "candidate_email": "jean@example.com",
        "language": "fr",
        "job_title": "Ingénieur Backend",
    }, headers=_AUTH)

    assert "évaluation de langue française" in sent["body"]
    assert "Ingénieur Backend" in sent["body"]


def test_create_invite_email_never_contains_internal_ids_or_secrets(client, db, monkeypatch):
    import backend.api.routes.interview as interview_module

    sent = {}

    async def _capture(to, subject, body):
        sent["body"] = body
        sent["subject"] = subject

    monkeypatch.setattr(interview_module, "_send_email", _capture)

    response = client.post("/api/invite", json={
        "candidate_name": "Alice Martin",
        "candidate_email": "alice@example.com",
        "language": "en",
        "external_reference_id": "app-12345-should-not-leak",
        "job_title": "Backend Engineer",
    }, headers=_AUTH)

    full_text = sent["subject"] + sent["body"]
    assert "app-12345-should-not-leak" not in full_text
    assert TEST_RWS_SERVICE_KEY not in full_text
    # Never a CEFR level -- VoiceHR is never even given the job's required_cefr in
    # the first place (RWS only ever sends `language`, see language_gate.py), so this
    # also pins that no future change starts passing it through.
    assert "CEFR" not in full_text
    assert response.status_code == 200


def test_create_invite_email_provider_failure_returns_502(client, db, monkeypatch):
    import backend.api.routes.interview as interview_module

    async def _fail(to, subject, body):
        raise HTTPException(status_code=502, detail="Email send failed: provider rejected request")

    monkeypatch.setattr(interview_module, "_send_email", _fail)

    response = client.post("/api/invite", json={
        "candidate_name": "Alice Martin",
        "candidate_email": "alice@example.com",
        "language": "en",
        "external_reference_id": "app-1",
    }, headers=_AUTH)

    assert response.status_code == 502

    row = db.execute("SELECT * FROM interviews WHERE candidate_email = ?", ("alice@example.com",)).fetchone()
    assert row is not None  # the interview itself was still created
    assert row["invite_email_sent_at"] is None
    assert "provider rejected request" in row["invite_email_error"]


def test_create_invite_not_configured_returns_500_and_does_not_mark_sent(client, db, monkeypatch):
    import backend.api.routes.interview as interview_module

    async def _not_configured(to, subject, body):
        raise HTTPException(status_code=500, detail="Email delivery not configured. Set RESEND_API_KEY in .env.")

    monkeypatch.setattr(interview_module, "_send_email", _not_configured)

    response = client.post("/api/invite", json={
        "candidate_name": "Alice Martin",
        "candidate_email": "alice@example.com",
        "language": "en",
    }, headers=_AUTH)

    assert response.status_code == 500
    row = db.execute("SELECT * FROM interviews WHERE candidate_email = ?", ("alice@example.com",)).fetchone()
    assert row["invite_email_sent_at"] is None


def test_retry_after_delivery_failure_reuses_interview_and_retries_delivery(client, db, monkeypatch):
    """The core failure-recovery contract: a retried call with the same
    Idempotency-Key must not create a second interview, and must actually attempt
    delivery again (not silently treat the existing row as already-delivered)."""
    import backend.api.routes.interview as interview_module

    call_count = {"n": 0}

    async def _fail_once(to, subject, body):
        call_count["n"] += 1
        raise HTTPException(status_code=502, detail="Email send failed: transient provider error")

    monkeypatch.setattr(interview_module, "_send_email", _fail_once)

    payload = {
        "candidate_name": "Alice Martin",
        "candidate_email": "alice@example.com",
        "language": "en",
        "external_reference_id": "app-1",
    }
    headers = {**_AUTH, "Idempotency-Key": "app-1:1"}

    first = client.post("/api/invite", json=payload, headers=headers)
    assert first.status_code == 502
    assert call_count["n"] == 1

    async def _succeed(to, subject, body):
        call_count["n"] += 1

    monkeypatch.setattr(interview_module, "_send_email", _succeed)

    second = client.post("/api/invite", json=payload, headers=headers)
    assert second.status_code == 200
    assert call_count["n"] == 2  # delivery was actually retried, not skipped

    rows = db.execute(
        "SELECT * FROM interviews WHERE idempotency_key = ?", ("app-1:1",)
    ).fetchall()
    assert len(rows) == 1  # still exactly one interview -- no duplicate was created
    assert rows[0]["invite_email_sent_at"] is not None
    assert rows[0]["invite_email_error"] is None  # cleared on the eventual success


def test_duplicate_call_after_successful_delivery_does_not_resend(client, db, monkeypatch):
    import backend.api.routes.interview as interview_module

    call_count = {"n": 0}

    async def _count_and_succeed(to, subject, body):
        call_count["n"] += 1

    monkeypatch.setattr(interview_module, "_send_email", _count_and_succeed)

    payload = {
        "candidate_name": "Alice Martin",
        "candidate_email": "alice@example.com",
        "language": "en",
        "external_reference_id": "app-1",
    }
    headers = {**_AUTH, "Idempotency-Key": "app-1:1"}

    first = client.post("/api/invite", json=payload, headers=headers)
    second = client.post("/api/invite", json=payload, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["link"] == second.json()["link"]
    assert call_count["n"] == 1  # the email was sent exactly once, not twice


def test_reinvitation_new_attempt_number_sends_a_new_email(client, db, monkeypatch):
    """A genuine re-invitation (new attempt number, new Idempotency-Key) is a new
    interview and is expected to send again -- this is the recruiter-initiated
    'Resend Invite' path, distinct from an automatic same-key retry."""
    import backend.api.routes.interview as interview_module

    call_count = {"n": 0}

    async def _count_and_succeed(to, subject, body):
        call_count["n"] += 1

    monkeypatch.setattr(interview_module, "_send_email", _count_and_succeed)

    payload = {
        "candidate_name": "Alice Martin",
        "candidate_email": "alice@example.com",
        "language": "en",
        "external_reference_id": "app-1",
    }
    client.post("/api/invite", json=payload, headers={**_AUTH, "Idempotency-Key": "app-1:1"})
    client.post("/api/invite", json=payload, headers={**_AUTH, "Idempotency-Key": "app-1:2"})

    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# Explicit resend route (POST /interview/{token}/invite)
# ---------------------------------------------------------------------------

def test_explicit_resend_route_sends_again_even_if_already_delivered(client, db, monkeypatch):
    import backend.api.routes.interview as interview_module

    call_count = {"n": 0}

    async def _count_and_succeed(to, subject, body):
        call_count["n"] += 1

    monkeypatch.setattr(interview_module, "_send_email", _count_and_succeed)

    create_response = client.post("/api/invite", json={
        "candidate_name": "Alice Martin",
        "candidate_email": "alice@example.com",
        "language": "en",
    }, headers=_AUTH)
    token = create_response.json()["link"].split("token=")[1]
    assert call_count["n"] == 1

    resend_response = client.post(f"/api/interview/{token}/invite")

    assert resend_response.status_code == 200
    assert resend_response.json()["status"] == "sent"
    assert call_count["n"] == 2  # an explicit resend always sends again


def test_explicit_resend_route_unknown_token_returns_404(client, db):
    response = client.post("/api/interview/does-not-exist/invite")
    assert response.status_code == 404


def test_explicit_resend_route_provider_failure_returns_502(client, db, monkeypatch):
    # mock_email_delivery (autouse) makes this initial create succeed normally.
    create_response = client.post("/api/invite", json={
        "candidate_name": "Alice Martin",
        "candidate_email": "alice@example.com",
        "language": "en",
    }, headers=_AUTH)
    assert create_response.status_code == 200
    token = create_response.json()["link"].split("token=")[1]

    import backend.api.routes.interview as interview_module

    async def _fail(to, subject, body):
        raise HTTPException(status_code=502, detail="Email send failed: provider rejected request")

    monkeypatch.setattr(interview_module, "_send_email", _fail)

    resend_response = client.post(f"/api/interview/{token}/invite")
    assert resend_response.status_code == 502
