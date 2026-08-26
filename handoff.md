# Company Second Brain — Implementation Handoff

Architecture reference: see the full "Company Second Brain — Implementation Handoff"
document delivered at project start (4 bounded contexts, Postgres+pgvector single instance
with 3 schemas, sync HTTP APIs, VoiceHR webhook, deterministic+semantic hybrid scoring,
immutable MatchResult, Gemini as sole AI provider). That document's architectural decisions
are NON-NEGOTIABLE for this build unless a genuine blocking issue is found during
implementation — see the bootstrap instructions.

## Current Implementation Status

- Architecture design: **DONE** (pre-existing, not produced this session)
- Free hosted AI model selection: **DONE** (pre-existing)
- Monorepo skeleton (directories only): **DONE**
- `docker-compose.yml` + `.env.example` per service: **DONE** (YAML validated; `docker compose
  up --build` NOT yet runnable — no Docker CLI in the authoring sandbox, and Dockerfiles don't
  exist until Step 4 anyway. Must be verified in a real Docker environment at Step 4.)
- Postgres init script (3 schemas, 3 least-privilege roles): **DONE** (manually reviewed for
  correctness — no cross-schema grants confirmed by grep; not yet run against a live Postgres,
  no daemon available in the authoring sandbox. Must be verified at Step 4.)
- Alembic setup per schema: **DONE** — all three `alembic upgrade head` commands ran against
  the live Postgres container and completed successfully. `alembic_version` table confirmed in
  `resume_intel`, `second_brain`, and `recruitment` schemas (verified by
  `pg_tables` query). Three schemas and three least-privilege roles also confirmed in
  `information_schema` and `pg_roles`. No cross-schema grants.
- FastAPI skeletons (`GET /health` x4): **DONE** for resume-intelligence, second-brain,
  recruitment-workflow. `GET /health` added to VoiceHR's existing main.py. All three new
  services built, started, and returned `{"status":"ok","service":"<name>"}` on `GET /health`
  against the live Docker stack. VoiceHR container also built and running —
  `GET /health` returns `{"status":"ok","service":"voicehr"}` confirmed via container exec.
  All verification targets met.
- Shared Pydantic schemas (`extra="forbid"`): **DONE** — `shared/schemas/__init__.py`:
  `CandidateSummary`, `ContactLookupResponse`, `RetrieveCandidatesRequest/Response`,
  `CandidateMatch`, `InvitationRequest/Response`, `InterviewAssessment`, `WebhookPayload`.
  All models use `ConfigDict(extra="forbid")`. PII fields (name, email, phone) present only in
  `ContactLookupResponse` and `InvitationRequest`; all other schemas reject them.
  41 schema unit tests in `tests/unit/test_schemas.py`; all 63 unit tests pass.
- Gemini API key wiring: **NOT STARTED**
- `LLMProvider` interface: **DONE** — `shared/llm.py`: `LLMProviderError`, abstract `LLMProvider`
  (`generate_text`, `generate_structured`, `embed_text`), `GeminiProvider` (Gemini 2.5 Flash
  default for text; `gemini-embedding-001` for embeddings via `loop.run_in_executor` wrapping
  the sync `client.models.embed_content`). 22 unit tests pass (`tests/unit/test_llm.py`).
  **Live Gemini API not verified** — `GEMINI_API_KEY` is empty in all service `.env` files.
- Resume Intelligence candidates table + dedup + contact endpoint: **DONE** — `resume_intel.candidates`
  table created by Alembic migration `0002` (UUID PK, `email_normalized TEXT UNIQUE`, JSONB,
  TIMESTAMPTZ). `store_candidate` in `db.py` does normalized-email dedup (strip + lower). FastAPI
  `api.py` exposes `POST /v1/resumes` (upload + parse + store) and
  `GET /v1/candidates/{candidate_id}/contact` (service-key auth via `x-api-key` header).
  `ContactLookupResponse` from shared schemas is the response model. Migration confirmed running
  inside container. All 20 integration tests pass inside the container (`pytest` run in
  `csb-resume-intelligence`). Limitations: host-side psycopg cannot reach Docker Postgres due to
  Docker Desktop WSL2 network intercepting port 5432 before pg_hba.conf applies — tests must run
  inside the container. VoiceHR container still not built (disk constraint, see Known Limitations).
- Second Brain knowledge upsert + pgvector embedding: **DONE** — `second_brain.candidate_knowledge`
  table created by Alembic migration `0002` (`candidate_id UUID PK`, `skills/job_titles JSONB`,
  `raw_text TEXT`, `source_version TEXT`, `embedding vector(768) NOT NULL`, timestamps). HNSW
  index on `embedding` using `vector_cosine_ops`. `db.py`: `CandidateKnowledge` ORM model,
  `upsert_candidate_knowledge` via PostgreSQL `ON CONFLICT DO UPDATE` (preserves `created_at`,
  refreshes `updated_at`). `main.py`: `POST /v1/knowledge/candidates` with service-key auth,
  validates `CandidateSummary` (PII rejected via `extra="forbid"` → 422), calls
  `get_llm_provider()` (lazy singleton, 503 if no key), embeds `raw_text` via `embed_text`
  (502 on failure), upserts to DB. 17 integration tests pass in `csb-second-brain` container
  (valid ingestion, DB persistence, embedding stored, upsert idempotency, `created_at`
  preserved, PII rejection for email/name/phone, missing/invalid payload, auth, embedding
  failure 502). **Live Gemini embedding verified** — `POST /v1/knowledge/candidates` returned
  200 with a real `gemini-embedding-001` key; `second_brain.vector_dims(embedding) = 768`
  confirmed in the DB. The `vector(768)` column dimension is correct.
- Second Brain semantic retrieval: **DONE** — `search_candidates()` in `db.py` executes cosine
  distance query via pgvector `<=>` operator with HNSW index, similarity = `GREATEST(0, LEAST(1,
  1 - distance))`, supports `candidate_ids` filter. `main.py`: `POST /v1/retrieve/candidates`
  validates `RetrieveCandidatesRequest` (PII rejected via `extra="forbid"` → 422), embeds
  `query_text` via live Gemini (502 on failure), calls `search_candidates`, returns
  `RetrieveCandidatesResponse` with `CandidateMatch` list. SQL uses vector literal embedded in
  f-string (safe: model output, not user input) to avoid psycopg3 + `::vector` bind parameter
  conflict. 34/34 integration tests pass in `csb-second-brain` (all 17 ingestion + 17 retrieval
  tests including empty KB, top-k, ordering, clamping, `candidate_ids` filter, PII rejection,
  auth, embedding failure). Conftest adds session-scoped `TRUNCATE` to prevent smoke-test row
  leakage. **Live smoke test verified**: ingest → retrieve returns `similarity_score: 0.733`
  from real `gemini-embedding-001` + pgvector cosine search.
- Second Brain candidate detail lookup: **DONE** (added to support RWS scoring, see below) —
  `GET /v1/knowledge/candidates/{candidate_id}` returns `CandidateSummary` (skills,
  years_of_experience, job_titles, raw_text, source_version) for one candidate, 404 if absent,
  service-key auth. Retrieval (`POST /v1/retrieve/candidates`) intentionally still returns only
  `candidate_id` + `similarity_score` (recall only) — RWS calls this new endpoint separately per
  retrieved candidate to get the attributes it needs for deterministic scoring, keeping recall
  and detail-fetch cleanly separated. 3 new integration tests added; 37/37 second-brain
  integration tests pass.
- Recruitment Workflow Service (RWS) core — JD structuring, hybrid matching, immutable
  MatchResult: **DONE**.
  - `services/recruitment-workflow/scoring.py`: pure, DB/HTTP-free deterministic scoring engine.
    `skill_score` = fraction of (required ∪ preferred) skills the candidate has (case-insensitive);
    `experience_score` = 1.0 if no minimum or met/exceeded, partial credit otherwise, 0.0 if
    required but candidate experience unknown; `title_score` = binary case-insensitive
    substring match against JD titles, or 1.0 if the JD specifies none.
    `deterministic_score` = weighted sum of the three. `final_score` = weighted sum of
    `semantic_score` (from Second Brain) and `deterministic_score`. The **required-skill hard
    gate** is evaluated independently: `qualified = hard_gate_passed AND final_score >= threshold`,
    so a missing required skill disqualifies a candidate regardless of how high the semantic or
    final score is. The LLM is never involved in this module.
  - `services/recruitment-workflow/db.py`: `Job`, `JobDescription` (versioned, unique on
    `(job_id, version)`), `ScoringConfig` (versioned), `MatchResult` (immutable — no update path
    exists anywhere in the code, only inserts) ORM models + CRUD functions.
  - Alembic migration `0002` (`recruitment.jobs`, `job_descriptions`, `scoring_configs`,
    `match_results`) ran against the live Postgres container — confirmed via `\dt recruitment.*`.
    CHECK constraints enforce both weight groups sum to 1.0 (`ABS(sum - 1.0) < 0.0001`) as
    defense-in-depth behind the Pydantic `model_validator` in `schemas.py`. A default
    `scoring_configs` row (version 1: 0.5/0.5 hybrid, 0.6/0.2/0.2 skill/experience/title) is
    seeded by the migration and confirmed present in the DB.
  - `POST /v1/jobs`: structures the JD via `llm.generate_structured` (advisory extraction
    only — never scores/qualifies), persists `Job` + `JobDescription` v1, calls Second Brain
    retrieval (recall only) with the raw JD text as the query, fetches each retrieved
    candidate's detail via the new Second Brain endpoint, scores every candidate with
    `scoring.score_candidate`, and persists one immutable `MatchResult` per candidate
    regardless of qualification outcome (auditable full match set). Response is sorted by
    `final_score` descending. 502 on JD-structuring failure or Second Brain call failure; 422 on
    an unknown `scoring_config_version`.
  - `GET /v1/jobs/{job_id}/matches`: read-only listing of persisted `MatchResult`s for a job
    (404 if the job doesn't exist). Recruiter decision endpoints (accept/reject) are explicitly
    NOT implemented this session, per scope.
  - `POST /v1/scoring-configs`: creates a new versioned `ScoringConfig`; rejects (422) if either
    weight group doesn't sum to 1.0.
  - `shared/http_client.py` implemented (was an empty scaffold): one thin async
    `call_service()` helper (API-key header, timeout, no retry machinery) used by
    `services/recruitment-workflow/clients.py` for both Second Brain calls.
  - Auth: all three new RWS endpoints use `x-api-key` service-key auth (same pattern as
    Resume Intelligence / Second Brain), **not** JWT. JWT recruiter auth is explicitly deferred
    to the recruiter-decision-endpoints step (see Known Limitations) — this task's scope
    excluded recruiter decisions entirely, so there is no recruiter-facing endpoint yet that
    would need it.
  - Tests: 27 unit tests for `scoring.py` (`tests/unit/test_scoring.py`, pure functions, no
    DB/HTTP — includes an explicit test that a missing required skill disqualifies a candidate
    even at 0.99 semantic score) + 21 integration tests
    (`tests/integration/test_recruitment_workflow.py`, Second Brain mocked via
    `monkeypatch.setattr(clients, ...)`, covering JD submission, structuring failure,
    retrieval failure, hard-gate disqualification, threshold disqualification, score ordering,
    MatchResult persistence of every required component, `GET .../matches`, scoring-config
    weight validation, and pinning a specific `scoring_config_version`). All pass inside
    `csb-recruitment-workflow`. Full regression: 90/90 unit tests, 37/37 second-brain
    integration tests, 21/21 RWS integration tests.
  - **Live end-to-end smoke test verified**: two candidates ingested into Second Brain with
    real `gemini-embedding-001` embeddings (one Python/FastAPI/PostgreSQL/Docker, one
    Java/Spring) → `POST /v1/jobs` with a real JD against live Gemini structuring + live
    pgvector retrieval → the Python candidate scored `final_score: 0.901`, `qualified: true`,
    all three required skills matched; the Java candidate correctly failed the hard gate
    (`hard_gate_passed: false`, `qualified: false`) despite a `0.579` semantic score. Verified
    persisted correctly in `recruitment.match_results` via direct `psql` query and via
    `GET /v1/jobs/{job_id}/matches`.
- Recruiter authentication + candidate selection/decision workflow: **DONE**.
  - `services/recruitment-workflow/auth.py`: `hash_password`/`verify_password` (bcrypt),
    `create_access_token`/`decode_access_token` (PyJWT, HS256, `exp`+`iat` claims). Fully
    independent of `require_service_key` — a JWT is read from `Authorization: Bearer` and
    verified against `JWT_SECRET`; a service key is read from `X-Api-Key` and compared
    against `SERVICE_API_KEY`. Neither can satisfy the other's check (verified by tests in
    both directions — see below).
  - Migration `0003`: `recruiters` (email UNIQUE, `password_hash`, `name`), `applications`
    (state machine: `selected` → `accepted` | `rejected`, `UNIQUE(job_id, candidate_id)`,
    `UNIQUE(match_result_id)`, CHECK on `status`), `recruiter_decisions` (append-only audit
    log, no update path anywhere in the code — only inserts). One recruiter is seeded by the
    migration itself: it reads `SEED_RECRUITER_EMAIL`/`SEED_RECRUITER_PASSWORD` from the
    environment, hashes the password with bcrypt at migration time, and inserts the row — no
    plaintext or hash is ever hardcoded in the migration file. Ran clean against the live
    Postgres container; confirmed via `\dt recruitment.*` (8 tables now) and a direct
    `SELECT` on `recruiters`.
  - `POST /v1/auth/login`: verifies bcrypt hash, issues a JWT with `JWT_EXPIRY_MINUTES`
    expiry (default 45). Never accepts or checks `X-Api-Key`.
  - `require_recruiter` dependency: parses `Authorization: Bearer`, decodes+verifies the
    JWT, loads the `Recruiter` row by `sub` (401 if the token is missing/malformed/
    expired/wrong-signature, or if the recruiter no longer exists).
  - `POST /v1/jobs` and `GET /v1/jobs/{job_id}/matches` now require `require_recruiter`
    (moved off `require_service_key`, per this session's task — closes the gap flagged in
    the previous session's Known Limitations). `POST /v1/scoring-configs` deliberately
    stays on `require_service_key`: it's a system/admin configuration action, not part of
    the recruiter's day-to-day selection/decision workflow.
  - Candidate selection + decisions (new `Application` state machine, forward-compatible
    with the future VoiceHR-driven states — not implemented yet):
    `POST /v1/matches/{match_id}/select` (creates an `Application` in `selected` state;
    422 if the match isn't qualified; 409 on a duplicate selection for the same
    `(job_id, candidate_id)`, enforced by a DB UNIQUE constraint rather than a
    check-then-act race), `POST /v1/applications/{id}/accept` and `/reject` (atomic
    conditional `UPDATE ... WHERE status = 'selected'`, so a duplicate/concurrent decision
    call always gets 409 regardless of timing — the same code path is exercised whether the
    two calls are truly simultaneous or sequential), `GET /v1/jobs/{job_id}/applications`
    (recruiter-facing listing). Every successful accept/reject is recorded in
    `recruiter_decisions` (recruiter_id, decision, previous/new status, timestamp) —
    verified both by direct `psql` query and by dedicated tests.
  - Tests: 10 unit tests for `auth.py` (`tests/unit/test_auth.py` — hash/verify roundtrip,
    salting, JWT roundtrip, expiry, wrong secret, malformed/empty token) + 49 integration
    tests in `tests/integration/test_recruitment_workflow.py` (up from 21 — added login
    success/failure, expired/malformed/wrong-signature JWT rejection, protected-endpoint-
    without-JWT, valid recruiter authorization, both directions of JWT-vs-service-key
    separation, select/accept/reject happy paths, decision persistence + audit content,
    disqualified-match selection rejected, duplicate selection 409, all four invalid state
    transitions (accept-after-accept, reject-after-accept, accept-after-reject,
    reject-after-reject), and an explicit check that a duplicate accept call does not
    create a second audit row). All pass inside `csb-recruitment-workflow`. Full regression
    after this work: 100/100 unit tests, 37/37 second-brain integration tests, 20/20
    resume-intelligence integration tests, 49/49 RWS integration tests.
  - **Live end-to-end smoke test verified** against real Postgres (no mocks): logged in as
    the seeded recruiter, submitted a JD with a valid JWT (rejected with `x-api-key` alone
    and with no auth at all), selected the qualified candidate's match, accepted the
    application, confirmed the `recruiter_decisions` row via `psql`, and confirmed that a
    second `accept` call, a `reject` after accept, and a duplicate `select` all correctly
    returned 409. Selecting the disqualified candidate's match correctly returned 422.
- VoiceHR integration (invitation + signed completion webhook): **DONE**. VoiceHR's real
  source was already present (not a skeleton, despite `services/voicehr/README.md` still
  saying so — that file is stale and should be updated). Its `POST /api/invite` endpoint
  and `process_interview()` (Vapi webhook handler → evaluate → email report) already
  existed; both were extended, not rewritten. Its SQLite persistence and interview
  engine were not touched beyond adding columns via the existing
  ALTER-TABLE-if-missing pattern in `backend/database.py`.
  - **VoiceHR side** (`services/voicehr/`):
    - `backend/database.py`: added `external_reference_id`, `idempotency_key`,
      `webhook_delivery_id`, `webhook_delivered_at` columns to `interviews`.
    - `backend/core/config.py`: added `rws_service_api_key`, `rws_webhook_hmac_secret`,
      `rws_webhook_url` (the `.env`/`.env.example` values for these already existed from
      an earlier session — `RWS_SERVICE_API_KEY`, `RWS_WEBHOOK_HMAC_SECRET`,
      `RWS_WEBHOOK_URL=http://recruitment-workflow:8000/v1/webhooks/voicehr` — only the
      `Settings` fields to read them were missing).
    - `backend/api/routes/invite.py`: `POST /api/invite` now requires `X-Api-Key`
      (`require_rws_service_key`), accepts an optional `external_reference_id` (so
      non-RWS callers like the admin UI are unaffected) and an optional
      `Idempotency-Key` header. A repeated call with the same idempotency key returns
      the existing interview instead of creating a duplicate; a new key (new attempt
      number) creates a new one. Response gained `interview_link` (alongside the
      existing `link`, kept for backward compatibility) and echoes
      `external_reference_id`.
    - `backend/services/rws_client.py` (new): `build_assessment_payload()` shapes the
      evaluator's full result dict down to exactly `InterviewAssessment`'s fields (that
      schema is `extra="forbid"` on the RWS side); `interview_status_for()` maps to
      `completed`/`inconclusive`/`failed`; `notify_rws_interview_result()` signs the
      JSON body with HMAC-SHA256 and POSTs it with `X-Signature` + `X-Delivery-Id`
      headers. Best-effort, single attempt, never raises — a failure to reach RWS is
      logged and swallowed so it can never break VoiceHR's own report-email flow.
    - `backend/api/routes/webhook.py`'s `process_interview()`: after the existing
      status/report/recording persistence (unchanged), if the interview has an
      `external_reference_id` it now calls `notify_rws_interview_result()` and records
      `webhook_delivery_id`/`webhook_delivered_at` on the interview row. Interviews with
      no `external_reference_id` (non-RWS-originated) never trigger this.
    - Tests: existing `tests/test_invite.py`/`test_integration.py` calls updated with the
      new required `x-api-key` header (8 + 3 call sites); `tests/conftest.py`'s
      `mock_settings` and `create_interview()` extended; `tests/test_database.py`'s
      exact-schema assertion updated to include the new columns (it was already stale —
      missing the pre-existing `invite_sent` column — before this session touched it).
      13 new tests added (external_reference_id persistence, missing/wrong API key,
      duplicate-invitation idempotency, re-invitation with incremented attempt creates a
      new interview, outbound webhook fired only when `external_reference_id` is set and
      with the correct `completed`/`inconclusive`/`failed` mapping, RWS-unreachable
      doesn't break the report email). Full VoiceHR suite: 119/121 pass — the 2 failures
      (`test_report.py::test_health_check`, `test_session.py::...gemini_live...`) are
      confirmed pre-existing and unrelated (verified by reading the actual `/health`
      route and the gemini_live pipeline code, neither touched this session).
  - **RWS side** (`services/recruitment-workflow/`):
    - Migration `0004`: extends `applications.status`'s CHECK constraint to
      `selected → invited → interviewed → accepted | rejected` (accept/reject now valid
      from any of the first three, not just `selected` — the interview is informative
      for the recruiter's decision, not a mandatory gate). Adds `invite_attempt_number`,
      `invited_at`, `interview_status`, `interview_assessment`, `interview_completed_at`
      to `applications`. Adds `interview_webhook_events` (append-only, `delivery_id
      UNIQUE` — this is the dedup mechanism). **Found and fixed a real bug during live
      testing**: the first version of this migration made
      `interview_webhook_events.application_id` a foreign key into `applications`, which
      caused a genuinely-unknown `external_reference_id` to raise `IntegrityError` on
      insert (FK violation) — indistinguishable from the *intended* `IntegrityError`
      (duplicate `delivery_id`), so every "unknown application" webhook was
      misclassified as "duplicate" until this was caught live. Fixed by making
      `application_id` a plain UUID column, not a FK: this table is a receipt log, and
      must be able to record a reference that doesn't resolve to a real row.
    - `db.py`: `Recruiter`... unchanged; `Application` gains the columns above;
      `transition_application()` now accepts the decision from `('selected', 'invited',
      'interviewed')` instead of only `'selected'`; new `record_invite_attempt()`
      (atomic `UPDATE ... WHERE status IN ('selected','invited')`, sets `invited`) and
      `record_interview_result()` (atomic `UPDATE ... WHERE status = 'invited'`, sets
      `interviewed` + assessment) — both return `None` (a no-op, not an error) on no
      matching row, exactly like the existing decision-transition pattern. New
      `InterviewWebhookEvent` model + `insert_webhook_event()` /
      `mark_webhook_event_processed()`.
    - `voicehr_client.py` (new): `invite_candidate()` — POSTs to VoiceHR's `/api/invite`
      with `Idempotency-Key: "{application_id}:{attempt_number}"`.
    - `resume_intelligence_client.py` (new): `get_contact()` — the pre-flight lookup;
      returns `None` on 404, never persists the PII-bearing response (passes it straight
      through to VoiceHR, per the architecture's "transient use only" rule for
      `ContactLookupResponse`).
    - `shared/http_client.py`: added an `extra_headers` param to `call_service()` (used
      for `Idempotency-Key`) — additive, backward compatible.
    - `POST /v1/applications/{id}/invite` (JWT-protected): loads the application (404 if
      missing, 409 if not in `selected`/`invited`), calls the Resume Intelligence contact
      lookup — **422 immediately, without ever calling VoiceHR, if no contact record
      exists** — then calls VoiceHR with `attempt_number = current + 1`, persisting that
      number only after VoiceHR responds successfully. This ordering is deliberate: if
      VoiceHR is called but RWS never receives the response, retrying recomputes the
      *same* attempt number (nothing was persisted), so the retry reuses the same
      idempotency key and VoiceHR's own dedup — not a fresh duplicate interview — is what
      protects it. 502 if VoiceHR is unreachable.
    - `POST /v1/webhooks/voicehr` (no JWT, no `x-api-key` — HMAC-SHA256 signature only,
      the third and deliberately separate auth mechanism per CLAUDE.md): verifies
      `X-Signature` over the raw request body (401 if missing/invalid), requires
      `X-Delivery-Id` (422 if missing), validates the body against `WebhookPayload`
      (`shared.schemas`, 422 on malformed), then inserts into
      `interview_webhook_events` **before any state mutation** — a duplicate
      `delivery_id` raises `IntegrityError` here and short-circuits to `200
      duplicate_ignored` with the application untouched, which is what makes dedup safe
      even under a true race between two deliveries of the same event. Only after that
      insert succeeds does it resolve `external_reference_id` to an application and call
      `record_interview_result()`; an unresolvable/malformed reference or an application
      not currently `invited` both return `200` (no-op, not an error) rather than 404/409
      — VoiceHR should never retry-storm on either condition. Any genuinely unexpected
      exception is not caught and surfaces as the framework's default 500.
    - Auth summary (all three mechanisms now in use and verified mutually exclusive by
      tests): recruiter JWT (`Authorization: Bearer`) for `/v1/jobs`,
      `/v1/jobs/{id}/matches`, `/v1/applications/{id}/invite`,
      `/v1/matches/{id}/select`, `/v1/applications/{id}/accept|reject`; service API key
      (`X-Api-Key`) for `/v1/scoring-configs` and outbound RWS→Second Brain/Resume
      Intelligence/VoiceHR calls; HMAC-SHA256 signature (`X-Signature`) for the inbound
      VoiceHR webhook only.
    - Tests: 69 integration tests in `tests/integration/test_recruitment_workflow.py`
      (up from 49) — successful invitation, missing-contact 422 without calling VoiceHR,
      VoiceHR-unavailable 502, re-invitation incrementing attempt number, duplicate
      invitation, invite requires JWT not service key, invalid/missing HMAC signature,
      duplicate webhook delivery (idempotent), unknown/malformed `external_reference_id`
      (200 no-op), malformed webhook payload (422), webhook for an application not yet
      invited (200 no-op), inconclusive assessment persisted, a genuinely unexpected
      error propagating rather than being swallowed, and accept/reject now working from
      `interviewed`. 3 new unit tests for `shared/http_client.py`'s `extra_headers`
      (httpx `MockTransport`, no real network).
    - Full regression after this work: 103/103 unit tests (100 + 3 new), 37/37
      second-brain, 20/20 resume-intelligence, 69/69 RWS integration, 119/121 VoiceHR
      (2 pre-existing/unrelated).
  - **Live end-to-end smoke test verified**, real Postgres + real VoiceHR (no mocks on
    either side): logged in → submitted a real JD → selected the qualified match →
    invited (pre-flight contact lookup hit a real `resume_intel.candidates` row, then a
    real `POST /api/invite` call to the live VoiceHR container) → confirmed the
    interview row in VoiceHR's actual SQLite DB had the correct `external_reference_id`
    and `idempotency_key = "{application_id}:1"` → re-invited, confirmed
    `invite_attempt_number` became 2 and a second interview row was created with
    `idempotency_key` ending `:2` → hand-crafted a correctly HMAC-signed webhook payload
    and POSTed it to `/v1/webhooks/voicehr`, confirmed the application transitioned to
    `interviewed` with the assessment persisted → accepted the application, confirmed
    `interview_status`/`interview_assessment` were still present on the final
    `accepted` response → separately confirmed: replaying the same delivery returns
    `duplicate_ignored`; a wrong or missing `X-Signature` returns 401; a syntactically
    valid but nonexistent `external_reference_id` returns `no_op` (this is where the FK
    bug above was caught); inviting a candidate with no Resume Intelligence contact
    record returns 422 and VoiceHR's interviews table gets zero new rows for it.
- Nothing is BLOCKED.

## Known Limitations (discovered this session)

- No existing Resume Intelligence or VoiceHR source code is available in this environment —
  only README-level documentation (`ResumeParser.md`, `VoiceHR.md`). User has confirmed both
  will be placed into `services/resume-intelligence/` and `services/voicehr/` respectively.
  Per Rule 1, their actual code must be inspected once present, before Step 7 (extend Resume
  Intelligence) and before any VoiceHR API additions. This does not block earlier steps.
- GEMINI_API_KEY has not been provided. Live `GeminiProvider` smoke test remains pending.
- Host-side psycopg cannot reach Docker Postgres via `localhost:5432`: Docker Desktop's WSL2
  network proxy intercepts port 5432 before pg_hba.conf applies, causing SCRAM-SHA-256 auth
  failure regardless of pg_hba.conf settings. Integration tests must be run inside the container
  via `docker exec csb-resume-intelligence python -m pytest /app/test_resume_intelligence.py -v`.
  Workaround for migrations: `docker exec csb-resume-intelligence alembic upgrade head`.
- `gemini-2.5-flash` returned `404 NOT_FOUND` from the live Gemini API this session ("no longer
  available to new users... use models/gemini-3.6-flash"). Updated `GeminiProvider.DEFAULT_MODEL`
  in `shared/llm.py` and `GEMINI_MODEL` in `services/recruitment-workflow/.env(.example)` to
  `gemini-3.6-flash`; the corresponding unit test assertion was updated too. `gemini-embedding-001`
  (used by Second Brain) was unaffected. `services/resume-intelligence/.env.example` and VoiceHR
  still reference `gemini-2.5-flash` — out of scope for this task (resume-intelligence's pipeline
  does not currently call `generate_text`/`generate_structured`; VoiceHR internals are untouched
  per the bootstrap rule) but worth checking before any future step that does call Gemini text
  generation from either service.
- No lint or type-check tooling (ruff/mypy/flake8/pylint) is configured anywhere in this repo —
  none was set up in prior sessions either. This session verified all new/changed files with
  `python -m py_compile` (syntax only) since no linter/type checker was available to run.
- **Resolved this session**: RWS's `POST /v1/jobs` and `GET /v1/jobs/{job_id}/matches` now
  require recruiter JWT (`require_recruiter`), not `x-api-key`. See the recruiter
  authentication entry above.
- Pydantic's `EmailStr` rejects the `.test` TLD as a "special-use or reserved name" (RFC 2606) —
  the first seed-recruiter email chosen (`recruiter@company.test`) failed validation on every
  login attempt with a 422 before this was caught by a live smoke test. Fixed by using
  `recruiter@example.com` instead (a `.com` domain is not on Pydantic's reserved-TLD list, even
  though `example.com` itself is also RFC-2606-reserved for documentation — Pydantic's `EmailStr`
  only checks syntax/reserved-TLD, it does not do DNS deliverability checks, so this passes).
  Worth remembering for any future seeded/placeholder email addresses in this project: avoid
  `.test`, `.example`, `.invalid`, `.localhost` as TLDs.
- The migration-seeded recruiter's plaintext password (`SEED_RECRUITER_PASSWORD` in `.env`) must
  match exactly what integration tests use to log in (`SEED_RECRUITER_EMAIL`/`_PASSWORD` constants
  in `tests/integration/conftest_recruitment_workflow.py`). If `.env`'s seed password is ever
  changed, migration `0003` must be re-run against a fresh/downgraded database for the new hash
  to take effect (`alembic downgrade 0002 && alembic upgrade head` — this drops and re-seeds
  `recruiters`/`applications`/`recruiter_decisions`), and the test constants must be updated to
  match, or login-dependent tests will fail with 401.
- `services/second-brain`'s integration test suite truncates `second_brain.candidate_knowledge`
  on every run (session-scoped autouse fixture, added last session to prevent smoke-test row
  leakage between test runs). Running those tests wipes any live-smoke-test candidates ingested
  earlier in the same session — discovered when a live RWS smoke test's candidates disappeared
  after re-running Second Brain's test suite for regression-checking. Not a bug, just a reminder:
  re-ingest live smoke-test candidates into Second Brain *after* running its test suite, not
  before, if a subsequent RWS live smoke test needs them.
- Second Brain's `RetrieveCandidatesResponse` was deliberately left unchanged (still only
  `candidate_id` + `similarity_score`); a new `GET /v1/knowledge/candidates/{candidate_id}`
  endpoint was added instead so RWS fetches candidate attributes in a second call. This means
  scoring a large retrieved set costs N+1 HTTP calls to Second Brain. Acceptable for this
  project's scale (demo, small candidate pool) — flagged here in case a future session considers
  batching or embedding the attributes directly in the retrieval response instead.
- `services/voicehr/README.md` still says "Status: skeleton only. Not yet implemented" and
  references a planned `POST /v1/interviews` endpoint name — both are stale. The real source was
  already present this session (invite/webhook routes, SQLite persistence, evaluator, all
  working), and the actual integration point is the existing `POST /api/invite`, extended in
  place rather than replaced. Worth updating that README in a future session; not done now to
  keep this session's diff focused on the integration itself.
- A foreign key from an append-only receipt/log table to the entity it references is the wrong
  default in this project whenever the log's job includes recording *unresolvable* references
  (see the `interview_webhook_events.application_id` bug above, caught only by live-testing the
  "unknown application_id" case — no unit or mocked-integration test would have caught it, since
  mocks don't enforce real FK constraints). Worth remembering if a similar audit-log table is
  added later (e.g. for a future reconciliation mechanism, if one is ever built).
- VoiceHR's outbound webhook delivery is genuinely best-effort/single-attempt (per this task's
  explicit "no reconciliation polling, no message brokers" constraint): if RWS is down or
  unreachable at the moment `process_interview()` runs, that interview's result is never
  retried and `webhook_delivered_at` stays `NULL` forever (verified this stores correctly and
  doesn't crash anything, but nothing currently surfaces "this interview finished but RWS never
  found out" for a human to notice). Acceptable for this project's scope; would need a
  reconciliation step to close if ever revisited, which is explicitly deferred.
- The `Idempotency-Key` header value is never itself validated for shape (RWS always sends
  `"{application_id}:{attempt_number}"`, but VoiceHR's `/api/invite` accepts any string). Not a
  problem for this integration (VoiceHR is only ever called by RWS in this architecture), but
  worth knowing if `/api/invite` is ever exposed to other callers.

## Exact Next Steps

1. **[NEXT]** LLM-generated match explanation (advisory only, generated *after*
   `final_score`/`qualified` are persisted — per the architectural rule that the LLM never
   determines the score itself). Not yet started; natural next point to surface it is the
   recruiter-facing match/application view (`GET /v1/jobs/{job_id}/matches` or
   `/applications`), as an additional field computed on read rather than stored, or stored once
   and cached — worth a deliberate choice rather than defaulting to one.
2. Knowledge feedback loop (per the end-to-end story: recruiter decision → knowledge feedback)
   — not yet designed. Now that real recruiter decisions and VoiceHR assessments both exist and
   are persisted, this is unblocked; still needs its own design pass (what exactly feeds back
   into Second Brain, and how "PII-free" and "no shadow candidate database" constraints apply).
3. `services/voicehr/README.md` cleanup (see Known Limitations above) — low priority, doesn't
   block anything.
