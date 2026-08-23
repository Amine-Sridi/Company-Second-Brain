"""
Integration test configuration for Resume Intelligence.

Works in two environments:
  - Windows host (pytest from project root): adds service dir + project root to sys.path,
    uses localhost DATABASE_URL.
  - Inside the Docker container (copied as /app/conftest.py): /app is already the service
    dir AND shared/ is already there; DATABASE_URL is already set in container env.
"""
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# ── Path setup (host-side only) ───────────────────────────────────────────────
# Detect whether we're inside the container by checking for /app/shared/__init__.py
_in_container = Path("/app/shared/__init__.py").exists()

if not _in_container:
    ROOT = Path(__file__).resolve().parents[2]
    SERVICE_DIR = ROOT / "services" / "resume-intelligence"
    for p in (str(SERVICE_DIR), str(ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)

# ── Database URL ──────────────────────────────────────────────────────────────
# Container: DATABASE_URL is already set (host=postgres via Docker network).
# Host: override with localhost so psycopg reaches the exposed port.
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://resume_intel_role:changeme@localhost:5432/"
        "company_second_brain?options=-csearch_path%3Dresume_intel"
    )

_TEST_DB_URL = os.environ["DATABASE_URL"]
os.environ.setdefault("SERVICE_API_KEY", "test-service-key")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_engine():
    return create_engine(_TEST_DB_URL, pool_pre_ping=True)


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


_TEST_SERVICE_KEY = "test-service-key"


@pytest.fixture
def client(db_session):
    from fastapi.testclient import TestClient
    from api import app, settings
    from db import get_db

    # Patch the live settings object so require_service_key checks against our
    # test key regardless of what's in the container's .env.
    original_key = settings.service_api_key
    settings.service_api_key = _TEST_SERVICE_KEY

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()
    settings.service_api_key = original_key
