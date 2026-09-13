"""
Tests for tests/integration/db_safety.py -- the guard that stops the destructive
integration-test fixtures (recruitment-workflow, second-brain) from ever running
against a real dev/demo Postgres database again.

Pure unit tests: resolve_test_database_url() only ever inspects os.environ and
parses a URL string, so none of this needs a real Postgres connection, and none of
it is itself destructive -- these tests only prove the guard raises (or doesn't)
under the right conditions, never actually TRUNCATE anything.
"""
from __future__ import annotations

import pytest

from tests.integration.db_safety import UnsafeTestDatabaseError, resolve_test_database_url

_RECRUITMENT_TEST_URL = (
    "postgresql+psycopg://recruitment_role:changeme@localhost:5432/"
    "company_second_brain_test?options=-csearch_path%3Drecruitment"
)
_REAL_DEMO_URL = (
    "postgresql+psycopg://recruitment_role:changeme@postgres:5432/"
    "company_second_brain?options=-csearch_path%3Drecruitment"
)


def test_missing_test_database_url_fails_closed(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    with pytest.raises(UnsafeTestDatabaseError, match="TEST_DATABASE_URL is not set"):
        resolve_test_database_url()


def test_empty_test_database_url_fails_closed(monkeypatch):
    """An explicitly-set-but-empty value must be treated the same as unset -- this
    is what stops a CI/shell config that does `export TEST_DATABASE_URL=` from
    silently satisfying the "is it set" check without actually providing a value."""
    monkeypatch.setenv("TEST_DATABASE_URL", "")
    with pytest.raises(UnsafeTestDatabaseError, match="TEST_DATABASE_URL is not set"):
        resolve_test_database_url()


def test_real_demo_database_name_is_rejected_even_if_pointed_at_by_test_var(monkeypatch):
    """The denylist catches a TEST_DATABASE_URL mistakenly copy-pasted from a
    service's own real .env -- setting the right variable name is not enough on
    its own if it points at the known real database."""
    monkeypatch.setenv("TEST_DATABASE_URL", _REAL_DEMO_URL)
    with pytest.raises(UnsafeTestDatabaseError, match="known dev/demo database name"):
        resolve_test_database_url()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://role:pw@localhost:5432/company_second_brain",
        "postgresql+psycopg://role:pw@postgres:5432/company_second_brain?options=-csearch_path%3Drecruitment",
        "postgresql+psycopg://role:pw@localhost:5432/COMPANY_SECOND_BRAIN",  # case-insensitive
    ],
)
def test_any_url_naming_the_real_database_is_rejected(monkeypatch, url):
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    with pytest.raises(UnsafeTestDatabaseError):
        resolve_test_database_url()


def test_database_name_without_test_marker_is_rejected(monkeypatch):
    """Even a database that ISN'T the known real one is rejected if its name
    doesn't unmistakably mark it as disposable -- e.g. a typo'd or unrelated
    database name should never be assumed safe by default."""
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://recruitment_role:changeme@localhost:5432/some_other_db",
    )
    with pytest.raises(UnsafeTestDatabaseError, match="does not contain"):
        resolve_test_database_url()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://role:pw@localhost:5432/company_second_brain_test",
        "postgresql+psycopg://role:pw@localhost:5432/COMPANY_SECOND_BRAIN_TEST",  # case-insensitive
        "postgresql+psycopg://role:pw@localhost:5432/test_company_second_brain",
        "postgresql+psycopg://role:pw@localhost:5432/test",
    ],
)
def test_unmistakable_test_database_names_are_accepted(monkeypatch, url):
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    assert resolve_test_database_url() == url


def test_accepted_url_is_returned_unmodified(monkeypatch):
    """resolve_test_database_url() must hand back exactly the validated URL --
    callers (test_engine fixtures) rely on it being usable as-is, including its
    query string (search_path)."""
    monkeypatch.setenv("TEST_DATABASE_URL", _RECRUITMENT_TEST_URL)
    assert resolve_test_database_url() == _RECRUITMENT_TEST_URL


def test_query_string_is_not_mistaken_for_part_of_the_database_name(monkeypatch):
    """A database literally named "test" with an unrelated query string must still
    resolve to the database name "test", not "test?options=...". Guards against a
    naive implementation that forgets to strip the query string first."""
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://role:pw@localhost:5432/test?options=-csearch_path%3Drecruitment",
    )
    # Would also pass if the marker check were sloppy, so assert directly on the
    # real database-name parser instead of only on the public function's success.
    from tests.integration.db_safety import _database_name

    assert _database_name(
        "postgresql+psycopg://role:pw@localhost:5432/test?options=-csearch_path%3Drecruitment"
    ) == "test"
