"""
Recruiter authentication: bcrypt password hashing + short-lived JWT issuance/verification.

Deliberately a separate mechanism from x-api-key service-to-service auth (see main.py's
require_service_key): a JWT is read from the Authorization header and verified against
JWT_SECRET, while a service key is read from X-Api-Key and compared against
SERVICE_API_KEY. Neither can satisfy the other's check.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

_ALGORITHM = "HS256"


class InvalidTokenError(Exception):
    """Raised when a JWT is malformed, has an invalid signature, or has expired."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(
    recruiter_id: uuid.UUID, email: str, secret: str, expiry_minutes: int
) -> tuple[str, datetime]:
    """Returns (token, expires_at). expires_at is timezone-aware UTC."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=expiry_minutes)
    payload = {
        "sub": str(recruiter_id),
        "email": email,
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, secret, algorithm=_ALGORITHM)
    return token, expires_at


def decode_access_token(token: str, secret: str) -> dict:
    """Returns the decoded payload. Raises InvalidTokenError on bad signature, malformed
    token, or expiry (PyJWT checks `exp` automatically during decode)."""
    try:
        return jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
