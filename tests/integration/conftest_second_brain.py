"""
Integration test configuration for Second Brain.

Copy this file AND db_safety.py as /app/conftest.py + /app/db_safety.py inside the
csb-second-brain container, then run tests with:
    docker exec -e TEST_DATABASE_URL=... csb-second-brain \
        python -m pytest /app/test_second_brain.py -v
(both files -- this suite's destructive fixture below refuses to run without
db_safety.py's guard; see its own module docstring and handoff.md.)
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# db_safety.py lives alongside this file (tests/integration/ on the host; copied
# next to conftest.py in the container -- see this module's own docstring above).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from db_safety import resolve_test_database_url  # noqa: E402


# ── Path setup (host-side only) ───────────────────────────────────────────────
_in_container = Path("/app/shared/__init__.py").exists()

if not _in_container:
    ROOT = Path(__file__).resolve().parents[2]
    SERVICE_DIR = ROOT / "services" / "second-brain"
    for p in (str(SERVICE_DIR), str(ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)

# ── Database URL ──────────────────────────────────────────────────────────────
# TEST_DATABASE_URL only -- no fallback to DATABASE_URL. See
# conftest_recruitment_workflow.py's own copy of this comment and handoff.md for why
# this project no longer allows that fallback for any destructive-fixture suite.
_TEST_DB_URL = resolve_test_database_url()

_TEST_SERVICE_KEY = "test-service-key"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_engine():
    return create_engine(_TEST_DB_URL, pool_pre_ping=True)


@pytest.fixture(scope="session", autouse=True)
def truncate_candidate_knowledge(test_engine):
    """
    Wipe any rows committed by previous smoke tests before the suite runs.

    Safe by construction: test_engine is built from _TEST_DB_URL, which is only
    ever resolve_test_database_url()'s return value -- there is no DATABASE_URL
    fallback path here to accidentally take. See db_safety.py and handoff.md.
    """
    from sqlalchemy import text
    with test_engine.connect() as conn:
        conn.execute(text("TRUNCATE second_brain.candidate_knowledge"))
        conn.execute(text("TRUNCATE second_brain.candidate_feedback"))
        conn.commit()
    yield


@pytest.fixture
def db_session(test_engine):
    """Per-test transactional session — rolls back after each test."""
    conn = test_engine.connect()
    trans = conn.begin()
    Session = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
    session = Session()
    yield session
    session.close()
    trans.rollback()
    conn.close()


class _MockLLMProvider:
    """Synchronous-compatible mock; embed_text returns 768 deterministic floats."""

    async def embed_text(self, text, *, model=None, dimensions=768):
        return [0.1] * dimensions

    async def generate_text(self, prompt, *, model=None):
        raise NotImplementedError

    async def generate_structured(self, prompt, schema, *, model=None):
        raise NotImplementedError


class _FailingLLMProvider(_MockLLMProvider):
    """Always raises LLMProviderError from embed_text."""

    async def embed_text(self, text, *, model=None, dimensions=768):
        from shared.llm import LLMProviderError
        raise LLMProviderError("Simulated embedding failure")


@pytest.fixture
def client(db_session):
    from fastapi.testclient import TestClient
    from main import app, settings, get_llm_provider
    from db import get_db

    original_key = settings.service_api_key
    settings.service_api_key = _TEST_SERVICE_KEY

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_llm_provider] = lambda: _MockLLMProvider()

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()
    settings.service_api_key = original_key


@pytest.fixture
def client_failing_llm(db_session):
    """Client wired with a provider that always fails on embed_text."""
    from fastapi.testclient import TestClient
    from main import app, settings, get_llm_provider
    from db import get_db

    original_key = settings.service_api_key
    settings.service_api_key = _TEST_SERVICE_KEY

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_llm_provider] = lambda: _FailingLLMProvider()

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()
    settings.service_api_key = original_key
