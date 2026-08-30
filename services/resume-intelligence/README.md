# Resume Intelligence

## Responsibility in Company Second Brain

Resume Intelligence is the one place in the platform that ever touches candidate PII
(name, email, phone). It owns the full path from an uploaded resume file to:

1. a durable, PII-bearing candidate record in its own `resume_intel` Postgres schema, and
2. a PII-free `CandidateSummary` pushed to Second Brain, so the candidate becomes
   semantically searchable/matchable without any other service ever seeing their identity.

It also exposes one narrow, service-key-gated egress for PII: a contact lookup RWS calls
transiently, right before inviting a candidate to a VoiceHR interview, to get the
name/email it needs — RWS never persists that response.

This service does not extract score, rank, or make any recruitment decision about a
candidate. It extracts and normalizes information only.

## Pipeline

```text
upload (PDF/DOCX/JPEG/PNG)
  -> pipeline/text_extractor.py   deterministic text extraction, no LLM
  -> pipeline/parsing_agent.py    Gemini structured extraction + deterministic post-processing
  -> db.py                        persist candidate (PII), normalized-email dedup
  -> pipeline/candidate_summary.py  build a PII-free CandidateSummary (allow-list only)
  -> second_brain_client.py       push it to Second Brain (POST /v1/knowledge/candidates)
```

### 1. Text extraction (`pipeline/text_extractor.py`) — deterministic, no LLM

- `.pdf`: native extraction via `pdfplumber` when the first two pages have selectable
  text (≥ 50 characters); OCR fallback (`pdf2image` + `pytesseract`, `fra+eng`) only when
  they don't. Table and multi-column layouts are flagged, not silently mangled.
- `.docx`: `python-docx`, paragraphs and table cells.
- `.jpg` / `.jpeg` / `.png`: direct OCR via `pytesseract`.
- Confidence varies by path (1.0 native text, 0.80 scanned-PDF OCR, 0.75 image OCR) and is
  further capped at 0.70 if the extracted text is unusually short (`LOW_CHAR_COUNT`).
- **`.doc` (legacy pre-2007 binary Word format) is not supported.** It would require the
  `antiword` CLI tool, which this service's Docker image does not install; see "Known
  deliberate limitations" below.

### 2. Structured extraction (`pipeline/parsing_agent.py`) — Gemini, then deterministic

- One `shared.llm.LLMProvider.generate_structured` call per attempt, against
  `schemas.candidate_extraction.CandidateExtraction` (a Pydantic v2 model with
  `extra="forbid"` throughout) — Gemini is the sole AI provider for this project, used only
  through the project-wide `shared/llm.py` abstraction. Bounded retry: up to 3 attempts on
  any provider/validation failure, then a clean failure (see "Failure semantics").
- The resume text is wrapped in `<resume_text>` delimiters with an explicit instruction to
  treat it as untrusted, candidate-supplied data and ignore anything inside it that looks
  like an instruction — verified live against a resume containing an actual injection
  attempt, which was correctly ignored.
- The LLM extracts information only. It never computes experience duration, never dedups
  skills, and never decides confidence — all of that happens afterward, deterministically:
  - `normalize_candidate_data`: trims every string field, case-insensitive-dedups
    `skills`/`certifications`/each job's `tech_stack`/`spoken_languages` while preserving
    first-seen display casing (no taxonomy, no synonym resolution — "JS" and "Javascript"
    are never collapsed), and validates every `start_date`/`end_date` against `YYYY-MM`,
    clearing (not guessing) anything malformed.
  - `find_missing_fields`: flags missing critical fields (name/email/phone) and other
    incompleteness signals.
  - `calculate_total_experience` / `years_of_experience_from_work`: one shared deterministic
    month-summing calculation, presented as a human-readable French string and as a float
    respectively (used by `CandidateSummary.years_of_experience`).

### 3. Persistence and dedup (`db.py`)

- `resume_intel.candidates`: `id` (UUID PK), `email_normalized` (unique, nullable),
  `name`, `email`, `phone`, `raw_text`, `candidate_json` (JSONB — the full structured
  extraction), `created_at`, `updated_at`.
- Dedup rule: if a candidate with the same normalized email (`strip().lower()`) already
  exists, its id is returned unchanged (`is_new=false`) — the existing row is never
  overwritten. If the extraction has no email at all, a new row is always created (no
  identifier to dedup on, so unknown candidates are never silently merged).
- No delete or update path exists for a candidate anywhere in this codebase — rows are
  immutable once created.

### 4. CandidateSummary → Second Brain (`pipeline/candidate_summary.py`, `second_brain_client.py`)

- `build_candidate_summary(candidate_id, candidate_json)` is a **positive allow-list**, not
  "take everything except basics": it reads only `work[]`, `education[]`, `skills`,
  `certifications`, and `spoken_languages` off `candidate_json`. The string `"basics"` does
  not appear anywhere in the file, and PII exclusion is enforced twice over — once by this
  allow-list, once by `shared.schemas.CandidateSummary`'s own `extra="forbid"` (no
  name/email/phone/address field exists on that model at all).
- `raw_text` sent to Second Brain is a **freshly synthesized** string built from those
  allow-listed fields — never the original extracted resume text. `certifications` are
  folded into `skills` (the shared `CandidateSummary` contract has no separate field for
  them); `spoken_languages` are folded into the synthesized text.
- `years_of_experience` is the deterministic float described above — never asked of Gemini.
- `second_brain_client.send_candidate_summary` calls `POST /v1/knowledge/candidates` via
  `shared.http_client.call_service` (the same client every other cross-service call in this
  project uses) — correlation-ID propagation needs no extra code, since `call_service`
  already reads the request-scoped id this service's `CorrelationIdMiddleware` sets.
- Ordering: the Second Brain push happens strictly **after** the candidate is durably
  committed in `resume_intel.candidates`. If it fails, the already-stored candidate is
  **not** deleted or rolled back (there is no delete path to call even if one wanted to);
  `POST /v1/resumes` returns `502` with the candidate_id in the error detail, so retrying
  the same upload is safe — it dedups to the same Resume Intelligence row, and Second
  Brain's own upsert-by-`candidate_id` makes a repeated push idempotent.

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | none | liveness |
| `POST` | `/v1/resumes` | `X-Api-Key` service key | ingest a resume, returns `candidate_id` |
| `GET` | `/v1/candidates/{candidate_id}/contact` | `X-Api-Key` service key | PII contact lookup for RWS, transient use only |

Both non-health endpoints use the same service-key mechanism — there is no separate
auth system, and this service never validates a recruiter JWT. In this architecture,
admin/recruiter uploads always go through RWS first: `recruiter client --JWT--> RWS
--service key--> Resume Intelligence`. RWS's own `POST /v1/resumes` is JWT-protected
and forwards to this endpoint using RWS's service key, which is never exposed to the
recruiter's browser (see `services/recruitment-workflow/main.py`'s
`upload_candidate_resume`). The same service-key mechanism here is deliberately
suitable for a future trusted caller too (e.g. an email-ingestion workflow) — no new
auth surface would be needed for that.

### Failure semantics

`POST /v1/resumes`:

| Status | Cause |
|---|---|
| `201` | extraction, structuring, storage, and Second Brain ingestion all succeeded |
| `401` | missing or wrong `X-Api-Key` |
| `422` | unsupported file type (anything outside `.pdf`/`.docx`/`.jpg`/`.jpeg`/`.png`) |
| `502` | Gemini structured extraction failed after 3 bounded attempts — no candidate is stored |
| `502` | candidate was stored, but the Second Brain push failed — candidate_id is in the response detail; retrying the upload is safe |
| `500` | any other unexpected failure (extraction crash, DB error, etc.) |

`GET /v1/candidates/{candidate_id}/contact`:

| Status | Cause |
|---|---|
| `200` | candidate found, contact data returned |
| `401` | missing or wrong `X-Api-Key` |
| `404` | no candidate with that id |
| `422` | `candidate_id` is not a valid UUID (rejected by FastAPI's own path-parameter validation, before ever reaching the database) |

Every failure path logs only exception **types** and non-PII identifiers (a `candidate_id`
UUID, an HTTP status code) — never an exception's message or any extracted candidate data,
since either can embed name/email/phone. This is enforced by convention throughout the
codebase and checked by tests (`tests/test_api.py`'s log-leak tests).

## PII boundary

- Candidate PII (name, email, phone, raw resume text) lives **only** in
  `resume_intel.candidates`, reachable only by this service's own database role.
- The only PII egress from this service is `GET /v1/candidates/{candidate_id}/contact`,
  gated by the service API key, intended for transient use only (callers must not persist
  the response).
- Everything sent to Second Brain (`CandidateSummary`) is built from an explicit
  professional-fields allow-list and independently schema-validated (`extra="forbid"`, no
  PII-shaped field exists on the model). This is proven directly by
  `tests/test_candidate_summary.py`'s PII-boundary test group, and was also confirmed live:
  a real ingested candidate's `resume_intel.candidates` row holds the name/email/phone,
  while the matching `second_brain.candidate_knowledge` row (same `candidate_id`) contains
  none of it — checked by reading the actual stored `raw_text`, not just asserting against
  a mock.

## Authentication

- Both `POST /v1/resumes` and `GET /v1/candidates/{candidate_id}/contact` require the
  `X-Api-Key` header to match `SERVICE_API_KEY` — the same static service-to-service API
  key pattern used by every other cross-service call in the platform (no JWT, no OAuth).
  This service never validates a recruiter JWT at all; it doesn't understand the concept.
- The recruiter-facing JWT boundary lives entirely upstream, on RWS's own
  `POST /v1/resumes` (protected by `require_recruiter`, a short-lived JWT in
  `Authorization: Bearer`). A recruiter's browser never receives or holds this service's
  API key — RWS forwards the uploaded file here with its own service credential after
  checking the JWT. Sending a recruiter JWT directly to this service's endpoints has no
  effect: it isn't a header this service reads, and even a well-formed
  `Authorization: Bearer <jwt>` header is simply ignored (only `X-Api-Key` is checked).

## Supported file types

`.pdf`, `.docx`, `.jpg`, `.jpeg`, `.png`. `.doc` is explicitly not supported (see above).
Anything else is rejected with `422` before any extraction is attempted.

## Environment variables

| Variable | Read by | Purpose |
|---|---|---|
| `DATABASE_URL` | `db.py`, `alembic/env.py` (each independently — see below) | Postgres connection string for the least-privilege `resume_intel_role` |
| `SERVICE_API_KEY` | `api.py` | key RWS presents to `GET /v1/candidates/{id}/contact` |
| `GEMINI_API_KEY` | `api.py` | Gemini API key |
| `GEMINI_MODEL` | `api.py` | Gemini model for structured extraction (default `gemini-3.6-flash`) |
| `SECOND_BRAIN_URL` | `api.py` | base URL for the Second Brain ingestion call |
| `SECOND_BRAIN_API_KEY` | `api.py` | service key Second Brain expects from callers |
| `LOG_LEVEL` | `api.py` | JSON log verbosity |

All API-level settings above (everything except `DATABASE_URL`) live in one place:
`api.py`'s single `pydantic-settings` `Settings` class. `DATABASE_URL` is read directly
from the environment independently by both `db.py` (the FastAPI app) and
`alembic/env.py` (the standalone migration CLI, which runs outside the app entirely) —
this is a deliberate exception, not leftover duplication: Alembic cannot depend on the
FastAPI app's `Settings` object without pulling in the whole web framework for a tool that
has nothing to do with it, and this same pattern is used consistently by every other
service's own `alembic/env.py` in this platform.

## Failure semantics summary (also see the tables above)

- **Extraction failure** (a malformed/corrupt file crashing `extract_text`): `500`, no
  candidate stored.
- **Gemini total failure** (3 bounded attempts, all failed): `502`, no candidate stored —
  never a placeholder "Unknown" row.
- **Second Brain failure**: `502`, but the Resume Intelligence candidate remains persisted;
  retrying the same upload is safe.
- **Unsupported file type**: `422`, rejected before any extraction is attempted.

## Known deliberate limitations

- **`.doc` is not supported.** Adding it back would mean adding `antiword` as a new system
  dependency for a rare, legacy format — deliberately not done.
- **No email-ingestion caller exists yet.** `POST /v1/resumes`'s service-key auth is
  deliberately generic (not RWS-specific) so a future trusted caller — e.g. an
  email-ingestion workflow that pulls resumes from an inbox — could call it the same way,
  with its own service-key credential. That workflow itself is explicitly not implemented.
- **No skill taxonomy or synonym resolution.** Skills are deduped only by exact
  (case-insensitive) match — "JS" and "Javascript" are kept as distinct entries. This is a
  deliberate scope boundary (see the project's own CLAUDE.md), not an oversight.
- **No fuzzy candidate deduplication.** Dedup is exact-normalized-email only. A candidate
  who reapplies with a different email is never merged with their existing record —
  intentional, to avoid ever incorrectly combining two different people.
- **Gemini free-tier daily quota.** The `GEMINI_API_KEY` configured for this project is on
  Gemini's free tier, which enforces a hard daily request cap per model. Live smoke testing
  during development has occasionally hit this cap; it produces a clean `502` (bounded
  retries, no candidate stored), not a code defect, and resets after 24 hours.
- **`tests/test_db.py`'s dedup/no-email tests need a real, reachable Postgres** and skip
  cleanly otherwise. In this project's sandboxed host environment, the Postgres container's
  mapped host port does not accept the credentials this service's own `.env` carries (a
  container-init-time password mismatch, not a code issue) — run these from inside the
  container instead: `docker compose run --rm resume-intelligence pytest tests/test_db.py`.
- **No test suite exists for Second Brain** in this repository (`services/second-brain` has
  no `tests/` directory), so the Second Brain side of the `CandidateSummary` integration is
  verified through live smoke testing rather than a regression suite.
