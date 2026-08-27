"""
Integration test configuration for Second Brain.

Copy this file as /app/conftest.py inside the csb-second-brain container, then
run tests with: python -m pytest /app/test_second_brain.py -v
"""
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# ── Path setup (host-side only) ───────────────────────────────────────────────
_in_container = Path("/app/shared/__init__.py").exists()

if not _in_container:
    ROOT = Path(__file__).resolve().parents[2]
    SERVICE_DIR = ROOT / "services" / "second-brain"
    for p in (str(SERVICE_DIR), str(ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)

# ── Database URL ──────────────────────────────────────────────────────────────
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://second_brain_role:changeme@localhost:5432/"
        "company_second_brain?options=-csearch_path%3Dsecond_brain"
    )

_TEST_DB_URL = os.environ["DATABASE_URL"]

_TEST_SERVICE_KEY = "test-service-key"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_engine():
    return create_engine(_TEST_DB_URL, pool_pre_ping=True)


@pytest.fixture(scope="session", autouse=True)
def truncate_candidate_knowledge(test_engine):
    """Wipe any rows committed by previous smoke tests before the suite runs."""
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
