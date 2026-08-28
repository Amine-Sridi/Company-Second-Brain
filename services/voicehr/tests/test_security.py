import uuid
from backend.core.security import generate_token, generate_report_token


def test_generate_token_returns_uuid():
    token = generate_token()
    assert token
    uuid.UUID(token)


def test_generate_token_unique():
    tokens = {generate_token() for _ in range(100)}
    assert len(tokens) == 100


def test_generate_token_format():
    token = generate_token()
    parts = token.split("-")
    assert len(parts) == 5
    assert len(parts[0]) == 8
    assert len(parts[1]) == 4
    assert len(parts[2]) == 4
    assert len(parts[3]) == 4
    assert len(parts[4]) == 12


def test_generate_report_token_returns_uuid():
    token = generate_report_token()
    assert token
    uuid.UUID(token)


def test_generate_report_token_unique():
    tokens = {generate_report_token() for _ in range(100)}
    assert len(tokens) == 100


def test_generate_report_token_format():
    token = generate_report_token()
    parts = token.split("-")
    assert len(parts) == 5


def test_token_and_report_token_different():
    token = generate_token()
    report_token = generate_report_token()
    assert token != report_token
