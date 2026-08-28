"""
Tests for the HTTP Basic auth gate on the admin/report-management surface
(backend/api/deps.py::require_admin_auth) -- protects candidate PII, transcripts,
recordings, and scores without a user database, session system, or JWT.
"""
import logging

import pytest

from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME, create_interview, create_report

_VALID_AUTH = (TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD)
_WRONG_PASSWORD_AUTH = (TEST_ADMIN_USERNAME, "not-the-right-password")
_WRONG_USERNAME_AUTH = ("not-the-right-username", TEST_ADMIN_PASSWORD)

# (method, path) for every route the admin/report-management surface protects.
_PROTECTED_GET_ROUTES = [
    "/admin",
    "/admin/reports",
    "/api/admin/reports",
]


@pytest.fixture
def seeded_report(db):
    """One completed interview + report, for routes that need something to list/retry."""
    token = create_interview(db, token="admin-auth-token", name="Admin Auth Candidate", status="completed")
    row = db.execute("SELECT id FROM interviews WHERE token = ?", (token,)).fetchone()
    report_token = create_report(db, row["id"], report_token="admin-auth-report")
    return report_token


# ---------------------------------------------------------------------------
# Unauthenticated / invalid-credential rejection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", _PROTECTED_GET_ROUTES)
def test_protected_route_without_credentials_returns_401(client, db, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


@pytest.mark.parametrize("path", _PROTECTED_GET_ROUTES)
def test_protected_route_with_wrong_password_returns_401(client, db, path):
    response = client.get(path, auth=_WRONG_PASSWORD_AUTH)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


@pytest.mark.parametrize("path", _PROTECTED_GET_ROUTES)
def test_protected_route_with_wrong_username_returns_401(client, db, path):
    response = client.get(path, auth=_WRONG_USERNAME_AUTH)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_admin_reports_retry_without_credentials_returns_401(client, db, seeded_report):
    response = client.post(f"/api/admin/reports/{seeded_report}/retry")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_admin_reports_retry_with_wrong_credentials_returns_401(client, db, seeded_report):
    response = client.post(f"/api/admin/reports/{seeded_report}/retry", auth=_WRONG_PASSWORD_AUTH)
    assert response.status_code == 401


def test_recordings_route_without_credentials_returns_401(client, db):
    response = client.get("/recordings/1.webm")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_recordings_route_with_wrong_credentials_returns_401(client, db):
    response = client.get("/recordings/1.webm", auth=_WRONG_PASSWORD_AUTH)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Valid credentials accepted
# ---------------------------------------------------------------------------

def test_admin_page_with_valid_credentials_returns_200(client, db):
    response = client.get("/admin", auth=_VALID_AUTH)
    assert response.status_code == 200


def test_admin_reports_html_with_valid_credentials_returns_200(client, db):
    response = client.get("/admin/reports", auth=_VALID_AUTH)
    assert response.status_code == 200


def test_admin_reports_api_with_valid_credentials_returns_200(client, db, seeded_report):
    response = client.get("/api/admin/reports", auth=_VALID_AUTH)
    assert response.status_code == 200
    assert response.json()["reports"][0]["report_token"] == seeded_report


def test_admin_reports_retry_with_valid_credentials_is_authorized(client, db, seeded_report):
    """A valid-credentials retry call must get past the auth gate -- it may still 404
    (no transcript recorded for this fixture) or otherwise fail downstream, but not 401."""
    response = client.post(f"/api/admin/reports/{seeded_report}/retry", auth=_VALID_AUTH)
    assert response.status_code != 401


# ---------------------------------------------------------------------------
# Public/candidate-facing routes remain unauthenticated
# ---------------------------------------------------------------------------

def test_health_remains_public(client, db):
    response = client.get("/health")
    assert response.status_code == 200


def test_health_detail_remains_public(client, db):
    response = client.get("/health/detail")
    assert response.status_code == 200


def test_interview_page_remains_public(client, db):
    response = client.get("/interview?token=any-token")
    assert response.status_code == 200


def test_report_page_remains_public(client, db):
    response = client.get("/report?token=any-token")
    assert response.status_code == 200


def test_report_by_token_api_remains_public(client, db, seeded_report):
    """The individual report-by-token link (already gated by an unguessable
    report_token, exactly like the interview invite link) is deliberately left
    unauthenticated -- it is not part of the general admin listing/management
    surface, and this task explicitly excludes changing that token model."""
    response = client.get(f"/api/report/{seeded_report}")
    assert response.status_code == 200


def test_interview_token_lookup_remains_public(client, db):
    token = create_interview(db, token="public-lookup-token", name="Public Candidate")
    response = client.get(f"/api/interview/{token}")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Credentials never appear in logs
# ---------------------------------------------------------------------------

def test_admin_credentials_never_appear_in_logs_on_success(client, db, caplog):
    with caplog.at_level(logging.DEBUG):
        response = client.get("/admin", auth=_VALID_AUTH)
    assert response.status_code == 200
    assert TEST_ADMIN_PASSWORD not in caplog.text
    assert TEST_ADMIN_USERNAME not in caplog.text


def test_admin_credentials_never_appear_in_logs_on_failure(client, db, caplog):
    with caplog.at_level(logging.DEBUG):
        response = client.get("/admin", auth=_WRONG_PASSWORD_AUTH)
    assert response.status_code == 401
    assert TEST_ADMIN_PASSWORD not in caplog.text
    assert "not-the-right-password" not in caplog.text
