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
- RWS's `POST /v1/jobs` uses `x-api-key` service-key auth, not JWT. The demo narrative describes
  this as a recruiter action, so JWT auth (one seeded account, per CLAUDE.md) should be added
  when the recruiter-decision-endpoints step is implemented — at that point it likely makes sense
  to move JD submission behind JWT too, rather than leaving it on a different auth scheme than
  `/v1/matches/{id}/accept`.
- Second Brain's `RetrieveCandidatesResponse` was deliberately left unchanged (still only
  `candidate_id` + `similarity_score`); a new `GET /v1/knowledge/candidates/{candidate_id}`
  endpoint was added instead so RWS fetches candidate attributes in a second call. This means
  scoring a large retrieved set costs N+1 HTTP calls to Second Brain. Acceptable for this
  project's scale (demo, small candidate pool) — flagged here in case a future session considers
  batching or embedding the attributes directly in the retrieval response instead.

## Exact Next Steps

1. **[NEXT]** Recruiter decision endpoints + JWT auth: seed one recruiter account, JWT login,
   `POST /v1/matches/{id}/accept` / `/reject`, and workflow state on `MatchResult`/`Application`.
   Consider moving `POST /v1/jobs` behind JWT at the same time (see Known Limitations above).
2. VoiceHR integration: `external_reference_id` column on VoiceHR's session table, invitation
   endpoint (`POST /v1/interviews/invite`), outbound HMAC-SHA256 signed webhook to RWS on
   interview completion. Inspect `services/voicehr/` source before starting (internals must not
   be rewritten, only its API surface extended).
3. LLM-generated match explanation (advisory only, generated *after* `final_score`/`qualified`
   are persisted — per the architectural rule that the LLM never determines the score itself).
   Not yet started; not required until the recruiter-facing match view needs it.
