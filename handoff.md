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
- `docker-compose.yml`: **NOT STARTED**
- Postgres init script (3 schemas, 3 least-privilege roles): **NOT STARTED**
- Alembic setup per schema: **NOT STARTED**
- FastAPI skeletons (`GET /health` x4): **NOT STARTED**
- Shared Pydantic schemas (`extra="forbid"`): **NOT STARTED**
- Gemini API key wiring: **NOT STARTED**
- `LLMProvider` interface: **NOT STARTED**
- Resume Intelligence candidates table + dedup + contact endpoint: **NOT STARTED**
- Second Brain knowledge upsert + pgvector embedding: **NOT STARTED**
- Nothing is BLOCKED.

## Known Limitations (discovered this session)

- No existing Resume Intelligence or VoiceHR source code is available in this environment —
  only README-level documentation (`ResumeParser.md`, `VoiceHR.md`). Per Rule 1, their actual
  code must be inspected before Step 9 (extend Resume Intelligence) and any VoiceHR API
  additions. **Action needed: the actual source repositories must be provided before those
  steps are reached.** This does not block the current step.

## Exact Next Steps

1. **[NEXT]** Write `docker-compose.yml`: Postgres (pgvector image) + 4 service containers,
   with `.env.example` per service.
2. Write the Postgres init script creating three schemas (`resume_intel`, `second_brain`,
   `recruitment`) and three least-privilege roles, no cross-schema grants.
3. Set up Alembic per schema; write the first empty migration for each.
4. Write a skeleton FastAPI app with `GET /health` for each of the 4 services; verify
   `docker compose up --build` boots all four and Postgres.
5. Define shared Pydantic schemas in `/shared/schemas`: CandidateSummary,
   ContactLookupResponse, RetrieveCandidatesRequest/Response, InvitationRequest/Response,
   WebhookPayload — all `extra="forbid"`.
6. Obtain one Google AI Studio API key; wire it into each relevant service's `.env.example`.
7. Implement the `LLMProvider` interface (Gemini default implementation).
8. Resume Intelligence: candidates table, normalized-email dedup, `GET
   /v1/candidates/:id/contact` (service-key auth). **Requires actual ResumeParser source —
   not yet available in this environment.**
9. Second Brain: `POST /v1/knowledge/candidates` (upsert, embed via gemini-embedding-001 into
   a pgvector column with an HNSW index).
