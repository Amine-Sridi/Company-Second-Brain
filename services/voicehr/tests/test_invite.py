from datetime import datetime, timedelta
from tests.conftest import TEST_RWS_SERVICE_KEY, create_interview

_AUTH = {"x-api-key": TEST_RWS_SERVICE_KEY}


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
