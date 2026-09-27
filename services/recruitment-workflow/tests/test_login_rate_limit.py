"""
tests/test_login_rate_limit.py
-------------------------------
`POST /v1/auth/login` is the only unauthenticated, credential-accepting endpoint in this
service, and so the only one worth brute-forcing. bcrypt also makes each guess expensive
for the *server*, so an unlimited login is a cheap way to saturate this process as well as
a way to grind passwords.

Two buckets are under test, and the second is the one that is easy to omit: an attacker
distributing attempts across many source addresses gets a fresh IP quota each time, so
the per-account bucket is what actually protects a specific recruiter.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main

URL = "/v1/auth/login"
GOOD = {"email": "recruiter@example.com", "password": "correct-horse"}
BAD = {"email": "recruiter@example.com", "password": "wrong"}


@pytest.fixture
def client(monkeypatch):
    # Each test starts from a clean slate -- these limiters are module-level singletons.
    main._login_ip_limiter.reset()
    main._login_account_limiter.reset()

    recruiter = SimpleNamespace(
        id=uuid.uuid4(), email=GOOD["email"], name="Test", password_hash="hash"
    )
    monkeypatch.setattr(
        main, "get_recruiter_by_email",
        lambda db, email: recruiter if email == GOOD["email"] else None,
    )
    monkeypatch.setattr(
        main, "verify_password", lambda plain, hashed: plain == GOOD["password"]
    )

    def fake_get_db():
        yield SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    main.app.dependency_overrides[main.get_db] = fake_get_db
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()
    main._login_ip_limiter.reset()
    main._login_account_limiter.reset()


def test_a_correct_password_still_works(client):
    resp = client.post(URL, json=GOOD)
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


def test_wrong_password_is_401_until_the_account_limit(client):
    for i in range(5):
        assert client.post(URL, json=BAD).status_code == 401, f"attempt {i + 1}"


def test_brute_force_is_blocked_with_429_and_retry_after(client):
    for _ in range(5):
        client.post(URL, json=BAD)
    resp = client.post(URL, json=BAD)
    assert resp.status_code == 429, resp.text
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) > 0


def test_the_block_does_not_reveal_whether_the_account_exists(client):
    """Both buckets must produce the same message: a distinct 'account locked' reply
    would confirm the account exists, which an enumeration attack wants."""
    for _ in range(5):
        client.post(URL, json=BAD)
    blocked_known = client.post(URL, json=BAD)

    main._login_ip_limiter.reset()
    main._login_account_limiter.reset()
    unknown = {"email": "nobody@example.com", "password": "x"}
    for _ in range(5):
        client.post(URL, json=unknown)
    blocked_unknown = client.post(URL, json=unknown)

    assert blocked_known.status_code == blocked_unknown.status_code == 429
    assert blocked_known.json()["detail"] == blocked_unknown.json()["detail"]


def test_a_distributed_attack_on_one_account_is_still_blocked(client):
    """Each forged X-Forwarded-For gets its own IP bucket, so only the per-account bucket
    can stop this. Without it, an attacker with many source addresses is unlimited."""
    for i in range(5):
        r = client.post(URL, json=BAD, headers={"X-Forwarded-For": f"198.51.100.{i}"})
        assert r.status_code == 401, f"attempt {i + 1} should reach the password check"

    resp = client.post(URL, json=BAD, headers={"X-Forwarded-For": "198.51.100.99"})
    assert resp.status_code == 429, "a fresh IP must not grant a fresh account quota"


def test_a_successful_login_clears_the_account_bucket(client):
    """A recruiter who mistypes twice then succeeds must not stay penalised."""
    for _ in range(4):
        assert client.post(URL, json=BAD).status_code == 401
    assert client.post(URL, json=GOOD).status_code == 200
    # The account bucket is clear again, so failures are once more answered on merit.
    assert client.post(URL, json=BAD).status_code == 401


def test_a_successful_login_does_not_clear_the_ip_bucket(client):
    """One valid credential must not erase the evidence of failed guesses from the same
    host -- otherwise an attacker who happens to own one account gets unlimited attempts
    against the others."""
    for _ in range(4):
        client.post(URL, json=BAD)
    assert client.post(URL, json=GOOD).status_code == 200

    # 5 attempts have been spent against the IP bucket (limit 10); the account bucket was
    # cleared. Spend the rest and confirm the IP bucket still trips.
    codes = [client.post(URL, json={"email": f"u{i}@example.com", "password": "x"}).status_code
             for i in range(10)]
    assert 429 in codes, "the IP bucket must survive a successful login"
