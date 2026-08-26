"""
Integration test configuration for Recruitment Workflow Service.

Copy this file as /app/conftest.py inside the csb-recruitment-workflow container, then
run tests with: python -m pytest /app/test_recruitment_workflow.py -v
"""
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


# ── Path setup (host-side only) ───────────────────────────────────────────────
_in_container = Path("/app/shared/__init__.py").exists()

if not _in_container:
    ROOT = Path(__file__).resolve().parents[2]
    SERVICE_DIR = ROOT / "services" / "recruitment-workflow"
    for p in (str(SERVICE_DIR), str(ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)

# ── Database URL ──────────────────────────────────────────────────────────────
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://recruitment_role:changeme@localhost:5432/"
        "company_second_brain?options=-csearch_path%3Drecruitment"
    )

_TEST_DB_URL = os.environ["DATABASE_URL"]

_TEST_SERVICE_KEY = "test-service-key"

# Must match SEED_RECRUITER_EMAIL / SEED_RECRUITER_PASSWORD used when the 0003 migration
# was run against this database -- see services/recruitment-workflow/.env.
SEED_RECRUITER_EMAIL = "recruiter@example.com"
SEED_RECRUITER_PASSWORD = "ChangeMe123!"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_engine():
    return create_engine(_TEST_DB_URL, pool_pre_ping=True)


@pytest.fixture(scope="session", autouse=True)
def truncate_recruitment_tables(test_engine):
    """
    Wipe applications/matches/jobs before the suite runs; keep the seeded scoring_configs
    row and the seeded recruiter (login tests depend on it). All tables with FKs into each
    other must be listed in one TRUNCATE ... CASCADE, or Postgres rejects it.
    """
    with test_engine.connect() as conn:
        conn.execute(
            text(
                "TRUNCATE recruitment.recruiter_decisions, recruitment.applications, "
                "recruitment.match_results, recruitment.job_descriptions, recruitment.jobs "
                "CASCADE"
            )
        )
        conn.commit()
    yield


@pytest.fixture
def db_session(test_engine):
    """Per-test transactional session -- rolls back after each test."""
    conn = test_engine.connect()
    trans = conn.begin()
    Session = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
    session = Session()
    yield session
    session.close()
    trans.rollback()
    conn.close()


class _MockLLMProvider:
    """Returns a fixed structured JD; embed_text/generate_text are unused by RWS."""

    def __init__(self, structured=None):
        self._structured = structured

    async def embed_text(self, text, *, model=None, dimensions=768):
        raise NotImplementedError

    async def generate_text(self, prompt, *, model=None):
        raise NotImplementedError

    async def generate_structured(self, prompt, schema, *, model=None):
        if self._structured is not None:
            return schema(**self._structured)
        return schema(
            required_skills=["Python"],
            preferred_skills=["Docker"],
            min_years_experience=2.0,
            titles=["Software Engineer"],
        )


class _FailingLLMProvider(_MockLLMProvider):
    async def generate_structured(self, prompt, schema, *, model=None):
        from shared.llm import LLMProviderError
        raise LLMProviderError("Simulated JD structuring failure")


@pytest.fixture
def client(db_session, monkeypatch):
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


@pytest.fixture
def auth_headers(db_session) -> dict:
    """
    Returns {"Authorization": "Bearer <token>"} for the seeded recruiter.

    Mints the token directly via auth.create_access_token rather than going through the
    real HTTP /v1/auth/login endpoint, because this fixture only depends on db_session --
    not on the `client` fixture -- so it works uniformly whether a test uses `client` or
    `client_failing_llm` (both mutate the same module-level app.dependency_overrides, so
    depending on `client` here would clobber overrides set by `client_failing_llm` in
    tests that need both). The real login endpoint is exercised end-to-end by the
    dedicated login tests below.
    """
    from auth import create_access_token
    from db import get_recruiter_by_email
    from main import settings

    recruiter = get_recruiter_by_email(db_session, SEED_RECRUITER_EMAIL)
    assert recruiter is not None, "seeded recruiter not found -- did migration 0003 run?"
    token, _ = create_access_token(
        recruiter.id, recruiter.email, settings.jwt_secret, settings.jwt_expiry_minutes
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_second_brain(monkeypatch):
    """
    Patches clients.retrieve_candidates / clients.get_candidate (as imported by
    main.py's `import clients`) so tests never make a real HTTP call to Second Brain.

    Usage: mock_second_brain.set_candidates([{"candidate_id": ..., "skills": [...], ...}])
    Each entry needs: candidate_id (str), skills, years_of_experience, job_titles,
    similarity_score.
    """
    import clients

    state = {"candidates": []}

    async def _retrieve_candidates(base_url, api_key, query_text, top_k):
        return [
            {"candidate_id": c["candidate_id"], "similarity_score": c["similarity_score"]}
            for c in state["candidates"][:top_k]
        ]

    async def _get_candidate(base_url, api_key, candidate_id):
        cid = str(candidate_id)
        for c in state["candidates"]:
            if c["candidate_id"] == cid:
                return {
                    "candidate_id": cid,
                    "skills": c["skills"],
                    "years_of_experience": c["years_of_experience"],
                    "job_titles": c["job_titles"],
                }
        return None

    monkeypatch.setattr(clients, "retrieve_candidates", _retrieve_candidates)
    monkeypatch.setattr(clients, "get_candidate", _get_candidate)

    class _Controller:
        def set_candidates(self, candidates):
            state["candidates"] = candidates

    return _Controller()


def make_candidate(
    skills=None, years_of_experience=3.0, job_titles=None, similarity_score=0.8
) -> dict:
    return {
        "candidate_id": str(uuid.uuid4()),
        "skills": skills if skills is not None else ["Python"],
        "years_of_experience": years_of_experience,
        "job_titles": job_titles if job_titles is not None else ["Software Engineer"],
        "similarity_score": similarity_score,
    }
