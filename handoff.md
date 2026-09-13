# Company Second Brain — Implementation Handoff

Cross-service source of truth: backend architecture, live API surface, security
boundaries, known limitations, and demo readiness. Frontend-only implementation
detail (routes, pages, components, visual design) lives in `FRONTEND_HANDOFF.md`
instead — this file never duplicates it, only references it (per the two files'
own long-standing division of labor).

**Project status: feature-complete; one queued follow-up feature remains** (see
`[NEXT]` at the end). Every backend service, the full recruiter frontend, and
the 2D/3D knowledge graph are implemented and passed a final whole-system
readiness audit (see "Demo readiness" below). A dedicated audit of VoiceHR's
autonomous real-time candidate interview confirmed it was already fully built
in a prior session and re-verified its architecture live this session — see
milestone 25 and the Known Limitations entry below for a real, external
(non-code) access issue that audit surfaced. A job-specific VoiceHR language
eligibility gate was then implemented as a deterministic, RWS-owned
second-stage screening signal on top of that interview flow (milestone 26),
VoiceHR was made to email candidates their interview link automatically
(milestone 27), and most recently the integration-test fixture that had
accidentally wiped the live demo database was hardened and the demo data it
destroyed was restored (milestone 28).

**RESOLVED — the previous "demo backbone unusable" warning that used to live
here.** Both blockers it described are now fixed: (1) the destructive
integration-test fixture can no longer run against anything but an
unmistakably-named, dedicated test database (see the new test-isolation
invariant under "Architecture / invariants" and "How to run integration tests
safely" below) — the exact incident that happened before is now structurally
impossible to repeat by accident; (2) representative demo Jobs/MatchResults/
Applications have been recreated (see "Current state" below) via a new,
explicit, idempotent seed script, including reconnecting one application to
VoiceHR's own surviving completed-report history. **What is still genuinely
unresolved**: VoiceHR's `RESEND_API_KEY` remains unset in this environment, so
**every real `POST /v1/applications/{id}/invite` call still fails with a
clean 502** — the seed script worked around this (it never calls that
endpoint), but a live "Invite" click in front of a jury still cannot succeed
until a real Resend key is configured (see Known Limitations).

---

## Current state

All 5 containers (`postgres`, `resume-intelligence`, `second-brain`,
`recruitment-workflow`, `voicehr`) build and run via `infra/docker-compose.yml`
(`docker compose up -d`), all with `restart: unless-stopped`. Three Postgres
schemas (`resume_intel`, `second_brain`, `recruitment`) with `pgvector` for
embeddings, one role per schema, no cross-schema grants (verified live).

**Test counts** (last confirmed green, this session):

| Service | Tests | Notes |
|---|---|---|
| Resume Intelligence | 86 passed | Runs in-container or on host. |
| Recruitment Workflow (RWS) | 105 passed | `services/recruitment-workflow/tests/` — mocked-DB unit/route tests, host or in-container. |
| VoiceHR | 154 passed | **Host only** — its Docker image deliberately ships without `pytest` (lean production image). +13 this session for automatic invitation email delivery (idempotent send/retry, job-title/language content, failure semantics, the explicit resend route). |
| Second Brain | none | No `tests/` directory exists. A pre-existing, accepted gap — its one real integration (Resume Intelligence → Second Brain ingest/embed) is instead covered by a live end-to-end smoke test (candidate ingested, embedded, retrieved, PII boundary confirmed via direct `psql`). |

**A second test suite exists at the repo root** (`tests/unit/`,
`tests/integration/`) — discovered two sessions ago, not new work.
`tests/unit/` (138 passed) exercises `shared/` modules (schemas, scoring,
auth, LLM interface, observability, HTTP client) directly, no DB.
`tests/integration/` runs each service's real FastAPI app against a **real
Postgres** — see the test-isolation invariant immediately below for what
changed about how this now has to be run. `tests/integration/test_db_safety.py`
(13 passed, new this session) is a pure unit test of the guard itself, no DB
needed. `test_resume_intelligence.py`/`test_second_brain.py` exist too but
were not run this session (untouched by this session's changes).

**Test-database isolation is now a hard invariant, not a caution.** The
incident this used to warn about here — `test_recruitment_workflow.py`'s
`truncate_recruitment_tables` fixture (session-scoped, `autouse=True`,
unconditional `TRUNCATE` of `recruitment.jobs`/`job_descriptions`/
`match_results`/`applications`/`recruiter_decisions`/`interview_webhook_events`)
being run directly inside the live `csb-recruitment-workflow` container,
where it silently fell back to that container's own real `DATABASE_URL` and
wiped every job/application/match row that had accumulated across many prior
sessions — is now structurally impossible to repeat by accident. See
"Architecture / invariants" and "How to run integration tests safely" below
for the actual mechanism (`tests/integration/db_safety.py`); the short
version is that both destructive fixtures (recruitment-workflow *and*
second-brain — the same unconditional-`TRUNCATE`-on-import pattern existed
there too, found and fixed the same way) now fail closed at import time with
a clear error unless `TEST_DATABASE_URL` is explicitly set to a database
whose name unmistakably marks it as disposable, with the real demo database
name additionally denylisted as defense in depth. `resume_intel.*`/
`second_brain.*` (candidates, knowledge) and VoiceHR's own SQLite were never
affected by the original incident (different schema / different datastore
entirely) — only RWS's own jobs/matches/applications were lost, and those
have since been restored (see below).

**Live-verified end to end, real services, no mocks** (repeated across many
sessions): login → JD submission (real Gemini structuring) → semantic
retrieval (real `pgvector`) → deterministic+hybrid scoring → candidate
selection → VoiceHR invitation → a real interview processed through the
actual Gemini Live + evaluation pipeline → signed webhook → recruiter
accept/reject → Second Brain feedback write-back → 2D/3D knowledge graph
rendering the resulting `MatchResult`s. (The specific job/application UUIDs
this used across sessions before milestone 28 no longer exist — see milestone
28 — but the mechanisms themselves were genuinely exercised at the time.)

**Current demo data (restored this session, milestone 28)**: one job,
`Email Delivery Smoke Test Engineer` (`40fdac22-121d-4d9f-a7c5-84bc333bd2e0`,
kept as-is — internal-sounding title, but real, unmodified `MatchResult`s
already scored against it from a prior session) with 8 real `MatchResult`s
(6 qualified) and 3 `Application`s: two in `selected` state, and one
(`a2769e7b-f63b-4d73-8ac3-a2e443b3d46b`, candidate "Amine Sridi") reconnected
to VoiceHR's one surviving completed interview/report (`overall_score: 85`,
`cefr_level: C1`) via a real replay of RWS's own webhook endpoint using
VoiceHR's actual currently-stored report data — not a fabricated assessment.
A second job, `Backend Engineer` (`14d720ed-df49-46bd-8d49-d6ce549ccac2`),
also exists but is **not usable for a demo**: it was created by an early,
since-corrected attempt at the seed script below, using a JD phrasing that
led Gemini's JD-structuring to extract `required_skills` (`"REST APIs"`,
`"API Deployment"`) none of the existing candidate pool's Second Brain skill
lists actually contain, so every one of its matches came back disqualified.
Left in place rather than hidden (no delete capability exists for jobs, by
design — see Architecture below) — harmless clutter in the Jobs list, not a
functional problem. Restored via a new script,
`services/recruitment-workflow/scripts/seed_demo_data.py` (idempotent,
demo/dev-only, requires `--yes`) — see "How to run integration tests safely"
below for why it exists and exactly what it does and does not do. Real
finding from running it: Gemini's free-tier daily quota (`quotaValue: 20`) is
exhausted again as of this session (confirmed via real `429 Too Many
Requests` responses) — the second job's creation succeeded before hitting it,
but a further job-creation attempt failed cleanly with the documented `502`,
no partial data written, exactly per this project's existing quota-failure
contract.

**This session (automatic invitation email delivery, milestone 27)**: a fresh
job/candidate/application created live, then `invite` called against the real
running stack with no `RESEND_API_KEY` configured — confirmed a clean `502`
surfaced all the way to a real browser's Applications page (error banner
visible, Invite/Accept/Reject all still enabled, status still `selected`, not
advanced to `invited`); confirmed in VoiceHR's own SQLite that the interview
row was still created with `invite_email_error` set and
`invite_email_sent_at` NULL; retried the identical `invite` call and confirmed
via `SELECT COUNT(*)` that no duplicate interview was created for the same
idempotency key, and that the retry attempted delivery again (not skipped).
The success path (email actually arriving) is covered thoroughly by mocked
unit tests but was **not** exercised live this session — no real
`RESEND_API_KEY` is configured in this environment (see Known limitations).

---

## Architecture / invariants

These mirror `CLAUDE.md`'s non-negotiable decisions; do not change any of them
without following that file's "if you think a decision is wrong" process.

- **One Postgres + pgvector instance, 3 schemas, 3 least-privilege roles, no
  cross-schema grants.** Services reach each other only over HTTP. Verified
  live: each role's grants are scoped to exactly its own schema.
- **Scoring is deterministic; the LLM never scores.** `scoring.py` computes
  `skill_score`/`experience_score`/`title_score` → `deterministic_score`,
  blended with Second Brain's `semantic_score` into `final_score`. A
  **required-skill hard gate** is evaluated independently:
  `qualified = hard_gate_passed AND final_score >= threshold` — a missing
  required skill disqualifies regardless of how high the score is. Gemini only
  ever writes the advisory `explanation` text, generated lazily on first read
  of `GET /v1/jobs/{job_id}/matches` and cached per-`MatchResult` from then on
  (never regenerated, never influences the score).
- **`MatchResult` is immutable — at the application level.** Every route only
  ever inserts; no code path issues an `UPDATE`/`DELETE` against
  `recruitment.match_results`. **Not enforced at the database level**:
  `recruitment_role` does hold `UPDATE`/`DELETE` grants on that table (a
  blanket per-schema grant, same as every other table the role must mutate).
  If asked "what stops a MatchResult from being edited," the honest answer is
  application-code discipline plus test coverage, not a DB constraint —
  correcting that in a schema migration was judged out of scope for the final
  readiness audit (not demo-blocking).
- **The VoiceHR language eligibility gate is a second-stage screening signal,
  deterministic, RWS-owned, and never touches resume matching.**
  `language_gate.py`'s `compute_language_gate()` is a pure function (five
  plain arguments in, one status out — no I/O, no LLM call) comparing a job's
  optional `(required_language, min_language_cefr)` against VoiceHR's
  webhook-reported `(language, cefr_level)` via a fixed `CEFR_RANK` ordering
  (A1–C2). It never reads or writes `MatchResult`/`final_score`/`qualified` —
  those remain exactly what resume scoring computed. A job's requirement is
  mutable (`PATCH /v1/jobs/{job_id}/language-requirement`), so it is
  **snapshotted onto the `Application` row at selection time**
  (`required_language`/`required_cefr` columns) — editing a job afterwards
  never changes an already-reported gate result (verified live: patching an
  existing job's requirement left its existing applications' gate at
  `not_required`, unchanged). The *assessed* language/CEFR are read straight
  out of the already-persisted `interview_assessment` JSONB (no separate
  columns) — reproducible for the same reason that blob is never mutated
  after its one webhook-triggered write. Gate status
  (`not_required`/`pending`/`passed`/`failed`/`needs_review`) is computed
  fresh on every read by the same function `accept_application` calls to
  enforce it, so "what the recruiter is shown" and "what blocks accept" can
  never drift apart. Only `accept` is gated — `reject` is never blocked,
  since a recruiter can always decline regardless of screening outcome.
- **Three independent, mutually-exclusive auth mechanisms** (verified never to
  substitute for one another, both by code inspection and by test): recruiter
  JWT (`Authorization: Bearer`, `JWT_SECRET`, `JWT_EXPIRY_MINUTES=45`) for
  every recruiter-facing RWS route; static service API keys (`X-Api-Key`) for
  service-to-service calls; HMAC-SHA256 signature (`X-Signature` over the raw
  body, `X-Delivery-Id` for idempotency) for the one inbound async boundary,
  VoiceHR's completion webhook. VoiceHR's own admin surface uses a fourth,
  separate mechanism (HTTP Basic, `secrets.compare_digest`) scoped only to
  VoiceHR's own PII-bearing report pages — never accepted by any RWS route.
- **PII lives only in `resume_intel.candidates`.** Every other schema/service
  sees only an opaque `candidate_id`. Shared cross-service Pydantic schemas
  (`shared/schemas/__init__.py`) use `extra="forbid"` so a stray PII field is
  rejected at the boundary, not silently passed through. RWS itself persists
  no candidate identity at all — its identity/knowledge-proxy routes
  (`get_candidate_identity`, `get_candidates`, `get_candidate_knowledge`,
  `get_candidates_jobs_graph`'s edges) declare no `db` dependency, a
  structural (not just documented) proof they read nothing of RWS's own and
  write nothing new.
- **VoiceHR keeps its own SQLite, unmigrated**, per `CLAUDE.md`. Its interview
  engine converged to Gemini Live as the sole interview-conduct
  implementation and Gemini as the sole evaluation provider over several
  cleanup passes (no more Vapi, local Whisper/Ollama/Kokoro/ElevenLabs, or
  Anthropic evaluator branch) — its persistence model, SQLite itself, and its
  interview logic were never rewritten, only its API surface extended
  (`external_reference_id`, `Idempotency-Key`, the outbound signed webhook,
  HTTP Basic admin auth). Its DB path (`storage/voicehr.db`) sits inside the
  `voicehr_data` named volume specifically so it survives a container
  recreate — confirmed live a third time this session (an unrelated
  `docker compose up` forced a recreate; the one existing interview/report row
  was still there immediately after).
- **VoiceHR owns sending the candidate their interview invitation email —
  automatically, and its success is what makes an invite succeed at all.**
  `POST /api/invite` (`backend/api/routes/invite.py`: `create_invite`) sends
  the email synchronously, in the same request, via `interview.py`'s
  `deliver_invitation_email()`/`_send_email()` (the same helper the
  previously-dead `POST /interview/{token}/invite` explicit-resend route now
  also calls — one delivery implementation, not two). RWS never sees the
  link, never stores the candidate's email, and never sends anything itself
  — it only learns whether delivery succeeded, via this same call's HTTP
  status. On failure, `create_invite` returns a non-2xx response, which
  `voicehr_client.invite_candidate` turns into `VoiceHRClientError`, which
  `main.py`'s `invite_application` turns into a `502` — and critically,
  `record_invite_attempt` is only ever reached after a *successful* response,
  so a failed delivery can never advance `Application.status` to `invited`.
  **Idempotency**: `interviews.invite_email_sent_at` (NULL until the first
  successful send) is what a duplicate `POST /api/invite` on the same
  `Idempotency-Key` checks before deciding whether to (re)attempt delivery —
  already-delivered → skip and return the existing state (no resend);
  not-yet-delivered (a prior attempt failed) → retry delivery on the *same*
  interview row, never create a second one. A genuinely new attempt number
  (a recruiter's manual "Resend Invite") is, as before, a new interview and a
  new email, by design. `interviews.job_title` is stored purely for the
  email's greeting (RWS passes `Job.title`, cosmetic only, never used for
  matching/eligibility); `invite_email_error` is retained only as VoiceHR's
  own internal diagnostic, never surfaced in the email itself.
- **The real-time interview session is candidate-only by construction, not by
  policy.** Confirmed by source inspection this session: RWS's frontend has
  zero WebSocket usage anywhere (`grep -ri "websocket" src/` — no matches),
  `InterviewsPage`/`ApplicationsPage` only ever render a final, already-scored
  `interview_assessment` object fetched over plain `GET`, and VoiceHR's own
  admin surface (`GET /admin/reports`) is a read-only completed-reports list
  with no session/live-interview route at all. There is no code path a
  recruiter/admin could use to join, watch, or influence a session in
  progress — the WebSocket at `/ws/interview/{token}` is opened directly by
  the candidate's own browser and nothing else ever connects to it.
  `backend/services/gemini_live.py` instantiates `genai.Client(api_key=...)`
  and calls `client.aio.live.connect(...)` **inside the VoiceHR container**;
  the browser only ever holds a same-origin WebSocket to VoiceHR
  (`ws://.../ws/interview/{token}`, confirmed via live Playwright network
  inspection — no Google/Gemini host ever contacted from the browser side),
  so the real Gemini API key never reaches client code.
- **Gemini is the sole AI provider**, behind `shared/llm.py`'s `LLMProvider`
  interface (`GeminiProvider`: `generate_text`, `generate_structured`,
  `embed_text`). Model: `gemini-3.6-flash` for text/structuring/evaluation,
  `gemini-embedding-001` for embeddings. Free-tier daily quota is a real,
  hit-in-practice constraint (`quotaValue: 20`/day for `gemini-3.6-flash`) —
  see "AI / Gemini quota" below.
- **No message brokers/orchestrators** (Kafka/RabbitMQ/Temporal/Redis/Celery/
  Kubernetes/ELK). The one async boundary (VoiceHR → RWS) is a signed,
  idempotent webhook. Structured JSON logging + a per-request correlation ID
  (propagated across service boundaries, confirmed live) cover observability;
  nothing is shipped anywhere beyond container stdout.
- **Destructive integration-test fixtures may only ever run against an
  explicit, unmistakably-named test database — never the real one, and never
  by falling back to a default.** `tests/integration/db_safety.py`'s
  `resolve_test_database_url()` is the one function every session-scoped
  `autouse` `TRUNCATE` fixture (recruitment-workflow's
  `truncate_recruitment_tables`, second-brain's
  `truncate_candidate_knowledge`) calls instead of reading `DATABASE_URL`
  directly. It fails closed (raises `UnsafeTestDatabaseError`, at import time,
  before a single query runs) in three cases: `TEST_DATABASE_URL` unset,
  `TEST_DATABASE_URL` naming the real database (`company_second_brain`,
  denylisted explicitly as defense in depth), or `TEST_DATABASE_URL` naming
  anything that doesn't contain `"test"` — there is no code path in either
  conftest that can silently substitute `DATABASE_URL` or a `localhost`
  default the way the pre-fix version did. A dedicated database,
  `company_second_brain_test`, exists on the same Postgres instance (same 3
  schemas, same 3 roles/grants, migrated to the same head revision as the
  real one) specifically to be this project's disposable integration-test
  target — see "How to run integration tests safely" below.

---

## API surface

Cross-checked directly against each service's route decorators this session —
not carried forward from memory.

### Recruitment Workflow (RWS) — the only service the browser talks to

| Method & path | Auth | Notes |
|---|---|---|
| `GET /health` | none | |
| `POST /v1/auth/login` | none | Issues the recruiter JWT. |
| `POST /v1/resumes` | JWT | Forwards to Resume Intelligence with the service key; browser never sees it. |
| `GET /v1/jobs` | JWT | `match_count` via one `LEFT JOIN`, no pagination. Each row also carries `required_language`/`min_language_cefr` (both `null` if the job has no requirement). |
| `POST /v1/jobs` | JWT | JD structuring (Gemini) → Second Brain retrieval → scoring → persists one `MatchResult` per candidate. Optional `required_language`/`min_language_cefr` (both or neither) set the job's language eligibility requirement — recruiter-set only, Gemini never sees these fields. |
| `PATCH /v1/jobs/{job_id}/language-requirement` | JWT | Configures or clears (both null) an existing job's language requirement. Never touches any already-created `Application`'s own snapshot. |
| `GET /v1/jobs/{job_id}/matches` | JWT | Lazily generates+caches `explanation` per match on first read. |
| `POST /v1/scoring-configs` | service key | Admin/system action, not recruiter-facing. |
| `GET /v1/candidates/{id}/identity` | JWT | Transient proxy to Resume Intelligence; no `db` dependency. |
| `GET /v1/candidates` | JWT | Transient proxy to Resume Intelligence; no `db` dependency. |
| `GET /v1/candidates/{id}/knowledge` | JWT | Transient proxy to Second Brain; no `db` dependency. |
| `GET /v1/graph/candidates-jobs` | JWT | Reads RWS's own persisted `MatchResult`/`Job` rows only — the 2D/3D knowledge graph's sole data source. Both qualified and disqualified edges included, unfiltered. |
| `POST /v1/matches/{match_id}/select` | JWT | 422 if not qualified; 409 on duplicate (DB `UNIQUE` constraint). Snapshots the job's current language requirement (if any) onto the new `Application`. |
| `POST /v1/applications/{id}/invite` | JWT | Pre-flight Resume Intelligence contact lookup (422 if none, before ever calling VoiceHR); idempotent retry via `{application_id}:{attempt}`. If the application has a snapshotted language requirement, VoiceHR is invited in that language regardless of the request body's own `language` field. Also passes the job's `title` (cosmetic, for VoiceHR's candidate email). **Success now means VoiceHR actually emailed the candidate** — a `502` here can mean either VoiceHR is unreachable or the email failed to send; either way the application is never marked `invited`. |
| `POST /v1/applications/{id}/accept` | JWT | Atomic conditional `UPDATE`; schedules a background Second Brain feedback write-back after the response is already sent. **422 if the language eligibility gate is `pending`/`failed`/`needs_review`** — the response body names which and the required-vs-assessed values. |
| `POST /v1/applications/{id}/reject` | JWT | Same state machine, background write-back — **never gated by the language eligibility gate**; a recruiter can always decline. |
| `GET /v1/jobs/{job_id}/applications` | JWT | Job-scoped. Every row's `language_gate` object (`status`, `required_language`, `required_cefr`, `assessed_language`, `assessed_cefr`) is computed fresh on every read, never a stored column. |
| `GET /v1/applications` | JWT | Global, unscoped, adds `job_title` and the same `language_gate` object. |
| `GET /v1/interviews/{id}/report` | JWT | Proxies VoiceHR; rewrites `recording` to a same-origin RWS URL — browser never sees VoiceHR's host or the service key. |
| `GET /v1/interviews/{id}/recording` | JWT | Relays raw audio bytes; no disk/DB write. |
| `POST /v1/webhooks/voicehr` | HMAC (`X-Signature`) | Inserts into `interview_webhook_events` (dedup by `delivery_id`) before any state mutation; unresolvable/malformed references return `200` no-op, never a retry-storming error. `assessment` now also carries `language` (the interview's own conducted language) alongside the pre-existing `cefr_level` — both feed the language eligibility gate. |

### Resume Intelligence (PII owner)

| Method & path | Auth |
|---|---|
| `GET /health` | none |
| `POST /v1/resumes` | service key |
| `GET /v1/candidates/{id}/contact` | service key |
| `GET /v1/candidates` | service key |

### Second Brain (PII-free knowledge + retrieval)

| Method & path | Auth |
|---|---|
| `GET /health` | none |
| `POST /v1/knowledge/candidates` | service key |
| `GET /v1/knowledge/candidates/{id}` | service key |
| `POST /v1/retrieve/candidates` | service key |
| `POST /v1/knowledge/candidates/{id}/feedback` | service key |

### VoiceHR (interview conduct — RWS-facing + candidate-facing + admin)

| Method & path | Auth |
|---|---|
| `POST /api/invite` | service key (`X-Api-Key`, RWS only) |
| `GET/POST /interview/{token}...` (start/prepare/complete) | token-gated, candidate-facing |
| `GET /api/rws/interviews/{external_reference_id}/report` \| `/recording` | service key — the two routes RWS's proxy above calls |
| `GET /api/report/{report_token}`, `GET /report` | unguessable-token bearer link, candidate/shareable |
| `GET /api/admin/reports`, `GET /admin/reports`, `POST /api/admin/reports/{token}/retry`, `GET /recordings/{filename}` | HTTP Basic |
| `GET /health`, `GET /health/detail` | none |

`POST /api/invite` now also emails the candidate their interview link
automatically, synchronously, as part of the same call — see "Architecture /
invariants" above for the full idempotency/failure-semantics contract. The
previously-dead `POST /interview/{token}/invite` (unauthenticated, token-only)
now shares that same delivery implementation and is kept as an explicit,
always-resends primitive — still not called from anywhere in the current
architecture, per this session's own "do not restore the old admin-UI flow"
instruction.

---

## How to run integration tests safely

`tests/integration/` has three real-Postgres conftest files:
`conftest.py` (Resume Intelligence — no destructive fixture; every test rolls
back its own transaction, safe against any reachable Postgres), and
`conftest_recruitment_workflow.py` / `conftest_second_brain.py` (both have a
session-scoped `autouse` `TRUNCATE` fixture — see "Architecture /
invariants" above). Only the latter two need the steps below.

**One-time setup** (already done in this environment; skip if
`company_second_brain_test` already exists):

```bash
docker exec csb-postgres psql -U csb_admin -d postgres \
  -c "CREATE DATABASE company_second_brain_test OWNER csb_admin;"
docker exec -i csb-postgres psql -U csb_admin -d company_second_brain_test <<'EOSQL'
CREATE SCHEMA IF NOT EXISTS resume_intel;
CREATE SCHEMA IF NOT EXISTS second_brain;
CREATE SCHEMA IF NOT EXISTS recruitment;
CREATE EXTENSION IF NOT EXISTS vector SCHEMA second_brain;
GRANT USAGE, CREATE ON SCHEMA resume_intel TO resume_intel_role;
GRANT USAGE, CREATE ON SCHEMA second_brain TO second_brain_role;
GRANT USAGE, CREATE ON SCHEMA recruitment TO recruitment_role;
GRANT CONNECT ON DATABASE company_second_brain_test
  TO resume_intel_role, second_brain_role, recruitment_role;
EOSQL
# Then run each service's own Alembic migrations against it once, e.g.:
docker exec -e DATABASE_URL="postgresql+psycopg://recruitment_role:changeme@postgres:5432/company_second_brain_test?options=-csearch_path%3Drecruitment" \
  csb-recruitment-workflow alembic upgrade head
```

**Every actual test run** (host-side `pytest` cannot reach Postgres at all on
this Windows/WSL2/Docker Desktop setup — see the existing "Host-side Postgres
tests may not connect" entry below; run inside the container instead):

```bash
docker cp tests/integration/db_safety.py csb-recruitment-workflow:/app/db_safety.py
docker cp tests/integration/conftest_recruitment_workflow.py csb-recruitment-workflow:/app/conftest.py
docker cp tests/integration/test_recruitment_workflow.py csb-recruitment-workflow:/app/test_recruitment_workflow.py
docker exec -e TEST_DATABASE_URL="postgresql+psycopg://recruitment_role:changeme@postgres:5432/company_second_brain_test?options=-csearch_path%3Drecruitment" \
  csb-recruitment-workflow python -m pytest /app/test_recruitment_workflow.py -v
```

Same pattern for `conftest_second_brain.py` / `test_second_brain.py` against
`csb-second-brain`, with `second_brain_role` and `-csearch_path%3Dsecond_brain`.
**Never omit `-e TEST_DATABASE_URL=...`** — without it, both conftests now
raise `UnsafeTestDatabaseError` at import time and no test runs at all,
which is the entire point (see Architecture above).

**Verified this session**: ran `test_recruitment_workflow.py` with no
`TEST_DATABASE_URL` set at all — reproduced the exact original incident's
starting condition — and it failed at conftest import with
`UnsafeTestDatabaseError`, zero queries issued. Then ran it correctly against
`company_second_brain_test` (88 passed) and confirmed via direct `psql`
row counts on `company_second_brain` (the real demo database) that
`jobs`/`applications`/`match_results` were bit-for-bit identical before and
after (2/3/16 both times) — the isolation is real, not just claimed.

## Demo data seed script

`services/recruitment-workflow/scripts/seed_demo_data.py` — demo/dev tooling
only, refuses to run without `--yes`, never imported or called by the
application itself. Recreates a representative Job/MatchResult/Application
backbone using only real mechanisms: the real `POST /v1/jobs` pipeline (only
if a job with the given `--job-title` doesn't already exist — skippable
entirely by pointing `--job-title` at an already-existing usable job, e.g. if
Gemini's daily quota is exhausted, which it was by the time this was used
this session), the real `POST /v1/matches/{id}/select` endpoint, and — for
reconnecting to VoiceHR's surviving interview history — a direct, explicit,
clearly-commented `UPDATE` (advancing one `Application` to `invited` without
ever calling the email-sending invite endpoint) followed by a replay of
RWS's real webhook endpoint using VoiceHR's own real, already-stored report
data. Never fabricates a candidate, a score, or a VoiceHR assessment; never
sends a VoiceHR email (confirmed live — it prints an explicit confirmation of
this on every run, and structurally cannot reach the code path that sends
one, since it never calls `POST /v1/applications/{id}/invite`). Idempotent:
verified live by running it twice — the second run recognized every job,
application, and the report reconnection as already done and changed
nothing further.

```bash
python services/recruitment-workflow/scripts/seed_demo_data.py --yes
# Or, reusing an existing job to avoid any Gemini call:
python services/recruitment-workflow/scripts/seed_demo_data.py --yes --job-title "Email Delivery Smoke Test Engineer"
```

---

## Known limitations

- **CRITICAL, external, not a code defect: VoiceHR's Gemini API key/project is
  currently denied access by Google.** Discovered this session while live-
  verifying the autonomous interview flow (see milestone 25): both a fresh
  Gemini Live connect attempt and a plain-text evaluator call
  (`POST /api/admin/reports/{token}/retry`) failed identically with
  `403 PERMISSION_DENIED — "Your project has been denied access. Please
  contact support."` (Live API surfaces it as a WebSocket close code 1008).
  **Scope confirmed narrow**: RWS/Resume Intelligence/Second Brain use a
  *different* `GEMINI_API_KEY` and were confirmed still fully working the same
  session (a real job creation triggered a live JD-structuring + embedding
  call successfully) — this is isolated to VoiceHR's own key/project, not a
  system-wide outage. Nothing in this codebase can fix it; it requires action
  in Google's console (billing/support) on whichever account owns that key.
  **Demo impact is contained**: the existing demo plan already avoids a live
  VoiceHR invite/interview (see "no in-product way to retrieve a VoiceHR
  interview link" below) and uses an already-completed report instead — that
  report was generated in an earlier session, before this access was denied,
  so it remains valid to show. Do not attempt a fresh live VoiceHR interview
  in front of the jury; if asked, the honest answer is "VoiceHR's Gemini
  access is currently denied by Google, verified during my last audit — the
  rest of the pipeline uses a separate key and is unaffected."
- **RESOLVED, two sessions ago's incident: every `recruitment.jobs`/
  `applications`/`match_results` row was wiped by an integration-test fixture
  run against the live Postgres by mistake.** Both halves are now fixed: the
  fixture itself can no longer do this again (see the test-isolation
  invariant under "Architecture / invariants" and "How to run integration
  tests safely" above), and the data it destroyed has been restored (see
  "Current demo data" under "Current state" above) via a new, explicit,
  idempotent seed script. Any job/application UUID referenced in this
  document's older history sections that predates this entry may still be
  stale (the mechanisms they describe were genuinely exercised at the time,
  the specific rows just no longer exist) — the "Current demo data" note is
  the one place with current, real UUIDs.
- **Gemini's free-tier daily quota was exhausted again while restoring demo
  data this session** (confirmed via real `429 Too Many Requests` responses
  from `generativelanguage.googleapis.com`, surfaced to the caller as RWS's
  own documented clean `502`, no partial data written). Not a new limitation
  — this project has hit and documented this exact quota before (see "AI /
  Gemini quota" under Demo readiness) — but a reminder that quota resets on
  Google's own schedule, not this project's, so plan any further live Gemini
  use (a fresh resume upload, a fresh job) accordingly on demo day.
- **Host-side Postgres tests may not connect.** On this Windows + Docker
  Desktop + WSL2 setup, host-side `psycopg` cannot reach the Docker Postgres
  container via `localhost:5432` (the WSL2 network proxy intercepts the port
  before `pg_hba.conf` applies, causing an auth failure regardless of
  credentials). Run integration tests inside the relevant container instead
  (`docker exec csb-<service> pytest tests/`) if a host run fails to connect.
- **Lesson worth repeating if a similar table is ever added**: an append-only
  receipt/log table whose job includes recording *unresolvable* references
  (e.g. `interview_webhook_events.application_id`) must use a plain column,
  not a foreign key — a real bug here once made a genuinely-unknown reference
  raise the same `IntegrityError` as an intentional duplicate, silently
  misclassifying "unknown" as "duplicate." No mocked test caught it; only
  live testing did, since mocks don't enforce real FK constraints.
- **Second Brain has no test suite.** Its one real integration is covered by a
  live smoke test instead (see "Current state"). Not fixed — adding one is a
  real scope addition, not something the final readiness audit's "no refactor
  unless demo-blocking" rule authorized.
- **One candidate (`0a699701-fb39-4c5a-9a3f-cd706f678b2f`) exists in
  `second_brain.candidate_knowledge` with no matching `resume_intel.candidates`
  row** (a test-data hygiene artifact from earlier sessions' independent
  seeding, not a code defect). `GET /v1/candidates/{id}/identity` correctly
  404s and inviting them correctly 422s. **Still relevant right now**: this
  exact candidate ended up selected into the current restored demo data
  (application `3a6df887-7c61-4a86-a576-9ffa7d11820d`, status `selected`, on
  the `Email Delivery Smoke Test Engineer` job) — the seed script has no way
  to know this candidate is identity-less, since RWS itself only discovers
  that at invite time. Do not click Invite/Accept on this specific
  application in a live demo; use one of the other two restored applications
  instead.
- **RESOLVED this session (milestone 27): "no in-product way to retrieve a
  VoiceHR interview link."** VoiceHR now emails the candidate their secure
  link automatically as part of `POST /api/invite` itself — see "Architecture
  / invariants" above. This is no longer a gap, but see the very next entry
  for what currently blocks it from working in this environment.
- **CRITICAL, demo-blocking as of milestone 27: `RESEND_API_KEY` is empty in
  VoiceHR's `.env`.** Automatic invitation email delivery (milestone 27) is
  now what determines whether `POST /api/invite` succeeds at all — with no
  key configured, `_send_email` immediately raises, and **every real "Invite"
  click in the recruiter UI currently fails with a clean 502**, verified live
  this session. This is a behavior change from before milestone 27, when a
  missing key was harmless (no email was ever attempted). A real Resend API
  key must be configured before "Invite" can be demonstrated live. **Not
  blocking for demo data itself anymore**: this session's demo-data
  restoration (see "Current demo data" above) deliberately never calls
  `POST /v1/applications/{id}/invite` at all — it advances the one
  reconnected application to `invited`/`interviewed` via a direct, explicit
  database write plus a real webhook replay instead, specifically to avoid
  needing this key just to seed data. If Resend's own sandbox sender is used
  once a key is configured, note it can only deliver to the Resend account's
  own registered email address, not arbitrary candidates — a verified custom
  domain (`RESEND_FROM_ADDRESS` pointed at it) is required to actually invite
  real candidate addresses; see `services/voicehr/README.md`'s "Resend email
  configuration" section and `.env.example` for the exact steps.
  `ADMIN_EMAIL` being empty is unrelated and still harmless (only gates the
  separate "report ready" notification, not on any critical path).
- **`infra/.env` and every service's `DATABASE_URL` use placeholder secrets**
  (`changeme-*-key` convention; Postgres role passwords are literally
  `changeme`), and Postgres is published on `0.0.0.0:5432` (host network
  interface). Fine for an offline single-machine demo; **must be rotated and/or
  unpublished before this stack ever runs on a shared or public network.**
  Not rotated automatically, per the readiness audit's explicit instruction.
- **`.doc` (legacy Word binary) uploads are rejected with a clean `422`** —
  support was deliberately removed (no `antiword` in the container) rather
  than adding a system dependency for one legacy format. PDF/DOCX/JPG/PNG all
  work.
- **No pagination anywhere** (`GET /v1/jobs`, `GET /v1/candidates`, `GET
  /v1/applications`) — matches the project's "no infra ahead of demonstrated
  need" rule; revisit only if a real dataset size makes a full list
  impractical.
- **`second_brain.candidate_feedback` is a write-only sink.** Nothing currently
  reads it back to influence retrieval or scoring — an open design question,
  not a defect.
- **VoiceHR's own `admin.html`** is reachable unauthenticated at the raw
  `/admin.html` static path (bypasses the `/admin` route's auth dependency,
  since `StaticFiles` serves it directly) — the page itself is an empty shell
  whose JS calls the now-protected APIs, so the residual exposure is
  low-value; not fixed, per `services/voicehr/README.md`'s own documented
  limitation.
- **VoiceHR's admin-initiated invite *creation* flow stays removed, by
  design** (it was already fully broken — `require_rws_service_key` had
  silently 401'd it since that auth requirement was added — and RWS is the
  sole interview creator in this architecture). `POST
  /api/interview/{token}/invite` (`.../invite/preview`) itself was *not*
  removed and is no longer dead: milestone 27 gave it a real internal
  implementation (`deliver_invitation_email`, shared with the automatic path)
  and kept it as an explicit, always-resends primitive — still not called
  from any current frontend, which is deliberate scope for this session (see
  `[NEXT]`).

---

## Demo readiness

**Verdict: DEMO READY again, with one remaining known gap.** The
whole-system audit that originally produced a DEMO READY verdict is still
architecturally accurate (no code-level `CRITICAL`/`HIGH` defect). The demo
data that was wiped is restored (see "Current demo data" under "Current
state"), and the fixture responsible can no longer repeat the incident (see
the test-isolation invariant). **What's still genuinely blocked**:
`POST /v1/applications/{id}/invite` fails with a clean `502` whenever it's
actually called, because VoiceHR's `RESEND_API_KEY` is unset and (since
milestone 27) a successful email send is what makes an invite succeed at
all. This does not block the demo sequence below — it already routes around
a live invite by using the pre-reconnected, already-`interviewed`
application for the Report Detail leg — but it does mean a *live* "Invite"
click cannot be demonstrated until a real Resend key is configured and the
`voicehr` container rebuilt/restarted. The infrastructure fix from the
original audit (`restart: unless-stopped` on all four app containers) is
still in place and still correct.

**AI / Gemini quota** — the three genuinely quota-consuming paths, for
planning purposes: (1) `POST /v1/resumes` (one extraction call), (2)
`POST /v1/jobs` (one JD-structuring call + one embedding call, separate
quota bucket), (3) `GET /v1/jobs/{id}/matches` on a job whose matches have
never been read before (up to one explanation call *per candidate* — the
single most quota-hungry path on a cold job, cached forever after). Every
other demo action (login, dashboard, candidate/job/application/report detail,
accept/reject, both graph views) reads only already-persisted data and costs
zero quota.

**4-6 minute demo sequence**: Dashboard → Candidates → Candidate Detail →
Jobs → Job Detail (`Email Delivery Smoke Test Engineer`, ranked matches) →
Select (a *different* qualified candidate than the two already in the
restored data, to show the live flow working — Second Brain retrieval isn't
re-run, so this is instant and free) → Applications → **Invite only if
`RESEND_API_KEY` has been configured for the day** (otherwise skip this
click and say so — see the verdict above); either way, continue to → Report
Detail on application `a2769e7b-f63b-4d73-8ac3-a2e443b3d46b` (the
already-`interviewed` one, reconnected to VoiceHR's real report this
session). **Know before presenting it**: this specific report's own
`evaluation_status` is `"failed"` (a leftover from an earlier session's retry
attempt hitting the still-open Gemini-access-denial finding below, not
something this session caused) — `AssessmentPanel` therefore shows a red
"Failed" badge and hides the numeric scores entirely, even though the real
transcript, strengths/concerns, and recording all render normally below it.
This is honest, unmodified real data, not a bug in this restoration — but it
is *not* a "look at the great score" moment, so frame it as a resilience
story (a technical evaluation failure still produces a usable, reviewable
report) rather than a scoring showcase. VoiceHR's Gemini access is still
denied by Google (see below either way), so a *fresh* interview cannot be
conducted live regardless of the Resend key. → Accept → Reports → Knowledge
Graph 2D (search + filter) → 3D.

**Live AI vs. persisted data**: use live AI sparingly (one fresh resume
upload or one `New Job` creation, if quota confidence is high that day);
treat everything else — matches, applications, reports, both graph views —
as the safe, persisted backbone of the demo.

**Failure/recovery — what the presenter should do, not a system to build**:
- Gemini quota exhausted → `POST /v1/resumes`/`POST /v1/jobs` return a clean
  `502`, no partial data written. Say so, continue with persisted data.
- A backend service unreachable → RWS's dependent endpoints return `502`
  naming the upstream, never a stack trace. Same move.
- An application already `interviewed`/`completed` → re-inviting correctly
  409s. View its existing report instead of retrying.
- An invite fails because email delivery failed (e.g. `RESEND_API_KEY`
  missing/wrong, or Resend itself rejects the request) → a clean `502` naming
  the reason, verified live in a real browser (error banner, Invite/Accept/
  Reject all still enabled, status still `selected`). Retrying is always safe
  — the same interview is reused, never duplicated.
- The orphaned candidate (`0a699701-...`, currently sitting in application
  `3a6df887-...` in `selected` state) → correctly 404s/422s on
  identity/invite if clicked. Use a different application row instead.
- A browser refresh on any detail route → confirmed live to re-fetch and
  render correctly from cold. Safe to do live if needed.

**Pre-demo checklist**: confirm all 5 containers healthy; confirm `GET
/v1/jobs` returns the restored `Email Delivery Smoke Test Engineer` job (not
`[]` — if it does return empty, re-run
`services/recruitment-workflow/scripts/seed_demo_data.py --yes --job-title
"Email Delivery Smoke Test Engineer"`); if a live "Invite" click is wanted,
confirm `RESEND_API_KEY` is set in `services/voicehr/.env` and the container
has been rebuilt/restarted since (currently unset — otherwise skip that one
click, per the demo sequence above); do not click Invite/Accept on
application `3a6df887-...` (the orphaned candidate, see above); do one
low-stakes Gemini smoke check the morning of, not during rehearsal (today's
quota is exhausted — see Known Limitations — confirm it has reset); confirm
no `.env` file changed since last verified boot.

**Likely jury questions**:
1. *What stops the LLM from deciding who's qualified?* — Scoring is
   deterministic Python; Gemini only writes the explanation, after the score
   is already computed and persisted.
2. *What enforces `MatchResult` immutability?* — Application code discipline
   and test coverage, not a database constraint (see Architecture above) — an
   honest, defensible tradeoff at this project's scale.
3. *How is candidate PII protected?* — Only `resume_intel.candidates` holds
   it; every other schema/service sees only an opaque `candidate_id`, enforced
   by `extra="forbid"` schemas at every boundary.
4. *What happens if Gemini is down?* — A clean `502`, no partial state
   written, verified live.
5. *Why SQLite for VoiceHR?* — A pre-existing system, deliberately
   unmigrated per project scope; its one real bug (a DB path outside the
   persisted volume) was found and fixed, and re-verified live.
6. *Can you run a live candidate interview right now?* — No: VoiceHR's own
   Gemini API key is currently denied access by Google (an external,
   non-code issue found during this project's last audit — see Known
   Limitations). The rest of the AI pipeline (JD structuring, embeddings,
   match explanations) uses a separate key and is confirmed unaffected; the
   demo instead shows an already-completed interview report generated before
   that access was denied.
7. *Does the language interview affect the resume match score?* — No,
   deliberately. `final_score`/`qualified` come only from `scoring.py`
   (resume matching); the language gate is a separate, later, deterministic
   comparison (`language_gate.py`) that can only block *acceptance*, never
   change a `MatchResult`. Editing a job's requirement later never changes an
   already-reported gate result either — each application snapshots the
   requirement at the moment it was selected.
8. *How does the candidate actually receive their interview link?* — VoiceHR
   emails it to them automatically and securely, the moment a recruiter
   clicks Invite — RWS never sees or stores that link or the candidate's
   email address. A failed send is treated as a failed invite (a clean `502`,
   application never marked invited) so recruiters can never believe an
   invitation went out when it didn't; a retry safely reuses the same
   interview rather than creating a duplicate or double-emailing the
   candidate.
9. *An earlier session's test run wiped your demo database — how do you know
   that can't happen again?* — It was a real integration-test fixture
   (session-scoped, `autouse`, unconditional `TRUNCATE`) run against the live
   Postgres by mistake, not a code defect in the product itself. The fix
   isn't "be more careful next time" — it's structural: that fixture (and an
   identical one in a second file, found during the same fix) now calls a
   shared guard that fails closed at import time unless pointed at an
   explicitly-named, unmistakably-disposable test database, with the real
   database's name additionally denylisted. Verified live: running the exact
   same command that caused the original incident, with no test database
   configured, now fails before a single query executes; running it correctly
   against the dedicated test database leaves the real demo database's row
   counts bit-for-bit unchanged, proven by direct before/after `psql` counts,
   not just claimed.

---

## Historical milestones

Chronological, one line each — full session-by-session detail (exact test
diffs, live-smoke-test transcripts, root-cause narratives) has been
compressed out; nothing here should be treated as a live claim without
re-verifying against current source, per every prior session's own discipline.

1. Architecture design, AI model selection, monorepo skeleton, Docker Compose +
   `.env.example` per service, Postgres init (3 schemas/3 roles), Alembic per
   schema, `GET /health` on all four services, shared `extra="forbid"`
   Pydantic schemas.
2. `shared/llm.py`'s `LLMProvider`/`GeminiProvider` interface.
3. Resume Intelligence: candidates table + normalized-email dedup + contact
   endpoint.
4. Second Brain: knowledge upsert + `pgvector` embedding + semantic retrieval
   + per-candidate detail lookup.
5. RWS core: deterministic scoring engine (hard gate, weighted hybrid score),
   `Job`/`JobDescription`/`ScoringConfig`/`MatchResult` persistence, JD
   submission → structuring → retrieval → scoring → immutable `MatchResult`
   per candidate.
6. Recruiter JWT auth + candidate selection/accept/reject state machine +
   audit log (`recruiter_decisions`).
7. VoiceHR integration: invitation (`POST /api/invite`, idempotency), signed
   completion webhook (HMAC-SHA256), `interview_webhook_events` dedup table.
8. Second Brain feedback write-back (`recruiter_decisions` → `candidate_feedback`,
   background task after the recruiter's response is already sent).
9. Observability: structured JSON logging, per-request correlation IDs
   propagated across service boundaries, PII-safe logging audit (two
   pre-existing PII-risk log lines found and fixed).
10. LLM-generated match `explanation` — advisory, lazy, cached, structurally
    incapable of touching scoring (reads only `MatchResult` fields, never
    calls `scoring.py`, never writes to `MatchResult`).
11. VoiceHR reliability/security audit and fixes: the real interview-completion
    path wasn't notifying RWS at all (fixed — the single most important
    finding); a secret-exposure bug in the report-ready email `from` header;
    a shadowed `/health` route hiding real diagnostics; evaluator retry +
    prompt-injection hardening (transcript content wrapped and marked
    untrusted); a stale Gemini model name.
12. VoiceHR admin/report-management HTTP Basic auth (PII/transcripts/
    recordings/scores gated; interview-taking and shareable-report links
    deliberately left as unguessable-token bearer links, unchanged).
13. VoiceHR README rewritten from a stale pre-implementation stub to match
    the real implementation.
14. VoiceHR repository cleanup: removed the entire legacy local
    Whisper/Ollama/Kokoro/ElevenLabs/Vapi interview-conduct pipeline and its
    tests (Gemini Live is the only remaining interview-conduct
    implementation), removed a committable file that hard-coded a live
    Gemini key, removed stale pre-integration product docs.
15. VoiceHR architecture convergence: removed the Anthropic/Ollama evaluator
    branches (Gemini-only), removed dead frontend JS from the legacy pipeline
    removal, removed the already-fully-broken admin-initiated invite flow,
    fixed a real bug in the Gemini Live pipeline (the greeting content was
    built but never actually sent — one-line fix) that had been carried
    forward across sessions as "pre-existing, unrelated."
16. RWS: `GET /v1/candidates` + `GET /v1/applications` (global) to unblock the
    frontend's Candidates/Interviews pages.
17. VoiceHR reports proxy: RWS-facing report/recording routes so the browser
    never talks to VoiceHR directly; live testing surfaced VoiceHR's SQLite
    persistence bug for the first time (see #21).
18. RWS: `GET /v1/candidates/{id}/knowledge` proxy for the Candidate Detail
    page.
19. Knowledge graph design audit (chat-delivered proposal: Cytoscape.js, a
    dedicated `/graph` page, qualified/disqualified visual distinction,
    click-to-highlight) and its one backend prerequisite,
    `GET /v1/graph/candidates-jobs` (reads persisted `MatchResult`/`Job` rows
    only, both qualified and disqualified edges included).
20. Resume Intelligence: two cleanup passes (removed the dead standalone
    ResumeParser CLI tool; migrated structured extraction off local Ollama
    onto Gemini, with a deterministic post-processing/normalization layer)
    plus the Resume Intelligence → Second Brain `CandidateSummary` push
    (closing the "candidate has no path to becoming searchable" gap), a final
    hardening pass (`.doc` support removed, error-semantics audit, README
    rewrite), and an explicit upload-auth-boundary decision (`POST
    /v1/resumes` requires the service key directly on Resume Intelligence;
    RWS's own upload endpoint requires the recruiter JWT and forwards with
    the service key).
21. VoiceHR SQLite persistence hardening: `DB_PATH` moved from a bare
    `voicehr.db` (outside the persisted volume, silently reset on every
    container recreate) to `storage/voicehr.db` (inside it) — a one-line fix,
    verified live via a real `docker compose down`/`up` persistence test.
22. RWS production serving: the frontend's built `dist/` is served
    same-origin by RWS's own FastAPI app (guarded so its absence never breaks
    a backend-only dev session), via a real two-stage Docker build (Node
    build stage, Python runtime stage with no Node in it).
23. **Frontend**: the entire React recruiter frontend — every page, the 2D
    Cytoscape knowledge graph, the V2 dark "command center" visual redesign,
    and an explicit optional 3D graph mode — was built and repeatedly
    real-browser-verified across many sessions tracked entirely in
    `FRONTEND_HANDOFF.md`. Not renarrated here; that file is authoritative
    for all of it.
24. **Final production/demo readiness audit**: whole-system live
    verification, one real fix (container restart policies), verdict
    **DEMO READY** — see "Demo readiness" above for the full current record.
25. **VoiceHR autonomous real-time candidate interview — audited, not
    rebuilt.** A detailed spec for this exact feature (candidate-only
    real-time interview: emailed token link → candidate-only page → mic
    permission → autonomous Gemini Live interview → completion → evaluation →
    signed webhook → admin report) was requested; inspection found it already
    fully implemented from earlier sessions (interview.html's consent/mic-
    test/live-orb/progress/reconnect/completion states,
    `backend/services/gemini_live.py`'s server-side-only Gemini Live session,
    `backend/api/deps.py`'s expiry/single-use/staleness-recovery logic,
    `backend/api/routes/invite.py`'s idempotency key), so nothing was rebuilt
    — per `CLAUDE.md`'s "inspect before implementing, don't recreate what
    exists." Live-verified this session: invalid/expired/already-completed
    tokens correctly rejected (404/410/409), a freshly invited candidate link
    reached the live WebSocket and exchanged real audio frames, the
    candidate-only architecture boundary holds by construction (zero
    WebSocket code anywhere in the RWS frontend), and the browser never
    contacts any Gemini/Google host directly. This same pass is what
    surfaced the VoiceHR Gemini-access-denial finding above — a real,
    external issue, not a defect in this feature.

26. **Job-specific VoiceHR language eligibility gate**: a deterministic,
    RWS-owned second-stage screening signal on top of the already-built
    autonomous interview flow (milestone 25) — `jobs`/`applications` gain an
    optional, paired `(required_language, min_language_cefr)` (migration
    0006), snapshotted onto each `Application` at selection time;
    `language_gate.py`'s pure `compute_language_gate()` compares that
    snapshot against VoiceHR's webhook-reported `(language, cefr_level)`
    (VoiceHR's own webhook payload gained the `language` field to make this
    possible — no VoiceHR schema/DB change needed, since the interview's
    language was already tracked); `accept_application` enforces it (422 when
    `pending`/`failed`/`needs_review`), `reject_application` never does. New
    `PATCH /v1/jobs/{job_id}/language-requirement` endpoint, new Job-creation
    fields, and new frontend UI (job form, Job Detail display/edit,
    Applications' language-screening column with a blocked/tooltipped Accept
    button, Report Detail's Required/VoiceHR/Result summary — see
    `FRONTEND_HANDOFF.md`). One real bug found and fixed during real-browser
    verification: a missing assessed language rendered as the literal text
    "null" next to a present CEFR level on Report Detail — fixed to omit it
    cleanly. Verified: 105 RWS + 141 VoiceHR + 138 repo-root unit tests, 88
    repo-root integration tests (real Postgres, inside the container) all
    green; a full live cycle (real job creation, select/snapshot,
    invite-language-override, signed webhook, blocked/allowed accept, always-
    allowed reject, duplicate-webhook no-op, live requirement edit not
    retroactively changing an existing application) exercised against the
    running stack; real-browser confirmation of every new UI state. Discovered
    along the way: a second, previously-undocumented repo-root test suite
    (`tests/unit/`, `tests/integration/`) exists and is real, current coverage
    — see "Current state" above.

27. **Automatic VoiceHR invitation email delivery**: `POST /api/invite`
    (`backend/api/routes/invite.py`: `create_invite`) now emails the
    candidate their secure interview link itself, synchronously, reusing
    (not duplicating) the previously-dead `_send_email`/`_get_invitation_content`
    helpers in `interview.py` via a new shared `deliver_invitation_email()` —
    also now the implementation behind the previously-unreachable explicit
    resend route, `POST /interview/{token}/invite`. New, minimal SQLite
    columns on `interviews` (`job_title`, `invite_email_sent_at`,
    `invite_email_error`) are exactly what make the idempotency contract
    work: a duplicate `POST /api/invite` on the same `Idempotency-Key` skips
    resending once delivery has actually succeeded, and retries delivery on
    the *same* interview (never a new one) if it previously failed. Email
    success is now literally what makes an invite succeed — a failure
    returns a non-2xx response that RWS already turns into "never mark this
    application invited," with no RWS-side code change required at all.
    Two real bugs fixed while activating this previously-dead code: `_send_email`
    built its `from` address out of `admin_email` (a recipient setting) instead
    of `resend_from_address` (the correct sender setting mailer.py already used
    for the other outbound email), and it had no timeout or transport-error
    handling at all. One new required infrastructure fix: `create_invite`
    becoming an `async def` (needed to `await` email delivery) surfaced a
    latent FastAPI + sync-sqlite3 cross-thread bug — fixed via
    `check_same_thread=False` on the one `sqlite3.connect()` call this
    matters for (`backend/api/deps.py`'s `get_db`, and the test suite's own
    override of it). Verified: 154 VoiceHR tests (+13), 105 RWS tests
    (unchanged count, strengthened), 88 repo-root RWS integration tests, all
    green; a full live failure-and-retry cycle against the real running stack
    with no `RESEND_API_KEY` configured (see Known Limitations) — clean `502`,
    interview still created but not double-created on retry, confirmed in a
    real browser that the recruiter sees a clear error and nothing is
    silently marked invited. The success path (a real email actually
    arriving) is covered only by mocked unit tests this session, not live —
    no real Resend credential exists in this environment. Also discovered,
    not caused by this feature: running `tests/integration/test_recruitment_workflow.py`
    against the live container's Postgres wiped every job/application/match
    row in the database (see "Current state" and Known Limitations) — a
    process mistake, not a code defect, but a real one.

28. **Test-database safety + demo data recovery**: found and fixed the real
    cause of milestone 27's session's own "found, not caused" incident — both
    `conftest_recruitment_workflow.py`'s `truncate_recruitment_tables` and an
    identical unconditional-`TRUNCATE`-on-import pattern in
    `conftest_second_brain.py`'s `truncate_candidate_knowledge` (same
    landmine, different file, found while fixing the first one) now call a
    new shared guard, `tests/integration/db_safety.py`'s
    `resolve_test_database_url()`, instead of reading `DATABASE_URL` with a
    `localhost` fallback. It fails closed (raises before any query runs)
    unless `TEST_DATABASE_URL` is explicitly set, names something other than
    the real database (`company_second_brain`, denylisted), and contains
    `"test"` in its name. Provisioned a real, dedicated
    `company_second_brain_test` database on the same Postgres instance (same
    3 schemas/roles, migrated to head) as this project's actual disposable
    integration-test target. 13 new pure-unit tests for the guard itself
    (`tests/integration/test_db_safety.py`). Then restored the RWS demo data
    the earlier incident destroyed via a new, explicit, idempotent,
    demo-only script (`services/recruitment-workflow/scripts/seed_demo_data.py`,
    refuses to run without `--yes`) — real `MatchResult`s via the real
    scoring pipeline (reusing an already-existing job to avoid a Gemini call
    entirely, after an earlier attempt at a fresh job discovered Gemini's own
    JD-structuring is sensitive enough to phrasing that a more "natural"
    description produced `required_skills` nothing in the existing candidate
    pool could satisfy — a real, if minor, finding), real `Application`s via
    the real select endpoint, and one application deliberately reconnected to
    VoiceHR's one surviving completed interview/report by relinking VoiceHR's
    own `external_reference_id` and replaying RWS's real webhook endpoint
    with VoiceHR's actual stored report data — never a fabricated assessment,
    and never a call to the email-sending invite endpoint. Verified: the
    original incident's exact trigger (no `TEST_DATABASE_URL`, run inside the
    live container) now fails at conftest import with zero queries issued;
    the suite passes fully (88 tests) against the dedicated test database;
    direct `psql` row counts on the real demo database are bit-for-bit
    identical before and after; the seed script is idempotent (verified by
    running it twice) and sent no VoiceHR email (verified by construction —
    it never calls the endpoint that could). Also documented, not caused
    here: Gemini's free-tier daily quota is exhausted again as of this
    session (real `429`s, clean `502` surfaced, no partial data), and the one
    reconnected report's own `evaluation_status` is `"failed"` (a genuine,
    unmodified leftover from an earlier session's retry attempt hitting the
    still-open Gemini-access-denial finding, not something this session
    introduced) — both are called out explicitly under "Demo readiness" so
    neither surprises a presenter live.

---

## [NEXT]

**VoiceHR invitation delivery UX + explicit resend/recovery flow.** Not
started. Scoped by an earlier session's own explicit instruction not to
build a large delivery subsystem up front — milestone 27 deliberately
shipped only the minimum needed for automatic delivery to be correct and
safe to retry (`invite_email_sent_at`/`invite_email_error`, the shared
`deliver_invitation_email`, the existing explicit-resend route wired to use
it). What it did *not* build, and what this next pass should look at:
whether/how a recruiter should be able to see that a specific invite's email
delivery failed (today it's visible only as a `502` at the moment of the
click — there's no persisted-state indicator on the Applications page
afterward, e.g. after a page reload), whether the existing `POST
/interview/{token}/invite` explicit-resend route should be exposed through
the recruiter frontend at all (or through a different mechanism), and
whether `invite_email_error` needs to be surfaced anywhere recruiter-facing
(currently VoiceHR-internal-only, by design). Inspect the current
`ApplicationResponse`/`ApplicationsPage` shape first — there is no
`invite_email_status` field on that response today, so this may need one,
following the same "add only if actually required" discipline milestone 27
itself used. **One environment fact still blocks a full manual walkthrough
of the underlying feature**, independent of this next task's own scope:
`RESEND_API_KEY` needs a real value before a live invite email can actually
be observed arriving (see Known Limitations / Demo readiness above) — the
demo data itself no longer needs regenerating, that part is done.
