# Company Second Brain — Implementation Handoff

Cross-service source of truth: backend architecture, live API surface, security
boundaries, known limitations, and demo readiness. Frontend-only implementation
detail (routes, pages, components, visual design) lives in `FRONTEND_HANDOFF.md`
instead — this file never duplicates it, only references it (per the two files'
own long-standing division of labor).

**Project status: feature-complete; the interview-rounds feature is now
audited end to end, with one small frontend-only accessibility item left**
(see `[NEXT]` at the end). Every backend service, the full recruiter frontend, and
the 2D/3D knowledge graph are implemented and passed a final whole-system
readiness audit (see "Demo readiness" below). A dedicated audit of VoiceHR's
autonomous real-time candidate interview confirmed it was already fully built
in a prior session and re-verified its architecture live this session — see
milestone 25 and the Known Limitations entry below for a real, external
(non-code) access issue that audit surfaced. A job-specific VoiceHR language
eligibility gate was then implemented as a deterministic, RWS-owned
second-stage screening signal on top of that interview flow (milestone 26),
VoiceHR was made to email candidates their interview link automatically
(milestone 27), the integration-test fixture that had accidentally wiped the
live demo database was hardened and the demo data it destroyed was restored
(milestone 28), an explicit resend/recovery flow was added on top of that
automatic delivery — a "Retry" UX for a failed initial send and a distinct
"Resend invitation" action once already invited, both reusing the same
VoiceHR interview/token (milestone 29), the autonomous interview session
itself was hardened against normal browser/network/audio failures — a
bounded, consistent reconnect budget, transcript preservation across a
reconnect, an exactly-once completion guard, a single-owner WebSocket lock
per interview, and clearer candidate-facing UI states (milestone 30) — and
most recently the interview's own content quality was overhauled: a real
five-dimension rubric (interaction split out from comprehension,
pronunciation honestly stopped being fabricated from text-only evidence), a
five-section adaptive-but-bounded question structure, language-purity/
never-reveal-the-result/prompt-injection-resistance instructions, a genuine
A1-C2 CEFR range, and a new `insufficient_evidence` outcome that stops a
short/thin interview from ever producing a real, comparable (and possibly
wrong) pass/fail verdict (milestone 31) — and most recently that rubric was
put to use and explained: a deterministic golden-case benchmark for the
evaluator (runnable standalone, no live Gemini needed), a hardened
assessment contract (pronunciation nulled unconditionally at read time, an
explicit `sufficient_evidence` field), and a full Report Detail redesign so
a recruiter can see *why* a candidate received their language result
(Language Screening → Assessment Quality → Language Profile →
Strengths/Concerns → Transcript) without reading the raw transcript or
evaluator JSON, plus a real duplicate-label bug fixed along the way
(milestone 32). Most recently, a design-only pass (no code) defined the
post-VoiceHR recruitment stage — what happens for a candidate once the
language gate clears — as a parallel `interview_rounds` signal beside
`Application`'s existing state machine, deliberately not a new set of
`Application.status` values (the same relationship the language gate itself
already has to that state machine); the first backend slice from that design
— the table, the model, and creation — landed in milestone 33, the
lifecycle actions (reschedule/complete/cancel) in milestone 34, and the
feature's frontend counterpart landed and was audited end to end as a
whole workflow across `FRONTEND_HANDOFF.md`'s milestones 18-20 — see that
file for the full audit findings.

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
**every real `POST /v1/applications/{id}/invite` or `.../resend-invite` call
still fails with a clean 502** — the seed script worked around this (it never
calls that endpoint), but a live "Invite"/"Resend invitation" click in front
of a jury still cannot succeed until a real Resend key is configured (see
Known Limitations). This is precisely the failure path milestone 29's new
"Invitation not sent" / Retry UX was built and live-verified against.

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
| Recruitment Workflow (RWS) | 135 passed | `services/recruitment-workflow/tests/` — mocked-DB unit/route tests, host or in-container. +12 this session (milestone 34): reschedule/complete/cancel success, 409-wrong-state, 404-unknown-round, 404-cross-application, and complete's 422-before-the-db cases, all in `test_interview_rounds.py`. |
| VoiceHR | 205 passed | **Host only** — its Docker image deliberately ships without `pytest` (lean production image). +11 this session (milestone 32): 5 new `test_report.py` assessment-contract tests, `test_interview_quality.py` restructured onto the new `golden_cases.py`/`benchmark_runner.py` split (9→15 tests: 13 golden cases + 2 whole-benchmark checks). Also runnable standalone, no pytest: `cd services/voicehr && python -m tests.benchmark_runner`. |
| Second Brain | none | No `tests/` directory exists. A pre-existing, accepted gap — its one real integration (Resume Intelligence → Second Brain ingest/embed) is instead covered by a live end-to-end smoke test (candidate ingested, embedded, retrieved, PII boundary confirmed via direct `psql`). |

**A second test suite exists at the repo root** (`tests/unit/`,
`tests/integration/`) — discovered two sessions ago, not new work.
`tests/unit/` (139 passed; +1 this session, milestone 31's new
`InterviewAssessment.evaluation_status == "insufficient_evidence"` schema
test) exercises `shared/` modules (schemas, scoring, auth, LLM interface,
observability, HTTP client) directly, no DB.
`tests/integration/` runs each service's real FastAPI app against a **real
Postgres** — see the test-isolation invariant immediately below for what
changed about how this now has to be run. `tests/integration/test_db_safety.py`
(13 passed, new this session) is a pure unit test of the guard itself, no DB
needed. `test_resume_intelligence.py`/`test_second_brain.py` exist too but
were not run this session (untouched by this session's changes).
`test_recruitment_workflow.py` is 96 passed (was 88 before milestone 29's +8
for the explicit resend flow) — re-confirmed green this session at the same
count, since milestone 30 touched only VoiceHR, which this suite doesn't
cover. **`tests/integration/test_interview_rounds_integration.py` (17
passed, milestones 33-34 combined)** — real Postgres, run against the
disposable `company_second_brain_test` database per the test-isolation
invariant below, never the live demo database. Covers what the RWS-local
monkeypatched suite structurally cannot: `recruitment.interview_rounds`'s DB
`CHECK` constraints are actually enforced (invalid `round_type`/`status`/
`outcome`, and both directions of the paired outcome-only-when-completed
constraint), a real end-to-end create → persist → embedded-on-read round
trip through the real API, and (milestone 34) real reschedule/complete/
cancel round trips including a real cross-application 404 and a real
409-after-a-real-terminal-transition.

**New this session (milestone 32): the recruiter frontend has its first-ever
test file.** This project has never introduced a JS test framework (Jest/
Vitest/React Testing Library) across the whole V2 frontend build-out,
relying on real-browser Playwright verification instead — that stays true
here too for anything involving actual rendering. `src/constants/
language.test.js` (13 passed) covers only the *pure logic* pieces
(label/tone consistency, exact wording, score-band boundaries, null-safety)
via Node's own built-in test runner, zero new dependencies or config:
`cd services/recruitment-workflow/frontend && node --test
src/constants/language.test.js`.

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
- **New this session (milestone 33): the post-VoiceHR interview stage is a
  parallel signal beside `Application`'s state machine, not a new set of
  `Application.status` values** — the exact same relationship the language
  gate already has to it, deliberately reused rather than re-invented.
  `Application.status` stays `selected`/`invited`/`interviewed`/`accepted`/
  `rejected`, unchanged; `recruitment.interview_rounds` is a separate,
  one-to-many table (`db.InterviewRound`), and `POST .../interview-rounds`
  never writes to `Application.status` at all. Creating a round is gated on
  two checks, both reusing existing logic rather than duplicating it: the
  application must not already be `accepted`/`rejected`, and
  `language_gate.status` must not be blocking (the exact same
  `_language_gate_info`/`BLOCKING_STATUSES` `accept_application` already
  uses) — this stage exists specifically "for candidates who pass the
  language gate," enforced at the API boundary. A round's own `outcome`
  (`pass`/`fail`/`needs_review`) is informational only: unlike the language
  gate, it does **not** block `accept`/`reject` — a technical/managerial
  interview is a human judgment call the recruiter already made, and
  auto-blocking their own final decision on a verdict they themselves
  recorded would work against "recruiter remains final decision-maker," the
  one constraint this design was explicitly built to preserve.
  `interviewer_name` is free text, not a foreign key: this project has
  exactly one seeded recruiter account and no interviewer/user directory
  (see CLAUDE.md's non-negotiable decisions) — inventing one would be
  speculative infrastructure this project's own code-style rules warn
  against.
- **New this session (milestone 31): the evaluator/RWS-gate responsibility
  split is now explicit and load-bearing, not just implicit.** VoiceHR's
  evaluator (Gemini, `backend/services/evaluator.py`) is the only place any
  LLM judgment enters this feature at all, and its output is strictly
  advisory evidence, never a decision: it returns
  `{cefr_level, confidence, dimensions: {comprehension_relevance,
  fluency_coherence, grammar_language_control, vocabulary_range,
  interaction_spontaneity}, strengths, concerns, evidence}` (pronunciation
  omitted — see Known limitations) or, when the transcript doesn't contain
  enough usable language, `cefr_level: null` with
  `evaluation_status: "insufficient_evidence"`. It never sees, and cannot
  express, "passed"/"failed"/"eligible" — those words do not exist in its
  vocabulary. RWS alone turns "assessed CEFR" into a decision, deterministically:
  `language_gate.py`'s `compute_language_gate()` (unchanged by this pass,
  see below) is the only place `CEFR_RANK[assessed] >= CEFR_RANK[required]`
  is ever evaluated, and it runs on plain data (two strings in, one status
  out) with no model call of any kind. The same discipline `CLAUDE.md`
  already requires for resume matching ("the LLM never determines...
  threshold comparisons") extends cleanly to this second, later gate.
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
- **New this session (milestone 29): a third, `external_reference_id`-keyed
  explicit resend route** — `POST /api/rws/interviews/{external_reference_id}/resend-invite`
  (`report.py`, service-key gated, alongside the report/recording routes it
  sits next to) — exists specifically because the token-keyed route above is
  unauthenticated and RWS never stores that token, so RWS couldn't call it
  directly without either storing a token (never done) or adding this new
  route. It also funnels through the same `deliver_invitation_email`, always
  attempts delivery (ignoring `invite_email_sent_at`, like the token-keyed
  route), and never creates a new interview or accepts an `Idempotency-Key`.
  RWS's own `POST /v1/applications/{id}/resend-invite` is its only caller,
  only from `invited`, with an in-memory per-application cooldown against
  double-click spam (not a persisted field). The "Invitation not sent"/Retry
  UI state on a *failed initial* send is deliberately ephemeral frontend
  state only, never persisted to `Application` — see milestone 29 below for
  why.
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
- **New this session (milestone 30): the autonomous interview session is
  robust to normal browser/network/audio failures without changing this
  architecture.** VoiceHR runs one uvicorn worker, one process (its
  Dockerfile has no `--workers` flag) — an in-process `asyncio.Lock` per
  interview token (`api/routes/session.py`) is therefore a real, sufficient
  single-owner guard, not a stand-in for a distributed one; held for the
  entire WebSocket handler body (through `process_interview`, not released
  early), it makes two simultaneous WebSocket connections for one interview
  structurally impossible to both bridge a live Gemini session. A single
  shared `MAX_INTERVIEW_RECONNECTS` (`database.py`, currently `2`) bounds
  every automatic recovery path identically — the WebSocket-disconnect
  handler's own reset and the separate stale-active-session check in
  `api/deps.py`'s `validate_interview_start` used to be independent and
  inconsistently capped; they now share one budget. `interviews.
  pending_transcript`/`pending_duration_seconds` (new nullable SQLite
  columns) carry a recoverably-dropped attempt's transcript/elapsed time
  into the next attempt (`resume_transcript`/`resume_duration_seconds` on
  `run_sarah_pipeline`/`run_gemini_live_pipeline`) — Gemini Live itself has
  no memory across a fresh `connect()` call, so the greeting/task-1 opening
  genuinely restarts (an honest reconnect, never a faked mid-conversation
  resume), but the transcript actually evaluated covers the whole
  conversation. `interviews.finalized_at` (new nullable column) is what
  `process_interview` claims atomically, with no `await` before the claim
  UPDATE, before ever calling `evaluate()` or sending the completion
  webhook — the actual exactly-once guarantee, independent of (and a
  backstop for) the WebSocket lock above; it does not touch or duplicate
  RWS's own `delivery_id`-based webhook dedup, which remains exactly as it
  was.
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
| `POST /v1/applications/{id}/invite` | JWT | Pre-flight Resume Intelligence contact lookup (422 if none, before ever calling VoiceHR); idempotent retry via `{application_id}:{attempt}`. If the application has a snapshotted language requirement, VoiceHR is invited in that language regardless of the request body's own `language` field. Also passes the job's `title` (cosmetic, for VoiceHR's candidate email). **Success now means VoiceHR actually emailed the candidate** — a `502` here can mean either VoiceHR is unreachable or the email failed to send; either way the application is never marked `invited`. Callable from `selected` (first send) or `invited` (still callable directly, though the frontend now only surfaces this button while `selected` — see below); a failed attempt leaves `invite_attempt_number` un-persisted, so the very next call recomputes the same attempt number/idempotency key, which is what makes a frontend "Retry" click reuse VoiceHR's already-created interview instead of creating a duplicate one. |
| `POST /v1/applications/{id}/resend-invite` | JWT | **New this session.** Explicit resend, only callable while `invited` (409 otherwise). Calls VoiceHR's new `POST /api/rws/interviews/{id}/resend-invite`, which reuses the exact interview/token created at invite time via the same `deliver_invitation_email` function every other send path already shares — never creates a new interview, never accepts/needs an `Idempotency-Key`, never looks up or persists the candidate's contact (VoiceHR already has it). A failure (`502`) never reverts the application away from `invited`. A short in-memory per-application cooldown (10s, module-level dict, no Redis) returns `429` on a rapid repeat click — a UX safeguard, not a persisted field or new workflow status. |
| `POST /v1/applications/{id}/accept` | JWT | Atomic conditional `UPDATE`; schedules a background Second Brain feedback write-back after the response is already sent. **422 if the language eligibility gate is `pending`/`failed`/`needs_review`** — the response body names which and the required-vs-assessed values. |
| `POST /v1/applications/{id}/reject` | JWT | Same state machine, background write-back — **never gated by the language eligibility gate**; a recruiter can always decline. |
| `POST /v1/applications/{id}/interview-rounds` | JWT | **Milestone 33.** Schedules a post-VoiceHR technical/managerial interview round — the stage after the language gate clears. 422 if the application is already `accepted`/`rejected`, or if `language_gate.status` is `pending`/`failed`/`needs_review` (same `BLOCKING_STATUSES` check `accept` already uses). Creates one `recruitment.interview_rounds` row (`scheduled`, no outcome yet) — never touches `Application.status`, which stays exactly `selected`/`invited`/`interviewed`/`accepted`/`rejected` as before; this is a parallel signal, not a new state-machine value, the same relationship the language gate itself has to that state machine. `interviewer_name` is free text, not a foreign key — this project has exactly one seeded recruiter account and no interviewer/user directory. |
| `POST /v1/applications/{id}/interview-rounds/{round_id}/reschedule` | JWT | **New this session (milestone 34).** Updates only `scheduled_at`; 409 if the round is not currently `scheduled` (names its actual status), 404 if the round doesn't exist or belongs to a different `application_id` than the URL's. |
| `POST /v1/applications/{id}/interview-rounds/{round_id}/complete` | JWT | **New this session (milestone 34).** Sets `outcome` (required — `pass`/`fail`/`needs_review`, 422 if missing/invalid, checked before ever reaching the DB) and optional `notes`, `status → completed`; only from `scheduled` (409/404 as above). Omitting `notes` preserves whatever was set at scheduling time rather than clearing it. |
| `POST /v1/applications/{id}/interview-rounds/{round_id}/cancel` | JWT | **New this session (milestone 34).** `status → cancelled`, no body; only from `scheduled` (409/404 as above). Terminal — no un-cancel. |
| `GET /v1/jobs/{job_id}/applications` | JWT | Job-scoped. Every row's `language_gate` object (`status`, `required_language`, `required_cefr`, `assessed_language`, `assessed_cefr`) is computed fresh on every read, never a stored column. **New this session**: every row also carries `interview_rounds` (a list, possibly empty), fetched fresh per application the same way `language_gate` is. |
| `GET /v1/applications` | JWT | Global, unscoped, adds `job_title` and the same `language_gate`/`interview_rounds` fields. |
| `GET /v1/interviews/{id}/report` | JWT | Proxies VoiceHR; rewrites `recording` to a same-origin RWS URL — browser never sees VoiceHR's host or the service key. |
| `GET /v1/interviews/{id}/recording` | JWT | Relays raw audio bytes; no disk/DB write. |
| `POST /v1/webhooks/voicehr` | HMAC (`X-Signature`) | Inserts into `interview_webhook_events` (dedup by `delivery_id`) before any state mutation; unresolvable/malformed references return `200` no-op, never a retry-storming error. `assessment` now also carries `language` (the interview's own conducted language) alongside the pre-existing `cefr_level` — both feed the language eligibility gate. **New this session (milestone 31)**: `assessment.evaluation_status` accepts a fourth value, `"insufficient_evidence"` (`shared/schemas.InterviewAssessment`) — sent with `cefr_level: null`, which `language_gate.py` already mapped to `needs_review` before this addition; no change to that file's own threshold logic. |

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
| `GET /api/rws/interviews/{external_reference_id}/report` \| `/recording` \| `POST .../resend-invite` | service key — the RWS-facing routes RWS's own proxy endpoints call |
| `GET /api/report/{report_token}`, `GET /report` | unguessable-token bearer link, candidate/shareable |
| `GET /api/admin/reports`, `GET /admin/reports`, `POST /api/admin/reports/{token}/retry`, `GET /recordings/{filename}` | HTTP Basic |
| `GET /health`, `GET /health/detail` | none |

`POST /api/invite` now also emails the candidate their interview link
automatically, synchronously, as part of the same call — see "Architecture /
invariants" above for the full idempotency/failure-semantics contract. The
previously-dead `POST /interview/{token}/invite` (unauthenticated, token-only)
shares that same delivery implementation and remains an explicit,
always-resends primitive kept for a future admin-UI need — still not called
from anywhere in the current architecture.

**New this session**: `POST /api/rws/interviews/{external_reference_id}/resend-invite`
(`report.py`, service-key gated, alongside the report/recording routes it sits
next to) is what RWS's own new resend endpoint calls instead — the
token-keyed route above can't be, since it's unauthenticated and keyed by a
raw token RWS never stores. This new route resolves the interview by
`external_reference_id` (RWS's own `application_id`, already used by the
report/recording routes) and always attempts delivery via the same shared
`deliver_invitation_email` function, on the interview's existing row — no new
interview, no new token, no `Idempotency-Key`. Returns only `{"status": "sent"}`;
never the token/link.

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
  instead. **Re-confirmed this session (milestone 32)**: the two console
  404s observed during a real-browser consistency pass across
  Applications/Interviews/Reports were traced directly to this exact
  candidate's identity lookup, not a new regression from anything touched
  this session.
- **RESOLVED this session (milestone 27): "no in-product way to retrieve a
  VoiceHR interview link."** VoiceHR now emails the candidate their secure
  link automatically as part of `POST /api/invite` itself — see "Architecture
  / invariants" above. This is no longer a gap, but see the very next entry
  for what currently blocks it from working in this environment.
- **CRITICAL, demo-blocking as of milestone 27: `RESEND_API_KEY` is empty in
  VoiceHR's `.env`.** Automatic invitation email delivery (milestone 27) is
  now what determines whether `POST /api/invite` succeeds at all — with no
  key configured, `_send_email` immediately raises, and **every real
  "Invite"/"Retry"/"Resend invitation" click in the recruiter UI currently
  fails with a clean 502**, verified live this and the milestone-29 session
  (the new explicit resend endpoint hits the exact same `_send_email`
  failure). This is a behavior change from before milestone 27, when a
  missing key was harmless (no email was ever attempted). A real Resend API
  key must be configured before "Invite"/"Resend invitation" can be
  demonstrated live succeeding. **Not
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
- **New this session (milestone 30): an interview that exhausts its
  `MAX_INTERVIEW_RECONNECTS` budget while genuinely abandoned (the candidate
  never returns at all) stays `active` forever, with no automatic terminal
  report and no webhook ever sent to RWS.** A deliberate, minimal-scope
  tradeoff, not an oversight: auto-finalizing it would need either a
  scheduler (explicitly out of scope — "no scheduler unless necessary") or
  forcing an evaluation of a possibly-empty/nonsensical transcript just to
  produce *some* terminal status. The chosen policy instead satisfies the
  actual requirement ("abandoned sessions must never appear as successful
  assessments") the simplest possible way: an exhausted-budget interview is
  simply never resumable and never evaluated, so it can never look
  successful — RWS's own `Application` just stays `invited` indefinitely for
  that candidate, which a recruiter can already act on manually (accept/
  reject are both allowed from `invited`). If a real deployment ever needs
  an automatic "this candidate never came back" signal to RWS, that would be
  the next incremental step, not a sign this policy was wrong at this
  project's scale.
- **New this session (milestone 31): pronunciation is permanently
  unassessed, by design, not a gap to close later.** VoiceHR's evaluator
  only ever receives the interview's text transcript — never audio — so
  `pronunciation_score` is now always `null` (`AssessmentPanel` already
  renders this as "—", same as any other missing score). Assessing
  pronunciation for real would mean sending recorded audio to an LLM for
  acoustic analysis, a genuine new capability, not a prompt/rubric tweak —
  explicitly out of this task's scope ("do not change... provider
  architecture"). If a future session adds this, it belongs alongside a
  real audio-analysis provider call, not a guessed number from text alone.
- **New this session (milestone 31): the new rubric/adaptive-questioning
  prompt and the new `insufficient_evidence` outcome have never been
  observed producing a real, completed evaluation.** VoiceHR's Gemini
  project is still denied access by Google (see the milestone-25 finding
  above, re-confirmed unchanged this session) — every live check this
  session could only reach the connect/evaluate *failure* path, honestly.
  The new prompt was confirmed to build correctly and reach that same
  pre-existing failure (not a new one), and the new evaluator logic is
  covered thoroughly by the mocked golden-case benchmark
  (`tests/test_interview_quality.py`) and prompt-content tests
  (`tests/test_interview_prompt.py`) — but no real candidate has ever seen
  the new Section E adaptive follow-up, and no real transcript has ever
  produced the new `interaction_spontaneity` score or a genuine
  `insufficient_evidence` webhook. The next session with real Gemini access
  should do exactly one controlled live interview and inspect the result
  before trusting this rubric's real-world behavior beyond what the mocked
  tests already prove.

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
conducted live regardless of the Resend key. → **optional: Schedule a
technical or managerial interview round on this exact application, then
Complete it** — confirmed live this session that application
`a2769e7b-...` is already schedule-ready with zero setup (`status:
"interviewed"`, `language_gate.status: "not_required"`, no existing
rounds), so this costs no Gemini/Resend quota and needs no workaround; a
good moment to show the post-VoiceHR human-interview stage without
implying the round's outcome affects the decision that follows (frame it
as advisory input the recruiter weighs, not a gate). → Accept (the round's
outcome, whatever it was set to, does not block this — the language gate
still would, unchanged) → Reports → Knowledge Graph 2D (search + filter)
→ 3D. **Not yet done**: no round has actually been scheduled on this
application as of this session — the option above is a recommendation for
whoever runs the demo to act on beforehand, not something scheduled during
this audit (touching the curated demo application beyond what was asked
wasn't this session's call to make).

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
7b. *If a technical/managerial round comes back "Fail," does the recruiter
   still have to reject the candidate?* — No, deliberately not. Unlike the
   language gate (a deterministic compliance-style check that really does
   block acceptance), a round's outcome is human judgment the recruiter
   already recorded themselves — it's advisory, shown alongside the
   decision, never enforced by the system. Auto-blocking a recruiter's own
   final call on a verdict they typed in themselves would work against the
   one rule that hasn't changed since day one: the recruiter is always the
   final decision-maker. Verified live (post-VoiceHR audit, milestone 20 in
   `FRONTEND_HANDOFF.md`): accepted an application with one round marked
   "Pass" and a second cancelled outcome sitting right next to it — Accept
   was never disabled by either.
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

29. **VoiceHR invitation delivery UX + explicit resend/recovery flow**:
    closes the gap milestone 27 deliberately left open. Added exactly one new
    endpoint per service, both reusing existing mechanisms rather than
    building new ones: VoiceHR's `POST /api/rws/interviews/{external_reference_id}/resend-invite`
    (`report.py`, service-key gated, alongside the report/recording routes)
    resolves the interview by `external_reference_id` and always attempts
    delivery via the same shared `deliver_invitation_email` used by every
    other send path — never a new interview/token, never an
    `Idempotency-Key`; RWS's `POST /v1/applications/{id}/resend-invite`
    (JWT-gated, `main.py`) is its thin proxy, only callable while `invited`
    (409 otherwise), with an in-memory 10s-per-application cooldown (429 on a
    rapid repeat, no persisted field, no Redis) guarding against double-click
    spam. Neither route looks up or persists the candidate's contact — VoiceHR
    already has it from the initial invite, and RWS still never stores
    candidate email.

    The "Invitation not sent" / Retry state on `ApplicationsPage` is
    deliberately **ephemeral, component-scoped React state only**
    (`lastInviteFailed`), not a new backend field or workflow status — RWS's
    own `invite_attempt_number` is still only persisted after a successful
    VoiceHR call (unchanged from milestone 27), so a failed attempt leaves no
    persisted signal to distinguish "never attempted" from "attempted and
    failed" beyond the existing `502`; a page reload correctly reverts to a
    plain "Invite" button, which is the right behavior (reload means
    re-reading real backend state, and this label was never part of it). The
    existing `POST /v1/applications/{id}/invite` is completely unchanged and
    still both creates the first interview and (while still `selected`)
    retries a previously failed attempt via its own existing idempotency key
    — "Retry" on the frontend is just that same call again, not a new
    endpoint. Frontend button matrix: `selected` (never attempted) → "Invite";
    `selected` (last attempt failed, ephemeral) → "Invitation not sent" +
    "Retry"; `invited` → "Invitation sent" + "Resend invitation" (new explicit
    resend). The old "Resend Invite" button (which called the *normal* invite
    endpoint from `invited`, creating a genuinely new attempt/interview) was
    removed from the UI in favor of this explicit resend — the backend
    endpoint it used to call is untouched and still accepts calls from
    `invited` if something else needed to invoke it.

    **Tests**: 6 new VoiceHR tests (`tests/test_report.py`, host-only) cover
    the new route's auth, 404, success-reuses-interview-never-leaks-token,
    sends-even-if-already-delivered, and failure→502. 6 new RWS unit tests
    (`tests/test_resend_invite.py`, no real Postgres, same
    monkeypatch-`main`/mock-`db` pattern as `test_language_requirement_workflow.py`)
    cover success/409/404/502/auth/rate-limit in isolation. 8 new
    repo-root integration tests (`tests/integration/test_recruitment_workflow.py`,
    real Postgres) cover: a failed initial send leaves the application
    `selected` with `invite_attempt_number` still `0`; a retry after failure
    reuses the exact same idempotency key; explicit resend sends
    intentionally and reuses the interview (no new `invite_candidate` call);
    resend failure leaves the application `invited`; resend on a `selected`
    application returns 409; resend on an unknown application returns 404;
    resend requires JWT not service key; and a second rapid resend click
    returns 429 with only one real call reaching VoiceHR. Combined with
    already-existing coverage (VoiceHR's own duplicate-idempotency-key test,
    RWS's missing-contact-422 and already-decided-409 tests), all ten
    scenarios this task's own spec called out are covered. Full suite counts
    this session: VoiceHR 160 passed (was 154), RWS unit 111 passed (was
    105), repo-root `tests/unit/` 138 passed (unchanged, not touched by this
    task), repo-root integration 96 passed (was 88).

    **Verified live** (with the DB-safety guard explicitly re-confirmed
    fail-closed first — ran the integration suite with no `TEST_DATABASE_URL`
    set, got `UnsafeTestDatabaseError` at conftest import with zero queries
    issued, then ran it correctly against `company_second_brain_test`):
    rebuilt and redeployed both the `recruitment-workflow` (frontend + backend)
    and `voicehr` images; in a real headless-Chromium session against the live
    stack, clicked "Invite" on a `selected` demo application with a real
    Resume Intelligence contact record and got a genuine (unmocked) `502`
    from VoiceHR's own `RESEND_API_KEY`-unset failure (`RESEND_API_KEY` is
    still unset in this environment — this was a real failure, not simulated);
    the "Invitation not sent"/"Retry" UI rendered correctly with the real
    error text in the banner; confirmed via direct Postgres/SQLite queries
    that the application stayed `selected` (`invite_attempt_number` still
    `0`) and that VoiceHR had created exactly one interview row for it
    (`invite_email_sent_at` still `NULL`); clicked "Retry" in the browser,
    got the same genuine `502` again, and confirmed via SQLite that still
    exactly one interview row exists for that `external_reference_id`, with
    the identical `token` and `idempotency_key` as before the retry — no
    duplicate interview was created. Did **not** attempt to fake or force a
    live successful send/resend or an `invited`-status "Resend invitation"
    screenshot (`RESEND_API_KEY` absence makes a real successful live send
    impossible right now, and manually forcing `status='invited'` in the DB
    would misrepresent an invite that never actually succeeded) — the
    successful-path and resend-specific UI logic are instead covered by the
    mocked-provider test suites above, which is what this task's own
    instructions called for. Confirmed via direct `psql` row counts
    (`jobs`/`applications`/`match_results`: 2/3/16, unchanged) that none of
    this session's real API calls or test runs touched the live demo data.

30. **VoiceHR autonomous interview session robustness**: audited the real
    session lifecycle (`api/deps.py`'s token/start validation,
    `api/routes/session.py`'s WebSocket handler, `services/gemini_live.py`'s
    Gemini Live bridge, `services/processing.py`'s completion path,
    `interview.html`'s candidate JS) before changing anything, and found
    three real, independent gaps rather than a single bug: (1) two
    *independent, inconsistent* recovery mechanisms for a dropped/stale
    session — `session.py`'s own WebSocket-disconnect handler capped its
    reset at `retry_count < 1`, but `deps.py`'s separate 30-second
    stale-active reactivation had no cap at all, silently overriding that
    "one retry" limit for almost any real-world reconnect (which is
    virtually always attempted more than 30 seconds after the original
    `/start`); (2) a reconnect discarded the entire transcript captured
    before the drop — `run_gemini_live_pipeline`'s `transcript` list was
    always freshly empty per WebSocket connection, so re-evaluating after a
    reconnect only ever saw the *last* leg's conversation; (3) no exactly-once
    guard on `process_interview` itself — its old check was a plain
    `SELECT` + later `UPDATE` with `evaluate()` (a real `await` point)
    in between, so two calls racing for the same interview would both pass
    the check, both evaluate, and both send a webhook with a *different*
    `delivery_id` each (`rws_client.py` mints a fresh one per call) — RWS's
    own delivery-id dedup cannot catch this, since from RWS's side these
    look like two independent, legitimate completions, not a retried
    duplicate.

    Fixed all three with small, DB-guard-first changes, keeping the exact
    same architecture (candidate-only token URL → VoiceHR WebSocket →
    Gemini Live → transcript/evaluation → signed RWS webhook; no recruiter
    live monitoring added, none was requested): a single shared
    `MAX_INTERVIEW_RECONNECTS = 2` (`database.py`) now bounds *both*
    recovery mechanisms identically (up to 2 automatic reconnects, 3
    attempts total, budget exhausted → the interview simply stays `active`
    and unreachable rather than looping forever or being auto-finalized —
    no scheduler, no forced evaluation of a possibly-nonsensical transcript,
    and it already satisfies "never looks like a successful assessment"
    since no report is ever generated for it); two new nullable SQLite
    columns, `pending_transcript`/`pending_duration_seconds`, persist
    whatever a recoverable disconnect (`browser_disconnect`/
    `gemini_live_error`) already captured, and the next WebSocket connect on
    the same token loads and forwards them into
    `run_sarah_pipeline`/`run_gemini_live_pipeline` as
    `resume_transcript`/`resume_duration_seconds`, seeding the new leg's
    transcript list and adding to its elapsed-time total — an honest
    reconnect, not a faked one: Gemini Live itself has no memory across a
    fresh `connect()` call, so Sarah's greeting/task-1 genuinely restarts,
    but the final transcript handed to evaluation/reporting covers the
    whole conversation, matching this task's own "show an honest
    recoverable state rather than pretending continuity" instruction; a
    third new column, `finalized_at`, is what `process_interview` now
    claims *before* calling `evaluate()` (`UPDATE ... WHERE finalized_at IS
    NULL AND status IN ('active','pending')`, checked via `rowcount`) —
    since this claim has no `await` before it, this project's single-
    process/single-event-loop deployment guarantees it always completes
    before a second concurrent call can even begin its own claim attempt,
    making finalization exactly-once without touching RWS's own
    delivery-id dedup at all (verified: a test that mocks `evaluate()` with
    a real `await asyncio.sleep()` and runs two `process_interview()` calls
    via `asyncio.gather` fails without the claim and passes with it —
    confirmed by literally reverting the claim and watching the test fail,
    then restoring it). Also added one new in-process guard,
    `api/routes/session.py`'s per-token `asyncio.Lock` (VoiceHR runs one
    uvicorn worker, one process — an in-process lock is a real, sufficient
    guard here, not a stand-in for a distributed one), held for the
    *entire* handler body including `process_interview` (not released
    early), so two simultaneous WebSocket connections for the same token
    can never independently start their own Gemini Live pipeline — the
    second is accepted just long enough to send a clear
    `{"type":"already_active"}` message, then closed. `finalized_at`'s
    claim remains the defense-in-depth backstop if the lock were ever
    bypassed. Frontend (`interview.html`, candidate-only, no admin/recruiter
    surface touched): added two new states the spec explicitly asked for —
    "Finishing up…" (`step-finalizing`, shown the instant the candidate
    clicks Finish, covering the real recording-upload wait that previously
    left them on a frozen screen with only a disabled button) and
    "Microphone problem" (`step-mic-lost`, triggered by a `track.onended`/
    `onmute` listener on the live mic track *or* a `getUserMedia` failure
    mid-interview, offering "Resume with microphone" which reuses the exact
    same tested reconnect path — deliberately not a bespoke recovery
    mechanism); the initial mic-permission-denied state gained a distinct,
    actionable message (permission denied vs. no device found) and a
    working "Try again" button that re-invokes `getUserMedia` in place,
    where before there was only static text and a permanently-disabled
    Start button; `startInterview()`'s failed-`/start` handling now reads
    and shows the backend's own `detail` message instead of one hardcoded
    generic string, so the new exhausted-budget message ("This interview
    could not be resumed after multiple attempts...") actually reaches the
    candidate (found live, mid-verification, exactly because the automated
    test suite can't see rendered browser text — see below); a stale
    "You can reconnect once" string (now inaccurate since the shared budget
    moved from 1 to 2) was corrected, and the two remaining purely-cosmetic
    client-side `retryCount` thresholds were aligned to the same shared
    value via one named constant, `MAX_CLIENT_RECONNECT_HINT` (the
    backend's own 409 remains the actual authority either way).

    **Tests** (deterministic, mocked Gemini/provider throughout — no real
    Gemini Live session in any test): 3 new `test_processing.py` tests
    (concurrent-calls-finalize-exactly-once — the decisive one, verified to
    fail without the fix; repeated-sequential-calls-run-once;
    pending-columns-cleared-on-finalize), 5 new `test_session.py` tests
    (completed-interview-cannot-be-resumed;
    recoverable-disconnect-persists-transcript-and-skips-process_interview;
    reconnect-forwards-pending-transcript-and-merges-the-result;
    disconnect-beyond-retry-budget-leaves-interview-active-not-looping;
    two-simultaneous-sockets-second-rejected — also verified to fail
    without the fix, by literally removing the lock and watching it fail),
    2 new `test_interview.py` tests (stale-session recovery within budget;
    stale-session recovery correctly refused once budget is exhausted, with
    the distinct message). VoiceHR: 170 passed (was 160). RWS/repo-root
    suites untouched by this task and confirmed still green (RWS unit: 111;
    repo-root `tests/unit/`: 138; repo-root integration
    `test_recruitment_workflow.py`: 96, run only after re-confirming the
    `TEST_DATABASE_URL` safety guard still fails closed with it unset).

    **Verified live**, against the real running stack (rebuilt/redeployed
    `voicehr` image), using VoiceHR's own genuine, still-open Gemini
    access-denial (`403`, see Known Limitations) as the real "upstream
    disconnect" this task needed anyway — never faked, and no claim is made
    here that real Gemini Live recovery works, only that VoiceHR's own
    robustness logic behaves correctly around a real upstream failure: (1)
    a real candidate session hit the real `403` three times in a row
    (fake-mic Chromium via `--use-fake-device-for-media-stream`); the first
    two correctly reset to `pending` with `retry_count` 1 then 2 and an
    empty `pending_transcript` (no conversation had started before Gemini's
    connect itself failed); the third correctly left the interview stuck
    `active` at `retry_count=2` (budget exhausted) instead of resetting
    again; a fourth attempt correctly got the new, distinct "could not be
    resumed after multiple attempts" message (confirmed both in the API
    response and, after the `res.json().detail` frontend fix mentioned
    above, in the rendered browser text) — and exactly one interview row
    existed for that token throughout all four attempts. (2) A denied
    microphone permission showed the new distinct message, kept the Start
    button disabled, showed a working "Try again" button, and — confirmed
    directly via SQLite — never advanced the interview's status away from
    `pending`. (3) A page refresh immediately after `/start` (before any
    real Gemini exchange) reached the consent screen again with zero
    duplicate interview rows created (1 before, 1 after). (4) The two new
    UI states (`step-finalizing`, `step-mic-lost`) were driven directly via
    `showStep()` and screenshotted to confirm they render with the intended
    copy — not reachable through the real flow without either a working
    Gemini connection (finalizing) or an actual live mic track ending
    mid-conversation (mic-lost), both infeasible to force honestly against
    the real, currently-denied Gemini project. Confirmed via direct `psql`
    row counts (`jobs`/`applications`/`match_results`: 2/3/16, unchanged)
    that none of this session's work touched the live demo data — VoiceHR's
    own SQLite gained a handful of harmless test interview rows from these
    live checks (`mic-denied-test@example.com`, `refresh-test@example.com`,
    `robustness-test@example.com`, plus the one from milestone 29's own
    verification), left in place as harmless residue, same as prior
    sessions' documented test-data artifacts.

31. **VoiceHR interview rubric + adaptive questioning quality.** Audited the
    real interview prompt, question plan, evaluator prompt/schema, and CEFR
    mapping (`backend/services/agent.py`, `gemini_live.py`, `evaluator.py`)
    before changing anything, and found four concrete, evidence-based gaps
    rather than vague "make it better" targets:

    1. **Pronunciation was scored despite zero audio evidence.** The
       evaluator only ever receives the text transcript (`format_transcript`)
       — never audio — yet the old prompt still asked Gemini for a
       `pronunciation_intelligibility_score` and weighted it into
       `overall_score` (15%), merely labeled "less certain." Fixed by
       removing it entirely from the schema/weights: `pronunciation_score`
       is now always `None` ("not assessed," never fabricated), matching
       this task's own "do not pretend to assess pronunciation from
       transcript-only evidence" instruction. `AssessmentPanel`'s existing
       null-tolerant rendering (`value == null ? "—"`) needed no change —
       pronunciation now always renders as "—", which is the honest,
       intended result, not a regression.
    2. **The CEFR mapping could structurally never report A1 or C2.**
       `cefr_level_for_score`'s old thresholds only ever produced A2-C1,
       four of the six real levels — fixed to a genuine A1-C2 mapping
       (`<35 A1, <50 A2, <65 B1, <80 B2, <92 C1, else C2`).
    3. **"Inconclusive" interviews still reported a real, comparable CEFR
       level.** A short/aborted interview (duration<300s, <3 candidate
       turns, or a safety-triggered termination) was already flagged
       `inconclusive` internally, but the evaluator still returned a real
       `cefr_level`/`overall_score` computed from that thin sample — just
       labeled "Low confidence," a label `language_gate.py`'s
       `compute_language_gate` never even looks at. In practice this meant
       a two-minute aborted interview could still legitimately pass or fail
       a job's language requirement. Fixed with a new, explicit
       `insufficient_evidence` outcome (see below) that nulls
       `cefr_level`/`overall_score` unconditionally.
    4. **Interaction/spontaneity had no rubric line of its own** — silently
       folded into a single `interaction_comprehension` bucket with
       comprehension. Split into two real, separately-scored dimensions.

    **Interview structure (candidate-facing prompt, `agent.py`/
    `gemini_live.py`)** — kept the existing four-task shape (evidence-based:
    already well-formed, not rewritten without cause) and made its section
    boundaries explicit in the system prompt: **A. Warm-up** (background),
    **B. Past experience** (a real challenge/actions/result), **C. Opinion /
    reasoning** (a stance and why, on a domain-neutral topic — the prompt now
    explicitly forbids trivia/technical-knowledge questions: this is a
    language assessment, not a knowledge test), **D. Clarification /
    reformulation** (paraphrase a short message), **E. Adaptive follow-up** —
    new: exactly one additional follow-up after the four fixed tasks, chosen
    by the interviewer from a small three-tier bank
    (`ADAPTIVE_FOLLOWUPS["elaborate"/"reasoning"/"abstract"]`) based on
    observed candidate ability. **Adaptive questioning is bounded by prompt
    instruction, not new orchestration code** (this project's Gemini Live
    architecture is one system prompt conducting the whole native-audio
    conversation — there is no per-turn code state machine to add rules to,
    and none was added): at most one elaboration follow-up per fixed task,
    at most one rephrase-once for a misunderstood question, never a second
    Section E follow-up, never an invented sixth section. **Language
    purity**: the prompt now explicitly requires the entire interview stay
    in the configured language, forbids the interviewer ever switching
    languages, allows exactly one brief request to continue in the target
    language if the candidate answers in another one, and explicitly
    forbids turning the session into translation assistance after that.
    **Never reveal the result**: explicit instruction never to say anything
    like "That sounds like B2," matching this task's own named example.
    **Prompt-injection / interview control**: new explicit section stating
    the candidate's own words are never instructions — covers revealing the
    system prompt, skipping ahead, assigning a level/score, declaring a
    pass, changing the rubric, and claiming to be the recruiter/an
    administrator. **Brevity**: added an explicit "keep your own turns
    brief, the candidate should do most of the talking" instruction (an
    audit finding: interviewer turns had no length constraint before).

    **Evidence-based CEFR evaluation (`evaluator.py`)**: `DIMENSION_WEIGHTS`
    is now five real, evidenced dimensions —
    `comprehension_relevance` (25%), `fluency_coherence` (25%),
    `grammar_language_control` (20%), `vocabulary_range` (15%),
    `interaction_spontaneity` (15%) — summing to 1.0, with pronunciation
    permanently excluded (see above). The model must also return
    `sufficient_evidence: bool` in its structured JSON; **either** that flag
    being `false` **or** the caller's own deterministic pre-check (duration/
    turn-count/termination, unchanged, still computed in `processing.py`)
    is enough to route the result through a new `_insufficient_evidence_result`
    path: `cefr_level`/`overall_score` become `None` (never a guessed
    number), `evaluation_status` becomes `"insufficient_evidence"`, and
    per-dimension scores are still passed through best-effort (useful
    signal for a recruiter's own reading of strengths/concerns, even though
    the aggregate verdict is withheld). `processing.py` now derives one
    combined `inconclusive` signal (`pre_check_inconclusive OR
    evaluation_status == "insufficient_evidence"`) used consistently for
    VoiceHR's own interview/report status *and* what RWS is told — the two
    can no longer disagree about whether a real CEFR verdict exists.
    **RWS needed a two-line, explicitly-authorized schema change, nothing
    else**: `shared/schemas/__init__.py`'s `InterviewAssessment.evaluation_status`
    Literal gained `"insufficient_evidence"` as a fourth accepted value
    (`language_gate.py` itself is untouched — `compute_language_gate` already
    mapped a null `assessed_cefr` to `"needs_review"` before this pass, for
    the pre-existing "VoiceHR's own evaluation failed" case; a new dedicated
    test, `test_insufficient_evidence_needs_review_not_pass_or_fail`,
    pins that this is the exact same, already-correct branch). **RWS resume
    scoring, the gate's threshold comparison itself, the admin workflow, the
    candidate-only privacy boundary, the provider architecture, and the
    webhook's transport mechanism (HMAC signing, delivery-id dedup,
    best-effort single-attempt delivery) are all completely unchanged** —
    confirmed by inspection and by every existing test in those areas still
    passing unmodified.

    **The new `interaction_spontaneity` dimension is VoiceHR-internal only,
    deliberately never added to the cross-service webhook schema**: it is
    stored in a new nullable SQLite column (`reports.interaction_score`,
    same lightweight `ALTER TABLE` pattern as every prior column addition)
    and surfaced only through VoiceHR's own rich report
    (`GET /api/report/{token}` / RWS's proxy `GET /v1/interviews/{id}/report`,
    which passes VoiceHR's JSON through verbatim, no schema on that path) —
    never through `POST /v1/webhooks/voicehr`. RWS's recruiter frontend
    (`ReportDetailPage.jsx`, `AssessmentPanel.jsx`) gained one new row each
    ("Interaction") using the exact same graceful-null pattern pronunciation
    already relied on (`value == null ? "—"`), so the two other
    `AssessmentPanel` call sites that only ever see the narrower
    webhook-derived object (ApplicationsPage/InterviewsPage) simply render
    "—" for it, no special-casing needed. `AssessmentPanel` also gained a
    label/tone for the new `insufficient_evidence` status and a short
    "needs manual review" note when no CEFR level exists for that status.

    **Tests** (all deterministic, mocked Gemini throughout — no live call in
    any test, per this task's own instruction): rewrote `test_evaluator.py`
    for the new dimension names/weights and added coverage for
    model-reported `sufficient_evidence: false`, the full A1-C2 CEFR range,
    pronunciation always being `None` regardless of input, the dimension
    weights summing to 1.0, and a prompt-injection-attempt case (proves the
    surrounding *code* has no keyword-based override a transcript could
    game — the model's own resistance can only be verified live, see
    below). New `test_interview_prompt.py` (10 tests) captures the real
    `config.system_instruction` the pipeline builds and asserts on its
    actual content: the five-section structure, the bounded
    adaptive-questioning rules, the domain-knowledge-testing prohibition,
    the never-reveal-the-result rule, the anti-injection rule, language
    purity for both languages, and that `TASKS`/`ADAPTIVE_FOLLOWUPS` are
    correctly embedded. New `test_interview_quality.py` is the golden-case
    benchmark this task's own section 10 asked for — nine table-driven
    cases (weak/intermediate/strong samples, very-short-answers,
    off-topic-answers, repeated-misunderstanding, candidate-switches-
    language, prompt-injection-attempt, abandoned-interview), each
    asserting an expected CEFR band or `insufficient_evidence` outcome —
    a small, maintainable regression aid, explicitly not a claim of
    certified CEFR accuracy. `test_database.py`/`test_processing.py`/
    `test_report.py` updated for the new `interaction_score` column.
    RWS gained one `test_language_gate.py` case and one
    `tests/unit/test_schemas.py` case for the new `evaluation_status` value.
    VoiceHR: 194 passed (was 170). RWS: 112 passed (was 111). Repo-root
    `tests/unit/`: 139 passed (was 138). Repo-root integration
    (`test_recruitment_workflow.py`): 96 passed, unchanged — re-run only
    after re-confirming the `TEST_DATABASE_URL` safety guard still fails
    closed with it unset.

    **Live verification**: VoiceHR's Google project **still** returns the
    documented `403 PERMISSION_DENIED` (re-confirmed directly this session,
    both via a raw evaluator call and via a real candidate-side browser
    session against the rebuilt/redeployed `voicehr` image) — unrelated to
    this change, not worked around, provider not changed, per this task's
    own explicit instructions. What *was* verified live and honestly claimed:
    the rewritten, longer system prompt builds without error for both
    languages and reaches Gemini Live's real connect call cleanly (proven
    both by `test_interview_prompt.py` executing the actual prompt-building
    code path, and by one real browser session that reached the exact same
    pre-existing 403 — not some new error the prompt rewrite might have
    introduced); the existing milestone-30 bounded-reconnect behavior
    (status reset to `pending`, `retry_count` incremented) still works
    correctly with the new prompt in place; the rebuilt `recruitment-workflow`
    frontend's Report Detail page renders correctly against the one real
    stored report in the current demo data (screenshotted; that specific
    report's own `evaluation_status` is `"failed"`, pre-existing per
    milestone 28's own notes, so it correctly shows "no scores available"
    rather than the new dimension grid — expected, not a defect). A genuine
    controlled interview producing a real, completed evaluation with a real
    CEFR verdict — the only way to observe the new interaction-dimension
    row actually populated, or the prompt's adaptive/language-purity/
    anti-injection behavior for real — remains blocked by the same external,
    non-code Google-side access denial documented since milestone 25;
    nothing here changes that. Confirmed via direct `psql` row counts
    (`jobs`/`applications`/`match_results`: 2/3/16, unchanged) that none of
    this session's work touched the live demo data.

32. **VoiceHR interview quality benchmark + recruiter-facing assessment
    explanation.** Two halves, both scoped to *using and explaining* the
    milestone-31 rubric — no interview-flow redesign, no change to resume
    scoring or `language_gate.py`'s threshold logic, exactly as instructed.

    **Benchmark runner** (`services/voicehr/tests/`): `golden_cases.py` (data)
    and `benchmark_runner.py` (analysis + CLI) are now split out of the test
    file that uses them, so the benchmark is runnable two ways — as pytest
    (`test_interview_quality.py`, runs in CI with everything else, no live
    Gemini) and standalone (`cd services/voicehr && python -m
    tests.benchmark_runner`, writes `tests/benchmark_results/latest.{json,md}`
    and exits non-zero on any failure). Every case mocks
    `evaluator._evaluate_with_gemini` directly with a hand-written response;
    `analyze_case()` checks four things per case, not just the CEFR band:
    dimension coverage (all five real rubric dimensions present in the
    result), missing/invalid top-level fields, "obvious contradictions"
    (e.g. `evaluation_status: "completed"` with a null `cefr_level`,
    `evaluation_status: "insufficient_evidence"` with a real CEFR still
    attached, `pronunciation_score` non-null — checked against a fixed
    disallowed-fields list too, in case a model response ever tried to sneak
    a `reasoning`/`chain_of_thought` key through), and the expected outcome
    itself. `REQUIRED_PASS_RATE = 1.0` is the explicit, reasoned threshold:
    every case is fully deterministic (a fixed mocked response, never a real
    model call), so there is no meaningful "acceptable" partial pass rate —
    a drop below 100% always means either a real pipeline regression or a
    fixture that needs updating, never noise to tolerate. Golden cases grew
    from 9 to 13 (one clean case per requested scenario, not dozens of
    near-duplicates): added `b2_independent_sample`, `c1_advanced_sample`
    (narrowed from the old combined "strong_sample"), `c2_exceptional_sample`,
    `fluent_but_poor_relevance` (linguistically capable but off-topic --
    demonstrates that a 25%-weighted comprehension score of 10 still pulls an
    otherwise-C1-looking profile down to B1, i.e. the weighting genuinely
    matters, not just cosmetically), and `strong_vocab_weak_grammar`
    (vocabulary's smaller 15% weight doesn't let it fully compensate for
    weak 20%-weighted grammar); enhanced `candidate_switching_language` into
    `repeated_language_switching` (multiple switch-backs across turns, not
    one). Full CEFR range A1→C2 is now covered by a clean, distinct case
    each. A committed `tests/benchmark_results/latest.{json,md}` snapshot
    exists as a concrete, browsable artifact (per this task's own "optional
    benchmark artifact" section) — illustrative of the runner's own output
    shape, not automatically kept in sync on every future run.

    **Assessment contract hardening** (`backend/api/routes/report.py`): two
    real fixes, found by re-reading the contract this task's own section 3
    asked for, not by assumption. (1) `pronunciation_score`/
    `pronunciation_intelligibility`/`pronunciation` in `get_report()`'s
    output are now hard-coded `None`, unconditionally — previously they
    passed through whatever the `reports.pronunciation_score` *column*
    happened to contain, which meant a handful of reports scored before the
    milestone-31 rubric pass (when pronunciation genuinely was written) could
    still render a real number, quietly contradicting the "never fabricated"
    contract for exactly the reports old enough to test it. Enforced at read
    time now, not just by what the current evaluator happens to write. (2) A
    new derived field, `sufficient_evidence: true | false | null`
    (`true` for `"completed"`, `false` for `"insufficient_evidence"`, `null`
    — genuinely unknown, not a guess either way — for `"failed"`), gives the
    frontend (and any future consumer) an explicit boolean instead of a
    string-comparison against `evaluation_status` everywhere. VoiceHR-only,
    same reasoning as milestone 31's `interaction_score`: never added to the
    cross-service webhook schema (`shared/schemas.InterviewAssessment`),
    since RWS's own gate logic never needs it — `evaluation_status` already
    reaching `"insufficient_evidence"` there is sufficient, and the webhook's
    own architecture (signing, dedup, transport) is untouched, per this
    task's explicit "do not redesign the interview flow" instruction read
    together with the prior session's "do not change webhook architecture."

    **Recruiter Report Detail redesign** (`ReportDetailPage.jsx`): reordered
    and restructured into exactly the hierarchy this task specified —
    **Language Screening** (Required/Assessed/Result — RWS's own
    deterministic verdict, `language_gate.py`, never recomputed here) →
    **Assessment Quality** (a status badge plus, for `insufficient_evidence`,
    the exact explanation text this task named verbatim: "VoiceHR did not
    collect enough usable language evidence to assign a reliable CEFR
    level.") → **Language Profile** (the five real dimensions, each shown as
    a qualitative band — Excellent/Strong/Good/Fair/Limited, a new
    `describeDimensionScore()` helper, purely display sugar, never used to
    compute anything — plus a short evidence-backed quote/note pulled from
    the evaluator's own `evidence` array when one exists for that dimension;
    only shown once `evaluation_status === "completed"`, since a per-dimension
    breakdown during `insufficient_evidence` would risk implying numbers that
    aren't trustworthy yet) → Strengths/Concerns → Recording → Transcript
    (always available, unchanged). `AssessmentPanel` (still used by
    ApplicationsPage/InterviewsPage's compact row-expansion view, a
    deliberately different, denser context this task didn't ask to redesign)
    dropped its permanently-`null` Pronunciation row for the same "why is
    this always blank" reason, and now shows the exact
    `INSUFFICIENT_EVIDENCE_EXPLANATION` text too, with a distinctly-toned
    (`warning`, never `danger`) badge — inconclusive is never styled like
    failed, anywhere. `noteText`/evidence rendering already existed and were
    reused, not reinvented.

    **Report consistency, the explicit ask in this task's own section 7**:
    found and fixed a real, concrete duplicate-label bug —
    `ReportDetailPage.jsx` had its own local `LANGUAGE_GATE_RESULT_TEXT`
    object showing `passed → "Meets requirement"`, while
    `constants/language.js`'s shared `LANGUAGE_GATE_LABELS` (used by
    ApplicationsPage's own column) showed the *same* status as `"Passed"` —
    two different words for one state, exactly the anti-pattern this task
    warned against. Fixed by deleting the duplicate and updating the one
    shared source (`LANGUAGE_GATE_LABELS.passed` is now `"Meets
    requirement"` everywhere). Also consolidated `EVALUATION_STATUS_LABELS`/
    `TONE` (previously defined three times — once each in `AssessmentPanel.jsx`,
    `ApplicationsPage.jsx`'s own `INTERVIEW_STATUS_LABELS`, and
    `InterviewsPage.jsx`'s own copy of the same) into one shared export in
    `constants/language.js`, reused by all three plus a new "Status" column
    on `ReportsPage`'s own list (previously the only page with no way to
    tell an `insufficient_evidence`/`failed` row apart from any other blank
    CEFR/score pair). `InterviewsPage`'s compact `formatScoreSummary()` also
    stopped collapsing `insufficient_evidence` into the same bare "—" a
    not-yet-interviewed row would show.

    **Tests**: Backend — `test_interview_quality.py` (15 cases, up from 9:
    13 golden cases + 2 whole-benchmark-runner tests, one of which pins that
    two consecutive runs produce byte-identical CEFR results, i.e. no hidden
    state/randomness). `test_report.py` gained 5 new tests for exactly this
    task's own list: structured dimension payload roundtrip (long-form +
    short-alias keys both survive a write→read cycle), pronunciation always
    null even when a legacy row's own column holds a stale non-null value,
    an old-style stored report (no `interaction_score`, default
    `evaluation_status`) still renders without a crash or a fabricated
    value, `insufficient_evidence` surviving the *full* `process_interview`
    → `reports` table → `get_report()` pipeline (not just a unit test of
    `evaluator.py` in isolation) with the correct combined `inconclusive`
    flag reaching the RWS-webhook helper, and a fixed-list check that no
    `reasoning`/`chain_of_thought`/`internal_notes`/`raw_response`/`thinking`
    key ever appears in a response. Frontend — since this project has never
    introduced a JS test framework (Jest/Vitest/RTL), relying on real-browser
    Playwright verification throughout instead, added 13 framework-free unit
    tests for `constants/language.js`'s pure logic
    (`src/constants/language.test.js`, run via Node's own built-in test
    runner — `node --test src/constants/language.test.js`, zero new
    dependencies/config) covering the label/tone consistency, the exact
    insufficient-evidence wording, `describeDimensionScore`'s band
    boundaries, and `describeLanguageGate`'s null-safety across every gate
    status. The seven rendering scenarios this task's own section 8 asked
    for (passed/failed/pending/needs_review/insufficient-evidence/
    no-requirement/missing-optional-dimension-data) were instead verified
    live, in real Chromium, using Playwright's `page.route()` to inject
    deterministic synthetic report/application fixtures — the same
    "deterministic fixtures instead of live Gemini" instruction section 10
    gives for live verification, applied to satisfy both sections in one
    pass rather than duplicating the effort. VoiceHR: 205 passed (was 194).
    RWS: 112 passed (unchanged — untouched this session). Repo-root
    `tests/unit/`: 139 passed (unchanged). Repo-root integration
    (`test_recruitment_workflow.py`): 96 passed (unchanged), run only after
    re-confirming the `TEST_DATABASE_URL` safety guard still fails closed
    with it unset.

    **Live verification**: rebuilt/redeployed both `recruitment-workflow`
    and `voicehr`. All six synthetic Report Detail states rendered exactly
    as designed (screenshotted) with zero console errors across the whole
    pass. The one real, persisted `evaluation_status: "failed"` demo report
    (pre-existing since milestone 28) still renders correctly post-redesign
    — "Failed" quality badge, the existing failure explanation text,
    transcript still reachable below. `ApplicationsPage`'s language-gate
    column confirmed to no longer say "Passed" anywhere (now consistently
    "Meets requirement"). `ReportsPage`'s new Status column renders. A
    375px-wide mobile viewport of the real report page showed no horizontal
    overflow. Two console 404s were observed during the consistency pass —
    both confirmed (via direct URL inspection) to be the already-documented
    orphaned-candidate identity lookup (`0a699701-...`, see Known
    limitations below), not a new regression. VoiceHR's Gemini access is
    still denied (documented since milestone 25, unrelated to this task,
    not worked around) — no genuine, freshly-completed interview exists to
    show the new Language Profile section against real (not fixture) data;
    everything in this milestone's own live-verification pass used either
    the one pre-existing real report or explicit, clearly-synthetic
    fixtures, never a faked "success." Confirmed via direct `psql` row
    counts (`jobs`/`applications`/`match_results`: 2/3/16, unchanged) that
    none of this session's work touched the live demo data.
33. **Post-VoiceHR interview stage, first backend slice: `interview_rounds`
    table + create endpoint.** A prior design-only session (no code) worked
    out how to represent a technical/managerial interview stage for
    candidates whose language gate has cleared, without widening
    `Application.status` or touching `MatchResult`/the language gate itself
    — the design settled on a parallel, one-to-many `interview_rounds` table,
    the same relationship the language gate already has to the state
    machine, rather than new `Application.status` values (which would have
    rippled into `_DECIDABLE_STATUSES`, the frontend's `STATUS_LABELS`/
    `STATUS_TONE`/`STATUS_ORDER`, and the Dashboard's pipeline-by-status
    breakdown, for no real benefit). This session implemented exactly the
    first slice of that design: migration `0007` (new
    `recruitment.interview_rounds` table — `round_type` `technical`/
    `managerial`, `status` `scheduled`/`completed`/`cancelled`, `outcome`
    `pass`/`fail`/`needs_review`/null, `interviewer_name` free text,
    `scheduled_at`, `notes`, `created_by` → `recruiters.id`, paired `CHECK`
    constraint enforcing `outcome` is set if-and-only-if `status =
    'completed'`), the `InterviewRound` ORM model + `create_interview_round`/
    `list_interview_rounds_for_application` in `db.py`, the
    `InterviewRoundResponse`/`InterviewRoundRequest` schemas, and
    `POST /v1/applications/{id}/interview-rounds`. That endpoint enforces two
    gates by reusing existing checks rather than duplicating logic: the
    application must not already be `accepted`/`rejected`, and
    `language_gate.status` must not be `pending`/`failed`/`needs_review`
    (the exact same `_language_gate_info`/`BLOCKING_STATUSES` `accept`
    already uses) — this is deliberately "for candidates who pass the
    language gate," enforced at the API boundary, not just a UI convention.
    `interviewer_name` is a plain `TEXT` column, not a foreign key: this
    project has exactly one seeded recruiter account and no interviewer/user
    directory (CLAUDE.md's non-negotiable decisions), so there is no table to
    reference. `_application_response` (shared by every `Application` read
    path — `select`, `accept`/`reject`, `invite`, `resend-invite`, both
    `GET .../applications` routes) now also fetches and embeds
    `interview_rounds`, one query per application — accepted deliberately
    rather than batched, at this project's demo scale (a handful of
    applications total). Only creation exists so far; reschedule/complete/
    cancel are the next slice (see `[NEXT]`) — deliberately deferred rather
    than built speculatively ahead of a real need to exercise them.

    **Verification**: migration applied cleanly to both the live demo
    database and the disposable `company_second_brain_test` database (same
    `alembic upgrade head`, both confirmed at `0007` via `\d
    recruitment.interview_rounds` — table owned by `recruitment_role`, same
    as every other table, no separate `GRANT` needed). RWS unit suite: 123
    passed (was 112 — the new `test_interview_rounds.py` plus a monkeypatch
    addition to every existing test that exercises `_application_response`,
    since it now has one more dependency to stub). Safe integration suite
    (`TEST_DATABASE_URL` pointed at `company_second_brain_test`, never the
    live database, per `db_safety.py`'s guard): the existing 96
    `test_recruitment_workflow.py` tests still pass unchanged, plus 9 new
    `test_interview_rounds_integration.py` tests covering what the
    monkeypatched suite structurally cannot — the DB `CHECK` constraints
    really are enforced by Postgres (invalid `round_type`, invalid `status`,
    invalid `outcome`, both directions of the paired outcome/status
    constraint), and one real end-to-end create → persist → embedded-on-read
    round trip through the real API and a real session. Confirmed via direct
    `psql` row counts that the live demo database's `jobs`/`applications`/
    `match_results` counts (2/3/16) and `interview_rounds` count (0) are
    exactly as before — this session only added schema, never demo data —
    and smoke-tested the live API (`GET /v1/applications` with a real JWT)
    to confirm every real application row now carries `"interview_rounds":
    []` with zero errors.
34. **Interview-round lifecycle actions: reschedule, complete, cancel.**
    Closes out the post-VoiceHR interview stage's first vertical slice
    (milestone 33 landed only creation). Three verb-named `POST` endpoints,
    matching this file's existing style (`accept`/`reject`/`invite`/
    `resend-invite`), not a generic `PATCH`:
    `POST .../interview-rounds/{round_id}/reschedule` (updates `scheduled_at`
    only), `.../complete` (sets `outcome` — required, `Literal["pass",
    "fail", "needs_review"]` — plus optional `notes`, `status →
    'completed'`), and `.../cancel` (`status → 'cancelled'`, no body) — all
    three only from `status='scheduled'`, all three terminal-in-effect
    (`completed`/`cancelled` have no path back to `scheduled`; reschedule
    is the only repeatable one). Each is a `db.py` helper using the exact
    same atomic conditional-`UPDATE ... WHERE status = 'scheduled'` pattern
    `record_invite_attempt`/`transition_application` already use — race-safe
    under concurrent duplicate requests, returns `None` on a no-op so the
    route can distinguish "doesn't exist" (checked first, via a new
    `get_interview_round` + `_get_own_interview_round`, which also confirms
    the round belongs to the `application_id` in the URL — a mismatch reads
    as 404, not a cross-application leak) from "exists but not
    `scheduled`" (409, naming the round's actual current status). `complete`'s
    request schema requires a valid `outcome` value before ever reaching the
    DB — matching migration 0007's paired outcome/status `CHECK` constraint
    at the API boundary, not just relying on the constraint to turn a bad
    request into a raw 500. Omitting `notes` on `complete` leaves whatever
    was recorded at scheduling time untouched (only overwritten when a
    caller actually supplies a new value) rather than clobbering it with a
    null. None of the three touch `Application.status`, `MatchResult`, or
    the language gate — the same boundary milestone 33 already established.

    **Verification**: RWS unit suite: 135 passed (was 123 — 12 new tests in
    `test_interview_rounds.py` covering each action's success path, its
    409-when-not-scheduled path with the correct current status named, its
    404-for-unknown-round path, its 404-when-the-round-belongs-to-a-
    different-application path, and `complete`'s 422-before-touching-the-db
    for an invalid outcome value). Safe integration suite (`TEST_DATABASE_URL`
    → `company_second_brain_test`, never the live database): the existing
    96 + 9 tests still pass unchanged, plus 8 new real-Postgres tests in
    `test_interview_rounds_integration.py` — a real reschedule persisting a
    new `scheduled_at`, a real complete persisting `outcome`+`notes` (proof
    the paired `CHECK` constraint is satisfied by the real `UPDATE`, not
    just assumed), a real complete-without-notes preserving the
    scheduling-time notes, a real cancel persisting `status='cancelled'`,
    a real reschedule blocked after a real complete (409, "completed" named),
    a real cancel blocked after a real cancel (409, "cancelled" named), a
    real cross-application 404, and all three actions 404ing together for a
    wholly unknown round/application pair — 113 total, up from 105. The DB
    `CHECK`-constraint tests milestone 33 already wrote
    (`test_check_constraint_rejects_completed_without_outcome`,
    `..._rejects_outcome_set_while_still_scheduled`, etc.) already cover
    this task's own "still gets rejected by the DB constraint even if
    application-level validation is ever bypassed" verification bar — not
    duplicated here. Confirmed via direct `psql` row counts that the live
    demo database's `jobs`/`applications`/`match_results`/`interview_rounds`
    counts (2/3/16/0) are exactly as before this session — deliberately
    did **not** create a live round against real demo data for this pass:
    every real-Postgres behavior (create, reschedule, complete, cancel, all
    five `CHECK` constraints) is already proven against the disposable test
    database, and this project has no delete endpoint for any resource by
    design, so a live create here would have left a permanent, unrequested
    change to visible demo state rather than a reversible smoke test.

---

## [NEXT]

**Frontend-only: app-wide dark-theme focus-ring audit** (see
`FRONTEND_HANDOFF.md`'s own `[NEXT]` for exact scope — this file only
cross-references it, per the two documents' own division of labor).

The interview-rounds feature is now complete end to end and audited as a
whole workflow: backend (milestones 33-34: create/reschedule/complete/
cancel, tested against both a mocked DB and real Postgres) and frontend
(milestones 18-20: display+schedule, then reschedule/complete/cancel, then
a real-browser audit of the full select→invite→gate→schedule→complete→
accept chain in one continuous session). That audit found and fixed two
concrete UX gaps (a focus-ring color mismatch, a missing read-only view on
`CandidateDetailPage`) and surfaced one broader, real, not-yet-fixed gap:
the same focus-ring root cause almost certainly affects every other V2
page's own inner content, not just the one page (`ApplicationsPage`) this
pass actually Tab-tested. That's the honest next item — not a manufactured
one, and not new feature work, just finishing a real accessibility check
this project has now confirmed matters (found live, not theorized).

What remains in Known Limitations above beyond this is external, not
implementable here: VoiceHR's Gemini access needs Google support/billing
action, and automatic invite emails need a real `RESEND_API_KEY`
configured — neither is a code change. Everything else logged there (no
pagination, Second Brain's missing test suite, the write-only
`candidate_feedback` sink, VoiceHR's unauthenticated `admin.html`) is
deliberately deferred per this project's own "no infra/refactor ahead of
demonstrated need" rule, not a gap waiting on the next session.
