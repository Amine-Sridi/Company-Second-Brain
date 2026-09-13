"""
Shared safety guard for this repo-root integration-test suite's real-Postgres fixtures.

Some of these conftest files (recruitment-workflow, second-brain) include a
session-scoped, autouse fixture that TRUNCATEs real tables before their suite runs --
necessary so repeated runs start from a clean slate, but genuinely destructive if ever
pointed at the same Postgres database used for local development or a demo.

This module is the one place that decides whether a resolved database URL is safe to
run those fixtures against, so every destructive fixture calls the same check instead
of several slightly-different copies of it drifting apart over time.

Real incident this exists because of: an earlier session ran
test_recruitment_workflow.py directly inside the live csb-recruitment-workflow
container. Its conftest had no separate test-database concept at all -- it read
DATABASE_URL, which (inside that container) is the same Postgres database recruiter
demos use, and its autouse TRUNCATE fixture wiped every job/application/match_result
row with no confirmation and no way to undo it. See handoff.md's "Known limitations"
for the full account. This module is the fix: destructive fixtures now call
resolve_test_database_url() instead of reading DATABASE_URL, and that function fails
closed (raises, never falls back) unless the resolved database is unmistakably a
disposable test database.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

# Database names known to be real dev/demo data, never a legitimate destructive-test
# target, regardless of what environment variable pointed at them. This is defense in
# depth, not the primary guard -- the primary guard is requiring TEST_DATABASE_URL to
# name a database that unmistakably marks itself as disposable (see
# _REQUIRED_TEST_MARKER below). This list exists so that even a TEST_DATABASE_URL
# mistakenly copy-pasted from a service's own .env is still caught.
_KNOWN_DEV_DEMO_DATABASE_NAMES = frozenset({"company_second_brain"})

# The database name resolved from TEST_DATABASE_URL must contain this marker
# (case-insensitive) -- "an unmistakable test database name such as *_test", per this
# project's own safety requirement (see handoff.md). A `_test` suffix, `test_` prefix,
# or a name that is just "test" all satisfy this; the point is that nobody could
# mistake it for the real demo database at a glance.
_REQUIRED_TEST_MARKER = "test"


class UnsafeTestDatabaseError(RuntimeError):
    """
    Raised -- and meant to propagate uncaught, failing the whole test session -- when
    a destructive integration fixture would otherwise run against something that
    isn't unmistakably a disposable test database. Every raise path in this module is
    a refusal to proceed; none of them is a silent fallback to another URL.
    """


def _database_name(url: str) -> str:
    """Extracts the database name from a SQLAlchemy-style URL, e.g.
    "postgresql+psycopg://user:pass@host:5432/dbname?options=..." -> "dbname".
    urlsplit handles the "postgresql+psycopg" scheme fine -- it's just another
    scheme string to it, the "+driver" suffix isn't special-cased anywhere here."""
    path = urlsplit(url).path
    return path.lstrip("/").split("?")[0]


def resolve_test_database_url() -> str:
    """
    The one function every destructive integration fixture (a session-scoped autouse
    TRUNCATE) must call instead of reading DATABASE_URL directly.

    Fails closed: raises UnsafeTestDatabaseError rather than falling back to
    DATABASE_URL, localhost defaults, or any other "helpful" default -- a helpful
    default is exactly how the real incident this guards against happened (see this
    module's own docstring). There is no environment in which this function silently
    returns something it hasn't validated.

    Deliberately does NOT set a default value for TEST_DATABASE_URL anywhere (not
    here, not in conftest, not in any .env.example) -- an unset variable must always
    surface as this exact, actionable error, never as a quiet substitution.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise UnsafeTestDatabaseError(
            "TEST_DATABASE_URL is not set. The destructive integration-test "
            "fixtures in this suite (session-scoped TRUNCATE) refuse to fall back "
            "to DATABASE_URL or any other default -- that exact fallback is what "
            "wiped the live demo database once already (see handoff.md's Known "
            "Limitations). Set TEST_DATABASE_URL to a dedicated, disposable "
            "Postgres database whose name unmistakably marks it as a test "
            "database, e.g.:\n"
            "  postgresql+psycopg://recruitment_role:changeme@postgres:5432/"
            "company_second_brain_test?options=-csearch_path%3Drecruitment"
        )

    db_name = _database_name(url)

    if db_name.lower() in _KNOWN_DEV_DEMO_DATABASE_NAMES:
        raise UnsafeTestDatabaseError(
            f"TEST_DATABASE_URL points at {db_name!r}, which is this project's "
            "known dev/demo database name -- refusing to run destructive "
            "integration fixtures against it under any circumstances. Point "
            "TEST_DATABASE_URL at a separate, disposable database instead "
            f"(e.g. {db_name}_test)."
        )

    if _REQUIRED_TEST_MARKER not in db_name.lower():
        raise UnsafeTestDatabaseError(
            f"TEST_DATABASE_URL's database name ({db_name!r}) does not contain "
            f"{_REQUIRED_TEST_MARKER!r} -- refusing to run destructive integration "
            "fixtures against a database whose name doesn't unmistakably mark it "
            f"as disposable. Rename it (or point at a copy) such as {db_name}_test."
        )

    return url
