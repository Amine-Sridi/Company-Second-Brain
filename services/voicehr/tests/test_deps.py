import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from backend.api.deps import validate_interview_token
from tests.conftest import create_interview


def test_validate_token_success(client, db):
    token = create_interview(db, token="valid-token", name="John Doe", language="en")
    result = validate_interview_token(token, db)
    assert result["token"] == token
    assert result["candidate_name"] == "John Doe"
    assert result["language"] == "en"


def test_validate_token_invalid(client, db):
    with pytest.raises(HTTPException) as exc_info:
        validate_interview_token("nonexistent-token", db)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Invalid token"


def test_validate_token_expired(client, db):
    expired = (datetime.utcnow() - timedelta(days=1)).isoformat()
    token = create_interview(db, token="expired-token", expires_at=expired)
    with pytest.raises(HTTPException) as exc_info:
        validate_interview_token(token, db)
    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == "Link expired"


def test_validate_token_completed(client, db):
    token = create_interview(db, token="completed-token", status="completed")
    with pytest.raises(HTTPException) as exc_info:
        validate_interview_token(token, db)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Interview already completed or active"


def test_validate_token_active(client, db):
    token = create_interview(db, token="active-token", status="active")
    with pytest.raises(HTTPException) as exc_info:
        validate_interview_token(token, db)
    assert exc_info.value.status_code == 409


def test_validate_token_pending_succeeds(client, db):
    token = create_interview(db, token="pending-token", status="pending")
    result = validate_interview_token(token, db)
    assert result["status"] == "pending"
