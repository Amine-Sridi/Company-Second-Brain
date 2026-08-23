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
  against the live Docker stack. VoiceHR Dockerfile created and syntactically correct, but
  `docker compose up --build voicehr` causes Docker daemon to crash (OOM / disk pressure —
  only 18.5 GB free; pipecat-ai pulls ~4–5 GB of PyTorch layers). VoiceHR container is NOT
  yet verified running. All other verification targets met.
- Shared Pydantic schemas (`extra="forbid"`): **NOT STARTED**
- Gemini API key wiring: **NOT STARTED**
- `LLMProvider` interface: **DONE** — `shared/llm.py`: `LLMProviderError`, abstract `LLMProvider`
  (`generate_text`, `generate_structured`), `GeminiProvider` (Gemini 2.5 Flash default, mirrors
  `google-genai==2.18.0` pattern from VoiceHR). 18 unit tests pass (`tests/unit/test_llm.py`);
  `py_compile` clean. **Live Gemini API not verified** — no `GEMINI_API_KEY` in the authoring
  environment. `google-genai` is not yet in the three new services' `requirements.txt` — will be
  added during the FastAPI skeleton step.
- Resume Intelligence candidates table + dedup + contact endpoint: **NOT STARTED**
- Second Brain knowledge upsert + pgvector embedding: **NOT STARTED**
- Nothing is BLOCKED.

## Known Limitations (discovered this session)

- No existing Resume Intelligence or VoiceHR source code is available in this environment —
  only README-level documentation (`ResumeParser.md`, `VoiceHR.md`). User has confirmed both
  will be placed into `services/resume-intelligence/` and `services/voicehr/` respectively.
  Per Rule 1, their actual code must be inspected once present, before Step 7 (extend Resume
  Intelligence) and before any VoiceHR API additions. This does not block earlier steps.
- VoiceHR Docker build (`docker compose up --build voicehr`) crashes Docker Desktop due to
  disk pressure: only 18.5 GB free, pipecat-ai[whisper,kokoro,ollama,elevenlabs] requires
  ~4–5 GB of image layers (PyTorch, ctranslate2, etc.). Must free at least 10 GB before
  attempting. VoiceHR Dockerfile and the `GET /health` route are correct — this is purely
  a host resource constraint.
- GEMINI_API_KEY has not been provided. Live `GeminiProvider` smoke test remains pending.

## Exact Next Steps

1. **[NEXT]** Define shared Pydantic schemas in `/shared/schemas`: `CandidateSummary`,
   `ContactLookupResponse`, `RetrieveCandidatesRequest/Response`, `InvitationRequest/Response`,
   `WebhookPayload` — all `extra="forbid"`. These are the cross-service contracts needed before
   implementing any real endpoints.
2. Resume Intelligence: candidates table, normalized-email dedup, `GET
   /v1/candidates/{candidate_id}/contact` (service-key auth). **Requires actual ResumeParser
   source — drop it into `services/resume-intelligence/` before this step.**
3. Second Brain: `POST /v1/knowledge/candidates` (upsert, embed via gemini-embedding-001 into
   a pgvector column with an HNSW index).
4. **Deferred — free at least 10 GB disk before attempting:** `docker compose up --build voicehr`
   to verify VoiceHR's container. The Dockerfile and `/health` addition are ready; the build
   crashes Docker Desktop due to insufficient free disk (~18.5 GB free, pipecat-ai needs ~4–5 GB).
   Run a live `GeminiProvider` smoke test once a `GEMINI_API_KEY` is available (neither has
   been provided yet).
