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

## Exact Next Steps

1. **[NEXT]** Recruitment Workflow Service: JD structuring endpoint (`POST /v1/jobs`), hybrid
   matching engine (deterministic + semantic scoring), `MatchResult` persistence (immutable,
   with candidate_snapshot and scoring config version), recruiter decision endpoints
   (`POST /v1/matches/{id}/accept`, `/reject`). JWT auth for recruiter endpoints; service-key
   auth for internal RWS→Second Brain calls.
2. VoiceHR integration: `external_reference_id` column on VoiceHR's session table, invitation
   endpoint (`POST /v1/interviews/invite`), outbound HMAC-SHA256 signed webhook to RWS on
   interview completion.
3. **Deferred:** VoiceHR integration — `external_reference_id` on session table, invitation
   endpoint, outbound HMAC-SHA256 signed webhook from VoiceHR to RWS. Inspect
   `services/voicehr/` source before starting.
