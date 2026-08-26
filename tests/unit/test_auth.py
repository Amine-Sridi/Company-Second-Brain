"""
Unit tests for RWS recruiter authentication (bcrypt hashing + JWT issuance/verification).

Pure functions -- no DB, no FastAPI. Run on the host with plain pytest.
"""
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest

_SERVICE_DIR = Path(__file__).resolve().parents[2] / "services" / "recruitment-workflow"
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))

from auth import (  # noqa: E402
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

_SECRET = "test-jwt-secret"


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def test_hash_password_does_not_return_plaintext():
    hashed = hash_password("correct-horse-battery-staple")
    assert hashed != "correct-horse-battery-staple"
    assert hashed.startswith("$2b$") or hashed.startswith("$2a$")


def test_verify_password_correct():
    hashed = hash_password("correct-horse-battery-staple")
    assert verify_password("correct-horse-battery-staple", hashed) is True


def test_verify_password_incorrect():
    hashed = hash_password("correct-horse-battery-staple")
    assert verify_password("wrong-password", hashed) is False


def test_hash_password_is_salted_differently_each_time():
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b
    assert verify_password("same-password", a) is True
    assert verify_password("same-password", b) is True


# ---------------------------------------------------------------------------
# JWT issuance / verification
# ---------------------------------------------------------------------------

def test_create_and_decode_access_token_roundtrip():
    recruiter_id = uuid.uuid4()
    token, expires_at = create_access_token(recruiter_id, "r@example.com", _SECRET, 45)
    payload = decode_access_token(token, _SECRET)
    assert payload["sub"] == str(recruiter_id)
    assert payload["email"] == "r@example.com"
    assert expires_at > datetime.now(timezone.utc)


def test_token_has_expiry_claim():
    token, _ = create_access_token(uuid.uuid4(), "r@example.com", _SECRET, 45)
    payload = decode_access_token(token, _SECRET)
    assert "exp" in payload
    assert "iat" in payload
    assert payload["exp"] > payload["iat"]


def test_expired_token_raises_invalid_token_error():
    recruiter_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    expired_payload = {
        "sub": str(recruiter_id),
        "email": "r@example.com",
        "iat": now - timedelta(minutes=10),
        "exp": now - timedelta(minutes=1),  # already expired
    }
    expired_token = jwt.encode(expired_payload, _SECRET, algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        decode_access_token(expired_token, _SECRET)


def test_token_signed_with_wrong_secret_is_rejected():
    token, _ = create_access_token(uuid.uuid4(), "r@example.com", _SECRET, 45)
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, "a-completely-different-secret")


def test_malformed_token_is_rejected():
    with pytest.raises(InvalidTokenError):
        decode_access_token("not-a-real-jwt", _SECRET)


def test_empty_token_is_rejected():
    with pytest.raises(InvalidTokenError):
        decode_access_token("", _SECRET)
