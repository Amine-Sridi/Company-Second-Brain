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
- Knowledge feedback loop (RWS → Second Brain recruiter-decision write-back): **DONE**.
  Completes the end-to-end story's last unimplemented arrow ("recruiter decision →
  knowledge feedback"). Deliberately scoped to just the write-back itself — no read
  path yet uses `candidate_feedback` (e.g. no scoring/ranking adjustment based on past
  outcomes); that would be a separate, later design decision.
  - **Second Brain side**: migration `0003` adds `second_brain.candidate_feedback`
    (`candidate_id`, `application_id UNIQUE`, `decision CHECK IN ('accepted','rejected')`,
    `job_title`, `matched_requirements`/`missing_requirements JSONB`, `final_score`,
    `decided_at`, timestamps). Both `candidate_id` and `application_id` are **plain UUID
    columns, not foreign keys** — deliberately, applying the lesson learned from last
    session's `interview_webhook_events` bug: `application_id` belongs to RWS's
    `recruitment` schema so a real FK is impossible anyway (no cross-schema grants), and
    even a same-schema FK on `candidate_id` would wrongly reject feedback for a candidate
    whose `candidate_knowledge` row no longer exists. New shared schema
    `CandidateFeedbackRequest` (`shared/schemas/__init__.py`, `extra="forbid"`, no PII
    fields — only `application_id`, `decision`, `job_title`,
    `matched_requirements`/`missing_requirements`, `final_score`, `decided_at`).
    `db.py`: `CandidateFeedback` ORM model, `upsert_candidate_feedback()` via
    `ON CONFLICT (application_id) DO UPDATE` (idempotent: a repeated delivery for the
    same application overwrites the same row rather than duplicating it),
    `get_candidate_feedback_by_application()`. `main.py`:
    `POST /v1/knowledge/candidates/{candidate_id}/feedback`, service-key auth
    (`candidate_id` is a path parameter, matching the existing `GET
    /v1/knowledge/candidates/{candidate_id}` convention rather than repeating it in the
    body). 15 new integration tests (up from 34 to 50 total in `csb-second-brain`):
    persistence, idempotent upsert (same `application_id` twice → one row, latest
    values win), PII rejection (email/name → 422), invalid decision/score/candidate_id,
    missing `application_id`, auth, and accepting feedback for a `candidate_id` with no
    `candidate_knowledge` row at all.
  - **RWS side**: `clients.py` gained `send_candidate_feedback()` (POSTs to the new
    Second Brain endpoint; raises `SecondBrainClientError` on any transport/non-200
    failure). `main.py`'s `_decide()` (shared by `accept_application`/
    `reject_application`) now takes a `BackgroundTasks` parameter: after
    `transition_application()` succeeds — i.e. after the state machine transition and
    `recruiter_decisions` audit row are already durably committed — it fetches the
    `MatchResult` (for `matched_requirements`/`missing_requirements`/`final_score`) and
    `Job` (for `title`) already tied to the application, and schedules
    `_send_candidate_feedback_background()` via `background_tasks.add_task(...)`. That
    function is `async`, takes only plain primitives (no DB session — the request-scoped
    session is closed before FastAPI's `BackgroundTasks` run, the same constraint
    already documented for VoiceHR's webhook notifier last session), and swallows
    `SecondBrainClientError` into a logged warning: a failed write-back must never
    surface to the recruiter, since by the time it runs the decision is already final
    and persisted. 5 new integration tests (up from 69 to 74 total in
    `csb-recruitment-workflow`): accept and reject both trigger a correctly-shaped
    write-back call, the write-back's `final_score` matches the actual persisted
    `MatchResult` (not a recomputed value), accept still returns 200 when the write-back
    fails (mocked `SecondBrainClientError`), and a second (409, already-decided) accept
    attempt does not trigger a second write-back call.
  - Preserved untouched, as instructed: the `Application` state machine's transitions
    and their atomic conditional-UPDATE guards (`transition_application` itself was not
    modified — only wrapped with an additional step *after* it succeeds), and
    `MatchResult`'s immutability (the write-back only *reads* an existing `MatchResult`
    via `get_match_result`, never writes to it).
  - Full regression after this work: 111/111 unit tests (103 + 8 new
    `CandidateFeedbackRequest` schema tests), 50/50 second-brain integration tests,
    20/20 resume-intelligence, 74/74 RWS integration tests, 119/121 VoiceHR (the same 2
    pre-existing/unrelated failures from last session, untouched — VoiceHR was not
    touched at all this session, consistent with the task's "do not modify working
    VoiceHR internals unnecessarily" instruction).
  - **Live end-to-end smoke test verified** against real Postgres (no mocks): called the
    feedback endpoint directly first (200, persisted, PII rejected with 422, a repeat
    delivery with a different `decision` value overwrote the same row rather than
    creating a second one — confirmed via `psql`, exactly one row). Then the full
    real flow: ingested a candidate into Second Brain → submitted a real JD → selected
    the qualified match → called `POST /v1/applications/{id}/accept` → confirmed via
    `docker logs` that the HTTP response to the client (`200 OK` on `/accept`) was
    logged **before** the outbound `POST .../feedback` call to Second Brain, confirming
    the write-back genuinely runs after the response, not before it → confirmed the
    resulting row in `second_brain.candidate_feedback` had the correct `candidate_id`,
    `application_id`, `decision: accepted`, `job_title`, `matched_requirements`, and the
    same `final_score` as the persisted `MatchResult`.
- Observability/hardening — structured JSON logging, correlation IDs, external-call
  latency/failure logging, sensitive-data exclusion: **DONE**. Scoped to the three
  services built this project (resume-intelligence, second-brain,
  recruitment-workflow); VoiceHR's internals were **not** touched, per this task's own
  "keep the existing architecture unchanged" instruction and the standing rule not to
  modify VoiceHR unnecessarily — see Known Limitations for what that means in practice.
  - New `shared/observability.py`: `JsonFormatter` (one JSON object per log line —
    timestamp, level, service, logger, message, `correlation_id` when set, plus any
    `extra=` fields — reads a record's actual attribute dict rather than a
    hand-maintained field list, so it can't silently drop a field it doesn't know
    about); a `contextvars`-based correlation ID (`get_correlation_id`/
    `set_correlation_id`/`new_correlation_id`); `configure_json_logging(service_name)`
    (replaces the root logger's handlers — idempotent, safe to call more than once);
    `CorrelationIdMiddleware` (Starlette `BaseHTTPMiddleware` — reads `X-Correlation-ID`
    from the incoming request or generates one, stores it in the contextvar for the rest
    of the request *and any FastAPI BackgroundTasks scheduled during it* since those run
    later in the same asyncio task rather than a new one, echoes it in the response
    header); `log_external_call()` (one structured line per outbound call — target,
    operation, `duration_ms`, `outcome`, `status_code` or `error_type` — never a header,
    body, or prompt); `Timer` (a `duration_ms` **property**, deliberately not a value
    frozen at `__exit__`, so it can be read from inside an `except` clause that logs and
    re-raises before the `with` block has exited — the first version of this class had
    exactly that bug, caught before it shipped by re-reading my own code, not by a test).
  - `shared/http_client.call_service()`: now attaches `X-Correlation-ID` (from the
    contextvar, omitted entirely if unset) to every outbound call, and logs
    method/target-host/path/status/`duration_ms`/outcome via `log_external_call` —
    never the request/response body, and the query string is stripped before logging
    the path (defense in depth, though none of these internal APIs currently put
    anything sensitive in a query string).
  - `shared/llm.py`: all three `GeminiProvider` methods (`generate_text`,
    `generate_structured`, `embed_text`) now log latency/outcome the same way — model
    name and operation, never the prompt or the response content (the module already
    had a comment to this effect for error messages; logging now upholds the same rule).
  - All three services' `main.py`/`api.py`: replaced their ad hoc `logging.basicConfig`
    calls with `configure_json_logging(<service-name>, settings.log_level)` and added
    `app.add_middleware(CorrelationIdMiddleware)`. Note: this only reformats
    **application** log lines (anything via `logging.getLogger(...)` that propagates to
    root, which is everything logged by our own code plus `httpx`'s own request-line
    logger). It does **not** reformat uvicorn's own access-log lines
    (`uvicorn.access`/`uvicorn.error` install their own handlers with
    `propagate=False` and were deliberately left alone — reconfiguring uvicorn's own
    logging is infrastructure this task didn't ask for and isn't needed for the
    stated goals).
  - RWS's webhook receiver (`POST /v1/webhooks/voicehr`) now logs one `webhook_received`
    line per delivery with `delivery_id`, `interview_status`, the outcome
    (`processed`/`no_op`/`duplicate_ignored`/`unknown_application`), and
    `application_id` when resolved — deliberately never `payload.assessment` (CEFR
    level, scores), which is treated as interview content.
  - Two pre-existing PII-risk log lines found and fixed (not new code, but directly
    within this task's "never log PII" mandate, and both one-line, zero-behavior-change
    fixes): `services/resume-intelligence/api.py`'s ingest-failure handler used to log
    `str(exc)` (a pipeline exception could embed extracted name/email/phone in its
    message) — now logs only `type(exc).__name__`. `pipeline/parsing_agent.py` had a
    `DEBUG`-level line logging the first 500 characters of the raw LLM response — which
    *is* the parsed candidate's name/email/phone, the exact thing being extracted — now
    logs only its length. Both are part of the pre-existing dropped-in ResumeParser
    pipeline; nothing else in that pipeline's ~30 other log lines was touched (company
    names for ESN classification, filenames, generic error messages — none of those are
    candidate PII per CLAUDE.md's definition, and broadly editing pre-existing pipeline
    files beyond a concrete, confirmed risk was judged out of scope). The standalone
    `pipeline`-CLI entry point (`services/resume-intelligence/main.py` — a batch tool,
    not the running FastAPI service) was left untouched for the same reason.
  - `services/resume-intelligence/requirements.txt` was missing `pytest` entirely
    (discovered when this session's container rebuild lost whatever manual
    `pip install` a previous session must have relied on — 20 previously-passing tests
    suddenly couldn't even collect). Added `pytest`, matching the other two services;
    unrelated to observability but blocked running "the relevant regression tests" as
    instructed, so fixed here.
  - Tests: 15 new unit tests for `shared/observability.py`
    (`tests/unit/test_observability.py` — JSON formatter validity/correlation-id
    inclusion/extra-field passthrough/exception formatting, correlation ID
    generate/get/set, the middleware's generate/echo/uniqueness behavior via a throwaway
    FastAPI app, `Timer`, `log_external_call` success/failure shape); 5 new unit tests
    in `test_http_client.py` (correlation ID propagated/omitted, latency logged on
    success, `error_type` logged on failure, the API key value never appears anywhere in
    captured log output); 3 new unit tests in `test_llm.py` (one per Gemini method —
    latency logged, and a distinctive marker string standing in for real PII in the
    prompt/text never appears in captured logs). 19 new integration tests across the
    three services (5 in resume-intelligence, 4 in second-brain, 10 in
    recruitment-workflow — correlation ID generated/echoed/differs-per-request in all
    three; PII/secret exclusion checks: contact-lookup response PII, service API keys,
    ingested raw_text, recruiter password, issued JWT access token, and — the one
    closest to "interview content" — a distinctive CEFR-level marker sent through the
    VoiceHR webhook, confirmed absent from RWS's own `webhook_received` log line). Full
    regression: 134/134 unit tests (111 + 23 new), 25/25 resume-intelligence, 54/54
    second-brain, 81/81 recruitment-workflow, 119/121 VoiceHR (same 2 pre-existing,
    unrelated failures as every prior session — VoiceHR was not touched at all this
    session).
  - **Live end-to-end smoke test verified**, real Postgres + real Gemini + all three
    services (no mocks): submitted a JD to RWS with an explicit
    `X-Correlation-ID: smoketest-correlation-id-4242` header — confirmed it was echoed
    on the response, and then confirmed via `docker logs` on **both** containers that
    the identical `correlation_id` appeared on RWS's own `generate_structured`/
    `retrieve_candidates`/`get_candidate` log lines *and* on Second Brain's own
    `embed_text` log line for the request that RWS's call triggered — i.e. the ID
    genuinely crossed the process boundary and was picked up by the receiving
    service's own middleware, not just echoed by the sender. Repeated this for the
    invite flow with a second distinct ID (`smoketest-invite-corr-9999`): confirmed it
    tagged RWS's outbound calls to **both** Resume Intelligence's contact lookup and
    VoiceHR's `/api/invite`, covering all three of RWS's downstream integrations.
    Every logged `external_call` line showed `duration_ms` and `outcome`/`status_code`,
    and manual inspection of every line emitted during this run (including `httpx`'s
    own request-line logger, which is outside this session's code but shares the same
    JSON handler) confirmed no API key, password, JWT token, or resume/JD/interview
    content appeared anywhere — including that the Gemini SDK's own request logging
    doesn't put the API key in the URL (it's sent as a header), so nothing there leaks
    either.
- LLM-generated match explanation (advisory, generated after `final_score`/`qualified`
  are already persisted): **DONE**. Closes the `[NEXT]` item from the previous session.
  Scoped exactly as specced: generated lazily on read (`GET /v1/jobs/{job_id}/matches`),
  generated and cached once per `MatchResult`, and never affects scoring/qualification.
  - New migration `0005`: `recruitment.match_explanations` (`match_result_id UUID
    UNIQUE REFERENCES match_results(id)`, `explanation TEXT`, `model TEXT`,
    `prompt_version TEXT`, `created_at`). Unlike `interview_webhook_events`, a real
    foreign key is correct here — every row is created by this service itself for a
    `match_result_id` it already knows exists, never from an externally-supplied
    reference. Ran clean against the live Postgres container.
  - `db.py`: `MatchExplanation` ORM model, `get_match_explanation()`,
    `create_match_explanation()` (raises `IntegrityError` on a concurrent duplicate —
    same pattern as every other uniqueness constraint in this codebase, caller catches
    and re-fetches rather than treating it as an error).
  - New `services/recruitment-workflow/explanation.py`: `build_prompt(match)` builds
    the LLM prompt strictly from fields already on the immutable `MatchResult` —
    `candidate_snapshot` (only `skills`/`years_of_experience`/`job_titles`, read via
    whitelisted `.get()` calls so even a hypothetical stray key in the snapshot could
    never reach the prompt), `matched_requirements`/`missing_requirements`, all five
    score components, `threshold_snapshot`, `hard_gate_passed`, `qualified` — plus an
    explicit instruction not to include or guess any personal identifying information.
    `get_or_create_explanation(db, llm, match, model)`: returns the cached explanation
    if one exists (no LLM call); otherwise calls `llm.generate_text` once, catches
    `LLMProviderError` and returns `None` on failure (logged, never raised further —
    the caller's response is unaffected either way), and persists the result via
    `create_match_explanation`, re-fetching on a concurrent-insert race instead of
    storing a second row for the same match. This module never imports or calls
    anything from `scoring.py`, never writes to `MatchResult`, and only ever *reads*
    `final_score`/`qualified` as prompt input — the architectural rule that the LLM
    never determines the score is enforced by this module structurally having no path
    back into scoring, not by a comment.
  - `main.py`: new `get_llm_provider_optional()` dependency — same lazy singleton as
    the existing `get_llm_provider()`, but returns `None` instead of raising 503 when
    Gemini can't be constructed (missing/invalid key). Declared as its own top-level
    function (not a plain wrapper call to `get_llm_provider`) specifically so it's
    independently overridable via `app.dependency_overrides` in tests and so a raising
    sub-dependency can't abort the whole request — FastAPI has no way to catch an
    exception raised while resolving a nested `Depends()`, so this had to be a sibling
    dependency, not a wrapper. `GET /v1/jobs/{job_id}/matches` (now `async`) takes this
    as a dependency and, per match, calls `get_or_create_explanation` only when a
    provider is available; when it isn't, it falls back to reading any already-cached
    explanation directly (`get_match_explanation`) so a missing key never regresses a
    previously-generated explanation back to null. The endpoint never 503s because of
    this — only `explanation` comes back `null` in that case, exactly as specced.
    `POST /v1/jobs` (JD submission) deliberately does **not** trigger generation: it
    already makes one synchronous Gemini call (JD structuring) plus N Second Brain
    calls per candidate; adding N more synchronous Gemini calls there would measurably
    slow down JD submission for no benefit, since nothing reads the explanation until
    the recruiter views the match list. `MatchResultResponse` gained
    `explanation: Optional[str] = None` (in `schemas.py`) — `null` at submission time,
    populated on first read.
  - Tests: 4 new unit tests in `tests/unit/test_explanation.py` (score/requirement
    content present in the prompt; a hypothetical stray `email`/`name` key in
    `candidate_snapshot` proven absent from the built prompt via whitelisting, not just
    absent in practice; the model-facing "no PII" instruction is present). 8 new
    integration tests in `tests/integration/test_recruitment_workflow.py`: explanation
    present and matches the mock LLM's fixed output; **not** generated at JD-submission
    time (`generate_text_calls == []` right after `POST /v1/jobs`); generated once and
    reused across two `GET .../matches` calls (`len(generate_text_calls) == 1` after
    two reads — the single most direct proof of the "generate once, reuse" invariant);
    the row actually lands in `match_explanations` with a non-empty
    explanation/model/prompt_version; a disqualified candidate (missing required skill)
    still comes back with `qualified: false`/`hard_gate_passed: false` exactly as
    scored even when the explanation LLM call is made to fail (new
    `_FailingExplanationLLMProvider` / `client_failing_explanation` fixture —
    `generate_structured` succeeds normally, only `generate_text` raises) — proving a
    generation failure never touches scoring; the prompt text sent to the mock LLM
    contains none of `@`/`email`/`phone`/`candidate_name`/`full_name`; an unavailable
    provider (`get_llm_provider_optional` overridden to return `None`) still returns
    200 with valid scores and `explanation: null`. `conftest_recruitment_workflow.py`'s
    `_MockLLMProvider.generate_text` (previously `raise NotImplementedError`, unused
    until now) now returns a fixed string and records every call in
    `.generate_text_calls`; the `client` fixture now shares one `_MockLLMProvider`
    instance (via a new `mock_llm` fixture) across a test's requests instead of a fresh
    instance per dependency resolution, specifically so call-count assertions are
    meaningful. Full regression after this work: 138/138 unit tests (134 + 4 new),
    25/25 resume-intelligence, 54/54 second-brain, 88/88 recruitment-workflow (81 + 7
    new), 119/121 VoiceHR (same 2 pre-existing/unrelated failures — VoiceHR untouched
    this session).
  - **Live end-to-end smoke test verified**, real Postgres + real Gemini (no mocks):
    ingested a candidate into Second Brain, submitted a matching JD to RWS (submission
    response's `explanation` correctly `null`), called `GET .../matches` twice. First
    call returned a real, coherent Gemini-written explanation
    ("...matched all required skills — Python, FastAPI, and PostgreSQL — with no
    missing requirements... final score of 0.927... qualified because... exceeds the
    qualification threshold of 0.300"); second call returned the byte-identical string.
    Confirmed via `docker logs` that only **one** `generate_text:gemini-3.6-flash`
    `external_call` line was ever logged despite the two reads. Confirmed via a direct
    DB query that `match_explanations` holds exactly one row for that match, with
    `model = gemini-3.6-flash`, `prompt_version = v1`, and a non-empty explanation.
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
- VoiceHR was **not** touched by this session's observability/hardening work — its own
  pre-existing `logging` usage (plain unformatted lines across `evaluator.py`, `mailer.py`,
  `agent.py`, etc.) is unchanged, it has no correlation ID middleware, and its logs are not JSON.
  This was a deliberate scope decision (this task said "keep the existing architecture
  unchanged" and "do not add new infrastructure," and prior sessions established a standing
  rule against modifying VoiceHR internals unnecessarily), not an oversight. Two of its files
  that this project's own code added last session — `backend/services/rws_client.py` and the
  couple of `logger.warning`/`logger.exception` calls around it in `webhook.py` — were also left
  as plain-text logging for consistency with the rest of VoiceHR's untouched logging, rather
  than making just those two files inconsistently JSON. If VoiceHR's logging is ever brought in
  line with the other three services, `shared/observability.py` cannot be imported there as-is
  (VoiceHR's Dockerfile builds from its own directory and never copies in `shared/` — confirmed
  in the VoiceHR-integration session) — it would need its own local copy or a genuinely shared
  package published differently.
- `configure_json_logging()` reformats application log lines (anything logged via
  `logging.getLogger(...)` that isn't `propagate=False`) but does **not** touch uvicorn's own
  `uvicorn.access`/`uvicorn.error` loggers, which install their own handlers with
  `propagate=False` by default and so keep printing their normal plain-text access-log lines
  (`INFO:     "GET /health HTTP/1.1" 200 OK`) alongside the JSON lines. This was a deliberate
  choice, not a partial implementation — reconfiguring uvicorn's own logging pipeline is
  infrastructure this task didn't ask for. If fully uniform JSON access logs are ever wanted,
  that's a separate, explicit follow-up (pass a custom `log_config` to uvicorn), not something
  to default into.
- No log rotation, shipping, or aggregation was added (no ELK/Loki/CloudWatch/etc.) — logs go to
  stdout as JSON lines only, per this task's explicit "do not add new infrastructure" instruction
  and CLAUDE.md's existing ban on ELK-style stacks. Whatever currently collects each container's
  stdout (`docker logs`, or nothing, depending on deployment) is the only place these logs live.

## Exact Next Steps

1. **[NEXT]** `services/voicehr/README.md` cleanup (see Known Limitations above) — low
   priority, doesn't block anything.
2. Nothing currently reads `second_brain.candidate_feedback` — it's a write-back sink only.
   Whether/how it should ever influence retrieval or scoring (e.g. down-weighting a candidate
   repeatedly rejected for a given skill profile) is an open design question, deliberately not
   answered this session — flagged here rather than in Known Limitations because it's a genuine
   future design choice, not a defect.
3. `GET /v1/jobs/{job_id}/applications` and `POST /v1/jobs` (JD submission response) don't
   surface `explanation` for anything beyond what's already cached at the time of the call —
   `explanation` is `null` in the JD-submission response and only gets populated the first time
   `GET /v1/jobs/{job_id}/matches` is called for that job. This was a deliberate scope/latency
   choice (see the Observability/hardening-adjacent entry above), not an oversight; if a future
   session wants `/applications` to also carry the explanation, `get_or_create_explanation` is
   already reusable there.
