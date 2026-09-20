"""
Integration test configuration for Recruitment Workflow Service.

Copy this file AND db_safety.py as /app/conftest.py + /app/db_safety.py inside the
csb-recruitment-workflow container, then run tests with:
    docker exec -e TEST_DATABASE_URL=... csb-recruitment-workflow \
        python -m pytest /app/test_recruitment_workflow.py -v
(both files -- this suite's destructive fixture below refuses to run without
db_safety.py's guard; see its own module docstring for why, and handoff.md for the
real incident that made this mandatory rather than optional.)
"""
import hashlib
import hmac
import json
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# db_safety.py lives alongside this file (tests/integration/ on the host; copied
# next to conftest.py in the container -- see this module's own docstring above).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from db_safety import resolve_test_database_url  # noqa: E402

TEST_WEBHOOK_HMAC_SECRET = "test-webhook-secret"


def sign_webhook_body(body: bytes, secret: str = TEST_WEBHOOK_HMAC_SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def make_webhook_payload(
    external_reference_id: str,
    interview_status: str = "completed",
    delivery_id: str | None = None,
    **assessment_overrides,
) -> tuple[bytes, dict]:
    """Returns (raw_body_bytes, headers) for a correctly-signed webhook POST."""
    assessment = {
        "overall_score": 78,
        "cefr_level": "B2",
        "fluency_score": 80,
        "vocabulary_score": 75,
        "grammar_score": 72,
        "pronunciation_score": 82,
        "comprehension_score": 85,
        "inconclusive": False,
        "evaluation_status": "completed",
    }
    assessment.update(assessment_overrides)
    payload = {
        "delivery_id": delivery_id or str(uuid.uuid4()),
        "external_reference_id": external_reference_id,
        "interview_status": interview_status,
        "assessment": assessment,
        "completed_at": "2026-08-26T00:00:00Z",
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Signature": sign_webhook_body(body),
        "X-Delivery-Id": payload["delivery_id"],
    }
    return body, headers


# ── Path setup (host-side only) ───────────────────────────────────────────────
_in_container = Path("/app/shared/__init__.py").exists()

if not _in_container:
    ROOT = Path(__file__).resolve().parents[2]
    SERVICE_DIR = ROOT / "services" / "recruitment-workflow"
    for p in (str(SERVICE_DIR), str(ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)

# ── Database URL ──────────────────────────────────────────────────────────────
# TEST_DATABASE_URL only -- deliberately no fallback to DATABASE_URL (that fallback,
# combined with running this suite inside the live csb-recruitment-workflow container
# where DATABASE_URL is the real demo database, is exactly what caused
# truncate_recruitment_tables below to wipe every job/application/match_result row
# once already; see db_safety.py's own docstring and handoff.md). This whole suite's
# engine is gated on it, not just the truncate fixture, since every test in this file
# goes through the same test_engine -> db_session chain.
_TEST_DB_URL = resolve_test_database_url()

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

    Safe by construction, not by care taken here: test_engine is built from
    _TEST_DB_URL, which is itself only ever resolve_test_database_url()'s return
    value (see above) -- this fixture has no DATABASE_URL fallback path to
    accidentally take, because it was never given one.
    """
    with test_engine.connect() as conn:
        conn.execute(
            text(
                "TRUNCATE recruitment.interview_webhook_events, "
                "recruitment.recruiter_decisions, recruitment.applications, "
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
    """
    Returns a fixed structured JD for generate_structured. generate_text backs the
    advisory match-explanation path (GET .../matches) -- returns a fixed string and
    records every call in .generate_text_calls so tests can assert on call count
    (e.g. "repeated reads do not call Gemini again") without a real Gemini call.
    embed_text is unused by RWS.

    Every generate_structured call is also recorded in .generate_structured_calls
    (prompts, not responses) -- this is what the JD-repair-pass tests assert on
    ("flagged -> exactly one repair call", "clean -> zero repair calls"), since
    submit_job's first structuring call and its (at most one) repair call both go
    through this same method. If repair_structured is given, the *second* call
    (the repair attempt -- main.py's repair_structured_extraction) returns it
    instead of structured; a third call is never made by submit_job, so this
    provider doesn't need to handle it.
    """

    def __init__(self, structured=None, explanation_text="Mock explanation.", repair_structured=None):
        self._structured = structured
        self._repair_structured = repair_structured
        self.explanation_text = explanation_text
        self.generate_text_calls = []
        self.generate_structured_calls = []

    async def embed_text(self, text, *, model=None, dimensions=768):
        raise NotImplementedError

    async def generate_text(self, prompt, *, model=None):
        self.generate_text_calls.append(prompt)
        return self.explanation_text

    async def generate_structured(self, prompt, schema, *, model=None):
        self.generate_structured_calls.append(prompt)
        if len(self.generate_structured_calls) > 1 and self._repair_structured is not None:
            return schema(**self._repair_structured)
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


class _FailingRepairLLMProvider(_MockLLMProvider):
    """First generate_structured call (the initial JD structuring) succeeds
    normally, returning whatever `structured` this was constructed with (expected
    to be a deliberately flagged extraction, so submit_job actually attempts a
    repair); every call after that (the repair attempt) raises LLMProviderError --
    simulating Gemini being unreachable, quota-exhausted, or otherwise failing
    specifically on the repair call. Used to prove a repair failure never blocks
    job creation: the job must still be created, with the original extraction
    persisted as "flagged"."""

    async def generate_structured(self, prompt, schema, *, model=None):
        self.generate_structured_calls.append(prompt)
        if len(self.generate_structured_calls) == 1:
            if self._structured is not None:
                return schema(**self._structured)
            return schema(required_skills=[], preferred_skills=[], min_years_experience=None, titles=[])
        from shared.llm import LLMProviderError
        raise LLMProviderError("Simulated JD repair failure")


class _FailingExplanationLLMProvider(_MockLLMProvider):
    """generate_structured works normally (JD submission succeeds); generate_text
    always fails, simulating Gemini being unreachable only for the advisory
    match-explanation path."""

    async def generate_text(self, prompt, *, model=None):
        from shared.llm import LLMProviderError
        self.generate_text_calls.append(prompt)
        raise LLMProviderError("Simulated match-explanation generation failure")


@pytest.fixture
def mock_llm() -> "_MockLLMProvider":
    """
    A single, shared _MockLLMProvider instance for the `client` fixture -- shared
    (not a fresh instance per call) so tests can assert on .generate_text_calls across
    multiple requests within the same test (e.g. "a second GET .../matches does not
    call Gemini again").
    """
    return _MockLLMProvider()


@pytest.fixture
def client(db_session, monkeypatch, mock_llm):
    from fastapi.testclient import TestClient
    from main import app, settings, get_llm_provider, get_llm_provider_optional
    from db import get_db

    original_key = settings.service_api_key
    original_webhook_secret = settings.webhook_hmac_secret
    settings.service_api_key = _TEST_SERVICE_KEY
    settings.webhook_hmac_secret = TEST_WEBHOOK_HMAC_SECRET

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_llm_provider] = lambda: mock_llm
    app.dependency_overrides[get_llm_provider_optional] = lambda: mock_llm

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()
    settings.service_api_key = original_key
    settings.webhook_hmac_secret = original_webhook_secret


@pytest.fixture
def client_failing_llm(db_session):
    from fastapi.testclient import TestClient
    from main import app, settings, get_llm_provider, get_llm_provider_optional
    from db import get_db

    original_key = settings.service_api_key
    original_webhook_secret = settings.webhook_hmac_secret
    settings.service_api_key = _TEST_SERVICE_KEY
    settings.webhook_hmac_secret = TEST_WEBHOOK_HMAC_SECRET

    def _override_get_db():
        yield db_session

    failing = _FailingLLMProvider()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_llm_provider] = lambda: failing
    app.dependency_overrides[get_llm_provider_optional] = lambda: failing

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()
    settings.service_api_key = original_key
    settings.webhook_hmac_secret = original_webhook_secret


@pytest.fixture
def client_failing_repair(db_session):
    """
    Like `client`, but the JD-repair-pass's own Gemini call always fails while the
    initial JD structuring succeeds (returning a deliberately flagged extraction,
    an empty one by default) -- for proving a repair failure never blocks job
    creation. Exposes the provider as `c.failing_llm` (same attribute name
    `client_failing_explanation` uses) so a test can inspect
    `.generate_structured_calls` to confirm exactly one repair attempt was made.
    """
    from fastapi.testclient import TestClient
    from main import app, settings, get_llm_provider, get_llm_provider_optional
    from db import get_db

    original_key = settings.service_api_key
    original_webhook_secret = settings.webhook_hmac_secret
    settings.service_api_key = _TEST_SERVICE_KEY
    settings.webhook_hmac_secret = TEST_WEBHOOK_HMAC_SECRET

    def _override_get_db():
        yield db_session

    failing = _FailingRepairLLMProvider()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_llm_provider] = lambda: failing
    app.dependency_overrides[get_llm_provider_optional] = lambda: failing

    with TestClient(app, raise_server_exceptions=True) as c:
        c.failing_llm = failing
        yield c

    app.dependency_overrides.clear()
    settings.service_api_key = original_key
    settings.webhook_hmac_secret = original_webhook_secret


@pytest.fixture
def client_failing_explanation(db_session):
    """
    Like `client`, but the advisory match-explanation LLM call (generate_text) always
    fails while JD structuring (generate_structured) succeeds normally -- for proving
    a Gemini failure on the explanation path never breaks matching/qualification.
    """
    from fastapi.testclient import TestClient
    from main import app, settings, get_llm_provider, get_llm_provider_optional
    from db import get_db

    original_key = settings.service_api_key
    original_webhook_secret = settings.webhook_hmac_secret
    settings.service_api_key = _TEST_SERVICE_KEY
    settings.webhook_hmac_secret = TEST_WEBHOOK_HMAC_SECRET

    def _override_get_db():
        yield db_session

    failing = _FailingExplanationLLMProvider()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_llm_provider] = lambda: failing
    app.dependency_overrides[get_llm_provider_optional] = lambda: failing

    with TestClient(app, raise_server_exceptions=True) as c:
        c.failing_llm = failing
        yield c

    app.dependency_overrides.clear()
    settings.service_api_key = original_key
    settings.webhook_hmac_secret = original_webhook_secret


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
    Patches clients.retrieve_candidates / clients.get_candidate / clients.send_candidate_feedback
    (as imported by main.py's `import clients`) so tests never make a real HTTP call to
    Second Brain.

    Usage: mock_second_brain.set_candidates([{"candidate_id": ..., "skills": [...], ...}])
    Each entry needs: candidate_id (str), skills, years_of_experience, job_titles,
    similarity_score.

    Feedback write-back calls are recorded in .feedback_calls (list of kwargs dicts).
    Call .fail_feedback() to make subsequent feedback calls raise SecondBrainClientError
    (simulates Second Brain being unreachable for the background write-back).
    """
    import clients
    from clients import SecondBrainClientError

    state = {"candidates": [], "feedback_calls": []}

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

    async def _send_candidate_feedback(
        base_url, api_key, candidate_id, application_id, decision, job_title,
        matched_requirements, missing_requirements, final_score, decided_at,
    ):
        state["feedback_calls"].append({
            "candidate_id": str(candidate_id),
            "application_id": str(application_id),
            "decision": decision,
            "job_title": job_title,
            "matched_requirements": matched_requirements,
            "missing_requirements": missing_requirements,
            "final_score": final_score,
            "decided_at": decided_at,
        })

    async def _failing_send_candidate_feedback(*args, **kwargs):
        raise SecondBrainClientError("simulated Second Brain network failure")

    monkeypatch.setattr(clients, "retrieve_candidates", _retrieve_candidates)
    monkeypatch.setattr(clients, "get_candidate", _get_candidate)
    monkeypatch.setattr(clients, "send_candidate_feedback", _send_candidate_feedback)

    class _Controller:
        def __init__(self):
            self.feedback_calls = state["feedback_calls"]

        def set_candidates(self, candidates):
            state["candidates"] = candidates

        def fail_feedback(self):
            monkeypatch.setattr(clients, "send_candidate_feedback", _failing_send_candidate_feedback)

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


@pytest.fixture
def mock_resume_intelligence(monkeypatch):
    """
    Patches resume_intelligence_client.get_contact (as imported by main.py's
    `import resume_intelligence_client`) so tests never make a real HTTP call.

    Usage: mock_resume_intelligence.set_contact(candidate_id, {"name": ..., "email": ...})
    Not calling set_contact for a candidate_id means "no contact record" (404 -> None),
    which is the default for every candidate_id unless explicitly set.

    Call .fail() to make every subsequent get_contact call raise
    ResumeIntelligenceClientError instead (simulates Resume Intelligence being
    unreachable, as opposed to reachable-but-no-contact-record, which set_contact's
    own default already covers).
    """
    import resume_intelligence_client
    from resume_intelligence_client import ResumeIntelligenceClientError

    state = {"contacts": {}, "should_fail": False}

    async def _get_contact(base_url, api_key, candidate_id):
        if state["should_fail"]:
            raise ResumeIntelligenceClientError("simulated Resume Intelligence network failure")
        return state["contacts"].get(str(candidate_id))

    monkeypatch.setattr(resume_intelligence_client, "get_contact", _get_contact)

    class _Controller:
        def set_contact(self, candidate_id, contact: dict) -> None:
            state["contacts"][str(candidate_id)] = contact

        def fail(self) -> None:
            state["should_fail"] = True

    return _Controller()


@pytest.fixture
def mock_voicehr(monkeypatch):
    """
    Patches voicehr_client.invite_candidate and voicehr_client.resend_invite (as
    imported by main.py's `import voicehr_client`) so tests never make a real HTTP
    call to VoiceHR.

    Records every call in .calls / .resend_calls for assertion. Call .fail() /
    .fail_resend() to make subsequent calls of that kind raise VoiceHRClientError
    (simulates VoiceHR being unreachable or a delivery failure -- indistinguishable
    from RWS's side either way, see voicehr_client's own docstrings).
    """
    import voicehr_client
    from voicehr_client import VoiceHRClientError

    calls = []
    resend_calls = []

    async def _invite_candidate(
        base_url, api_key, idempotency_key, candidate_name, candidate_email,
        language, external_reference_id, job_title=None,
    ):
        calls.append({
            "idempotency_key": idempotency_key,
            "candidate_name": candidate_name,
            "candidate_email": candidate_email,
            "language": language,
            "external_reference_id": external_reference_id,
            "job_title": job_title,
        })
        return {
            "interview_link": "http://voicehr.test/interview?token=abc",
            "expires_at": "2026-09-01T00:00:00Z",
            "external_reference_id": external_reference_id,
        }

    async def _failing_invite_candidate(*args, **kwargs):
        raise VoiceHRClientError("simulated VoiceHR network failure")

    async def _resend_invite(base_url, api_key, external_reference_id):
        resend_calls.append({"external_reference_id": external_reference_id})

    async def _failing_resend_invite(*args, **kwargs):
        raise VoiceHRClientError("simulated VoiceHR resend delivery failure")

    monkeypatch.setattr(voicehr_client, "invite_candidate", _invite_candidate)
    monkeypatch.setattr(voicehr_client, "resend_invite", _resend_invite)

    class _Controller:
        def __init__(self):
            self.calls = calls
            self.resend_calls = resend_calls

        def fail(self) -> None:
            monkeypatch.setattr(voicehr_client, "invite_candidate", _failing_invite_candidate)

        def fail_resend(self) -> None:
            monkeypatch.setattr(voicehr_client, "resend_invite", _failing_resend_invite)

    return _Controller()


@pytest.fixture
def mock_interview_round_mailer(monkeypatch):
    """
    Patches interview_round_mailer.send_interview_round_notification (as imported
    by main.py's `import interview_round_mailer`) so tests never make a real HTTP
    call to Resend. Defaults to succeeding; call .fail() to make subsequent calls
    raise NotificationDeliveryError (simulates Resend being unreachable or
    rejecting the request) -- independent of settings.resend_api_key, which is
    simply unset in this test environment (see the dedicated
    "missing RESEND_API_KEY" tests, which deliberately do NOT use this fixture, to
    exercise that real not-configured path with the real, unmocked mailer).

    Records every call's kwargs in .calls for assertion (e.g. confirming the
    candidate's email/name never leaked into a field it shouldn't have).
    """
    import interview_round_mailer
    from interview_round_mailer import NotificationDeliveryError

    calls = []
    state = {"should_fail": False, "error": "simulated Resend failure"}

    async def _send(**kwargs):
        calls.append(kwargs)
        if state["should_fail"]:
            raise NotificationDeliveryError(state["error"])

    monkeypatch.setattr(interview_round_mailer, "send_interview_round_notification", _send)

    class _Controller:
        def __init__(self):
            self.calls = calls

        def fail(self, error: str = "simulated Resend failure") -> None:
            state["should_fail"] = True
            state["error"] = error

    return _Controller()


def select_and_invite(
    client, auth_headers, mock_second_brain, mock_resume_intelligence,
    mock_voicehr, candidate=None,
) -> tuple[str, str]:
    """
    Test helper: submits a job, selects the one qualified candidate, sets up a
    passing contact lookup, and invites it. Returns (job_id, application_id) with the
    application left in the 'invited' state -- the common starting point for webhook
    and decision tests.
    """
    candidate = candidate or make_candidate(skills=["Python"], similarity_score=0.9)
    mock_second_brain.set_candidates([candidate])
    mock_resume_intelligence.set_contact(
        candidate["candidate_id"],
        {
            "candidate_id": candidate["candidate_id"],
            "name": "Test Candidate",
            "email": "candidate@example.com",
            "phone": None,
        },
    )

    submit_resp = client.post(
        "/v1/jobs",
        json={
            "title": "Backend Engineer",
            "raw_description": "We need a backend engineer skilled in Python.",
            "threshold": 0.1,
            "top_k": 20,
        },
        headers=auth_headers,
    )
    job_id = submit_resp.json()["job_id"]
    match_id = submit_resp.json()["matches"][0]["match_id"]

    application_id = client.post(
        f"/v1/matches/{match_id}/select", headers=auth_headers
    ).json()["application_id"]

    client.post(
        f"/v1/applications/{application_id}/invite",
        json={"language": "en"},
        headers=auth_headers,
    )
    return job_id, application_id
