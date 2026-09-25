# Company Second Brain — Implementation Handoff

Cross-service source of truth: backend architecture, live API surface, security
boundaries, known limitations, and demo readiness. Frontend-only implementation
detail (routes, pages, components, visual design) lives in `FRONTEND_HANDOFF.md`
instead — this file never duplicates it, only references it (per the two files'
own long-standing division of labor).

**Project status: feature-complete; VoiceHR's Gemini access is restored
after a long-standing external denial, and JD submission now deterministically
flags suspicious Gemini extractions without a mandatory review step
(milestone 36), automatically attempts one Gemini repair call on a flagged
extraction before giving up and leaving it flagged (milestone 37), and now
also flags a job post-match when its extraction looked clean but every
candidate still failed the required-skills gate despite relevant profiles
(milestone 38). An end-to-end reliability audit of that whole pipeline
(milestone 39) found and fixed two real MEDIUM defects (a DB failure in the
advisory anomaly write could have turned a fully successful submission into
an opaque 500; the frontend's warning banner/badge overclaimed "extraction"
as the cause even for a match-outcome-only flag) and surfaced one
pre-existing MEDIUM gap (a partial-write risk in the per-candidate scoring
loop), which milestone 40 then closed by making the whole submission
atomic (one commit at the end, not one per row) — and, with Gemini's daily
quota reset, live-confirmed the post-match anomaly check firing through a
real, fresh `POST /v1/jobs` for the first time. Milestones 41-42 automated
the post-VoiceHR human interview-round stage end to end: scheduling,
rescheduling, or cancelling a round now emails both the candidate (contact
fetched fresh from Resume Intelligence, never persisted) and the
interviewer, with a real ICS calendar attachment and a correctly
incrementing `SEQUENCE` so a real calendar client treats a reschedule as an
update and a cancel as a removal, not a duplicate. Milestone 43 then
resolved the last real environment blocker: a genuine `RESEND_API_KEY` is
now configured for both VoiceHR and RWS, with live delivery confirmed for
both services' mailers (including a real ICS-bearing email) — the only
remaining constraint is Resend's own sandbox mode (no verified custom
domain), which still limits real deliveries to the account's own verified
address. Milestone 44 then drove that whole pipeline for real with a
genuinely deliverable candidate — a real resume upload, a real qualifying
match, and a real `Invite` click that actually succeeded, the first in this
project's history** (see `[NEXT]` at the end). Every backend service, the full
recruiter frontend, and
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
whole workflow across `FRONTEND_HANDOFF.md`'s milestones 18-21 (the last of
which closed the one gap the audit itself surfaced, an app-wide
dark-theme focus-ring fix) — see that file for the full audit findings.

**RESOLVED — the previous "demo backbone unusable" warning that used to live
here.** Both blockers it described are now fixed: (1) the destructive
integration-test fixture can no longer run against anything but an
unmistakably-named, dedicated test database (see the new test-isolation
invariant under "Architecture / invariants" and "How to run integration tests
safely" below) — the exact incident that happened before is now structurally
impossible to repeat by accident; (2) representative demo Jobs/MatchResults/
Applications have been recreated (see "Current state" below) via a new,
explicit, idempotent seed script, including reconnecting one application to
VoiceHR's own surviving completed-report history. **RESOLVED (milestone
43)**: a real `RESEND_API_KEY` is now configured and live delivery is
confirmed working for both VoiceHR's candidate-invite email and RWS's
interview-round notifications (see Known Limitations for the one remaining
caveat — Resend's sandbox mode still restricts deliveries to the account's
own verified address until a custom domain is verified, so this
environment's `@example.com` demo candidate addresses specifically still
won't receive real mail). A live "Invite"/"Resend invitation" click in front
of a jury will now genuinely attempt real delivery rather than fail
instantly on a missing key — whether it *succeeds* for a given candidate
depends on that candidate's address being deliverable under the current
sandbox restriction. Milestone 29's "Invitation not sent" / Retry UX remains
exactly as useful either way, since a sandbox rejection is still a real,
clean failure it correctly surfaces.

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
| Recruitment Workflow (RWS) | 174 passed | `services/recruitment-workflow/tests/` — mocked-DB unit/route tests, host or in-container. +15 this session (milestone 38): a new `test_match_anomaly.py`, pure-function coverage of every branch in `match_anomaly.py` (fires under the intended conditions, doesn't fire for low semantic relevance/a passing hard gate/too small a pool/an empty list, uses the *average* semantic score rather than a single outlier, and `merge_anomaly_warning`'s own status-transition and duplicate-warning-append idempotency). Milestone 36 (JD-extraction validation) previously added +24 here; unchanged this session. |
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
`test_recruitment_workflow.py` is 102 passed (was 99 before milestone 37's +3
for the JD-repair-pass: a flagged extraction genuinely repaired by a mocked
second Gemini call reports `extraction_status: "repaired"` with the repaired
(not original) requirements actually persisted and matched, a repair attempt
whose own output still fails validation falls back to the *original*
extraction persisted as `"flagged"` (not the failed repair's output), and a
repair call that itself raises `LLMProviderError` never blocks job creation
(job still created, original extraction persisted as `"flagged"`, matching
still runs). The two milestone-36 tests for the clean/flagged happy paths
were also extended in place with call-count assertions (`generate_structured_calls`)
proving a clean extraction makes zero repair calls and a flagged one makes
exactly one — not new tests, so not counted in the +3. Milestone 38 added
+5 (a clean-but-hallucinated extraction gets promoted to `"flagged"`
post-match when every one of 5 relevant candidates fails the hard gate; the
same shape does *not* fire when semantic relevance is weak, when at least
one candidate passes the hard gate, or when the pool is under 5 candidates;
and a direct DB comparison confirms every `MatchResult` scoring field
persisted before the anomaly check ran is byte-for-byte identical to what
the API response already said after it ran). Milestone 39 added +1: the
reliability audit's own MEDIUM fix (a simulated DB failure writing the
post-match anomaly must still return `200` with the job's real,
actually-persisted pre-anomaly state, never a `500`). **109 passed this
session (milestone 40's +1)**: a real mid-loop `SecondBrainClientError`
(the 3rd of 5 candidates) followed by `db_session.rollback()` (simulating
the real per-request session cleanup the shared-session test harness
otherwise skips) confirms zero `Job`/`MatchResult` rows survive; the
pre-existing retrieval-failure test was extended with the same check.
**`tests/integration/test_interview_rounds_integration.py` (17
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

**New this session (milestone 44)**: a third job, `Smoketest Live Invite
Demo Engineer` (`f7d2f474-5284-4d6e-81ef-6fcb31d89c0d`), and a new,
**identity-complete, non-orphaned candidate** (`e7e2f34f-...`, real email is
the Resend account's own verified address) with one qualified `MatchResult`
and one real `Application` (`095731d3-a1ba-4738-82b4-3c2a9182602f`) already
successfully `invited` — see the milestone 44 write-up below and `[NEXT]`.
This is now the recommended application for demonstrating a live,
succeeding "Invite" click, and the one to use for `[NEXT]`'s real interview.

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

**This session (deterministic JD-extraction validation, milestone 36)**: JD
submission's real failure mode — Gemini extracting `required_skills` as
phrase-like text (e.g. `"building and deploying REST APIs"`) that can never
exact-match under the hard gate, or under-extracting everything so almost
every candidate qualifies on semantic score alone — is now caught
deterministically, without adding a mandatory recruiter-review step. A new
pure module, `jd_validation.py` (no DB/HTTP/LLM calls, mirrors `scoring.py`'s
own discipline), normalizes the structured JD (trim/dedupe/drop
required-preferred overlap — provably scoring-neutral, since
`compute_skill_score` targets `required ∪ preferred` as a set union) and
checks it against 4 deterministic rules, run immediately after Gemini
structuring and before persistence/matching. A flagged extraction is never
blocked or held for review — it's persisted and matched exactly like a clean
one; `extraction_status`/`extraction_warnings` (new columns on
`JobDescription`, migration `0008`) just make the flag visible. Frontend: a
"Needs review" badge on the Jobs list, a non-blocking warning banner on the
Job Detail page — no confirmation step, no new required user action. Ran the
full stack live end to end against the real running containers (not just
tests): migration `0008` applied to both the live and disposable test
databases (confirmed all 8 pre-existing `job_descriptions` rows backfilled to
`clean`/`[]` with zero errors), then a real job submitted through the actual
Gemini pipeline with a deliberately vague description — Gemini genuinely
under-extracted everything, the job came back `extraction_status: "flagged"`
with the expected warning message, and 5 real `MatchResult`s were still
created and returned (not blocked), confirmed identically via `GET
/v1/jobs`, the exact endpoint the frontend reads. The new UI strings
("Needs review", "extraction looks suspicious") were confirmed present in
the actual built frontend bundle the container serves, and then confirmed
rendering correctly in a real Chromium browser (Playwright, cached binary on
this machine): logged in, navigated to `/jobs`, the flagged job shows the
"Needs review" badge next to its title while every other (clean) job shows
none, clicked into it, and the warning banner rendered on `/jobs/{id}` with
the exact warning message from the API — zero console/page errors
throughout.

**This session (automatic JD repair pass, milestone 37)**: milestone 36
flagged bad extractions but took no corrective action. Now, when the first
extraction is flagged, `main.py` makes exactly one additional Gemini call
(`repair_structured_extraction`) — the original JD, the first extraction,
and the exact deterministic warnings it triggered, asking Gemini to fix only
those specific problems and return the same `StructuredJobDescription`
schema — then re-runs `jd_validation.validate_and_normalize` on the result.
If the repair genuinely passes validation, the *repaired* requirements are
what get persisted and matched, with `extraction_status: "repaired"`
(migration `0009` widens the DB `CHECK` constraint). If the repair call
itself fails (`LLMProviderError` — unreachable, quota, malformed response)
or its own output still fails validation, the *original*, pre-repair
extraction is what's persisted as `"flagged"` — the failed repair attempt is
discarded, never partially applied, and never blocks job creation either
way. No retry loop (at most one repair call, ever), no new LLM provider, no
scoring change (the repair pass only changes what gets validated and
matched against, never how matching itself works), no recruiter confirmation
step. `jd_validation.py` itself stays exactly as narrow as milestone 36 left
it — no Gemini call was added to that module; the repair call lives in
`main.py`, which already owns the one place in this codebase allowed to call
Gemini for JD structuring. Frontend: a distinct "Auto-repaired" badge
(violet/accent tone, not the warning-toned "Needs review") on the Jobs list,
and a small informational (non-alert) note on Job Detail for a repaired job —
`"flagged"`'s existing warning banner is completely unchanged. Verified: 159
RWS unit tests unchanged (no new unit tests — the repair pass is only
exercisable through the full `submit_job` pipeline, which only the
repo-root integration suite covers), 102 repo-root integration tests passed
(+3: a mocked repair that succeeds, one whose own output still fails
validation, and one where the repair call itself raises `LLMProviderError` —
all three confirm the correct final `extraction_status`, that job creation
is never blocked, and that matching still runs). Frontend build and its 31
Node tests passed; a real-browser Playwright regression pass confirmed the
existing flagged/clean badge and banner behavior is completely unaffected by
this change (no job in this environment's data has actually been repaired,
so the new "Auto-repaired" badge/note were verified by code inspection
against `StatusBadge`'s existing API and this page's existing CSS patterns,
not an actual browser render of that specific state — see Known
Limitations). **Live-verified against real Gemini, not just tests**: two
real, controlled submissions confirmed genuine real-world behavior neither
mocked test alone could prove. First, a deliberately incoherent JD
(random-word gibberish) triggered a real flagged extraction, a real repair
attempt, *and* a real transient Gemini failure on that repair call (`503
Service Unavailable`, confirmed via the container's own structured logs
showing two `generate_structured` calls under one correlation ID, the
second ending in `outcome: "error"`) — the endpoint still returned `200`
with the original extraction correctly persisted as `"flagged"`, exactly
the designed fallback behavior, encountered for real rather than simulated.
Second, two more realistic JDs both came back genuinely `"clean"` on the
first attempt (real Gemini extracted short, atomic-enough skill names),
confirming the repair pass correctly does *not* fire when it isn't needed —
zero repair calls for either. A real `"repaired"` outcome (flagged, then
genuinely fixed by the repair call) was not observed live this session —
real Gemini simply didn't happen to produce a fixable-but-initially-bad
extraction for the JDs tried, and this project's own daily Gemini quota
history (see Known Limitations) argues against spending more real calls
chasing that specific outcome when the mocked integration test already
proves it deterministically. Two disposable "Smoketest JD Repair Pass"
jobs (2 and 3) and one already-committed one (1) were left in place, same
established convention as every other session's smoketest data.

**This session (post-match JD anomaly detection, milestone 38)**: milestones
36-37 catch bad *extractions* before matching ever runs; nothing until now
caught a JD that extracts and validates perfectly cleanly but still produces
a nonsensical *matching outcome*. New pure module `match_anomaly.py`
(no DB/HTTP/LLM calls, same discipline as `jd_validation.py`/`scoring.py`),
called once from `main.py: submit_job` immediately after every retrieved
candidate's `MatchResult` is already scored and persisted. Fires only when
ALL of: at least 5 candidates were retrieved and scored; average
`semantic_score` across all of them is >= 0.5; zero candidates qualified;
and every single one failed the required-skills hard gate specifically (not
merely a low final score with a passing gate). Both thresholds are grounded
in this project's own real, already-persisted data, not invented round
numbers — checked live against the actual Postgres numbers before picking
them: the real, already-documented `Backend Engineer (Demo)` anomaly
(8 candidates, 0 qualified, 0 passed the gate) averages 0.72 semantic
relevance; a second real case found live while building this feature (a
narrative JD with no explicit skill list, real Gemini extracting compound
requirements like "Backend Microservices") averages 0.66; the one real
genuinely-irrelevant retrieval observed averages 0.44 — 0.5 sits between
those. On a fire, `merge_anomaly_warning` folds the warning into the job's
existing `extraction_status`/`extraction_warnings`: `"clean"`/`"repaired"`
become `"flagged"` (the one status this app's frontend already renders a
warning banner for); an already-`"flagged"` job stays `"flagged"`; the
append is idempotent (an equal warning is never added twice). Persisted via
a new, narrowly-scoped `db.py` function,
`update_job_description_extraction_metadata`, which updates only the
`extraction_status`/`extraction_warnings` columns on the already-committed
`JobDescription` row — never `required_skills`/`preferred_skills`/etc (the
reproducibility-critical fields, untouched), and **never any `MatchResult`
row** (there is no update path for `MatchResult` anywhere in `db.py` at
all — confirmed by grep, not just by not writing one this session).
Purely observational: no rescoring, no second Second Brain retrieval, no
second Gemini call of any kind. Frontend: **zero code changes** — the new
warning is just another entry in the same `extraction_warnings` array
`JobDetailPage`'s existing banner already iterates and renders generically,
and `"flagged"` is the same status `JobsListPage`'s existing "Needs review"
badge already keys off; reusing the exact existing UI, exactly as this
feature's own requirement asked for, needed no new conditional anywhere in
the frontend. Verified: 174 RWS unit tests (+15, all pure `match_anomaly.py`
coverage — fires only under the intended conditions, doesn't fire for weak
semantic relevance/a passing hard gate/too small a pool/an empty list, uses
the *average* score rather than a single outlier, and the merge function's
status-transition and duplicate-append idempotency), 107 repo-root
integration tests (+5, against real Postgres, `TEST_DATABASE_URL` only): a
clean-but-hallucinated extraction genuinely gets flagged post-match against
5 real (mocked) candidates; the same shape does not fire for low semantic
relevance, when a candidate passes the hard gate, or with too small a pool;
and a direct DB comparison confirms every `MatchResult` scoring field
persisted before the anomaly check ran is byte-for-byte identical to the
API response after it ran. A real-browser Playwright regression pass
confirmed the pre-existing "flagged" banner (from an unrelated, older
warning) still renders correctly with this new code path wired into
`submit_job`. **Not done this session**: a fresh, full live-Gemini
end-to-end trigger of this specific feature (submit a real job today, watch
real Gemini hallucinate a compound skill, watch this code flag it live) —
blocked by Gemini's free-tier daily quota being exhausted partway through
this session's own live verification attempts (a real `429
RESOURCE_EXHAUSTED`, `GenerateRequestsPerDayPerProjectPerModel-FreeTier`,
confirmed via the real error body, not assumed). A same-session attempt to
retroactively demonstrate this feature against the real, already-existing
`Backend Engineer (Demo)` job's real historical `MatchResult`s (which
already, unmodified, satisfy every one of this feature's own conditions —
confirmed via a read-only `SELECT`, which is precisely where the 0.72/0.66
numbers above came from) was deliberately not carried out: the auto-mode
permission system flagged the one action that would have done it (running a
script inside the container that writes to the live shared demo database
outside the normal `POST /v1/jobs` flow) as a shared-resource modification,
and rather than work around that classifier, the write was skipped — see
the reasoning in this session's own record for why that was the right call,
not a shortcut avoided. The feature's correctness against exactly that real
data shape is still fully proven (the 107 integration tests reproduce the
same numeric shape against real Postgres, just with mocked candidates
instead of Gemini in the loop), just not demonstrated through a fresh
browser screenshot of a newly-flagged live job.

**This session (JD extraction reliability end-to-end audit, milestone 39)**:
audited the full pipeline (Gemini extraction → deterministic validation →
optional repair → persistence → retrieval → scoring → post-match anomaly →
recruiter warnings) for contradictory states, warning loss/duplication,
repair using the wrong extraction, the anomaly incorrectly promoting status,
backward compatibility, transaction/partial-write risk, quota/failure
behavior, `MatchResult` reproducibility, and frontend consistency across
`clean`/`repaired`/`flagged`. Checked existing tests and real data first
(all 174 RWS unit + 107 integration tests green baseline; every real job's
`extraction_status`/`extraction_warnings` combination in the live database
inspected directly via SQL — no contradictory clean-with-warnings or
flagged-with-no-warnings rows found). **Two concrete defects found and
fixed**: (1) **MEDIUM, the post-match anomaly's own metadata write had no
error handling** — a transient DB failure at that one `UPDATE` (which runs
*after* the job and every `MatchResult` are already durably committed)
would have raised an unhandled exception, turning an otherwise fully
successful submission into an opaque `500` for the recruiter, directly
violating this feature's own "must never block job creation" design
promise (and risking a recruiter resubmitting into a duplicate job, since
job creation has no idempotency key). Fixed: the write is now wrapped in
`try/except SQLAlchemyError`, rolling back and logging on failure, and
falling back to responding with the job's real pre-anomaly state rather
than a promised-but-unwritten promotion — the response now always describes
what's actually persisted, never what merely should have been. (2)
**MEDIUM, frontend messaging.** Both `JobDetailPage`'s warning banner
("This job description's extraction looks suspicious") and
`JobsListPage`'s "Needs review" badge tooltip ("Gemini's extraction... looked
suspicious") hardcoded an *extraction*-specific claim that stopped being
universally true the moment milestone 38 added a second, unrelated cause for
the same `"flagged"` status (a clean extraction whose *match outcome* looks
wrong). A job flagged only by `match_anomaly.py` would show a banner
asserting something false about its own extraction — a real, live-jury-demo
risk (a recruiter reading a false claim and looking for a problem that isn't
there). Fixed with a minimal wording change in both places (no new
component, no new conditional structure) to describe the state generically,
letting the actual `extraction_warnings[].message` list (already rendered
verbatim) carry the specific cause. **One MEDIUM finding reported but
deliberately not fixed this session** (see Known Limitations): a real,
pre-existing partial-write risk in `submit_job`'s per-candidate scoring loop
(each `MatchResult` commits immediately; a Second Brain candidate-lookup
failure partway through leaves a durably-committed Job + JobDescription +
partial `MatchResult`s, then a `502` with no `job_id` for the client to
reference, and no idempotency key to protect a recruiter's retry from
creating a duplicate job) — this predates milestones 36-38 entirely (it's a
property of the base matching loop, not the JD-extraction-reliability layers
this audit targeted), and fixing it properly is an architecture-level
atomicity decision with real tradeoffs, not a small patch to bundle into
this audit; see `[NEXT]`. **Everything else checked came back clean**:
contradictory states, warning duplication, repair using the wrong
extraction, the anomaly incorrectly promoting status, and `MatchResult`
reproducibility (confirmed by grep: no `UPDATE`/`DELETE` path exists for
that table anywhere in `db.py`) — no defects found, verified rather than
assumed. **Live Gemini**: one real, controlled `POST /v1/jobs` was
attempted per this task's own instruction ("if quota allows") — a real `429
RESOURCE_EXHAUSTED` confirmed the daily quota (see Known Limitations) is
still exhausted from earlier in this same session; per the task's explicit
"otherwise do not work around quota" instruction, no further live attempts
were made, and the three-layer interaction / real `"repaired"` outcome gaps
milestones 37-38 both left open remain open. Verified: 174 RWS unit tests
unchanged, 108 repo-root integration tests (+1, the anomaly-persist-failure
regression), frontend build + 31 Node tests passed, and a real-browser
Playwright pass confirmed the corrected wording renders correctly (badge
tooltip and banner both updated, old inaccurate text gone, underlying
warning messages still shown, a clean job still shows neither) with zero
console errors.

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
| `GET /v1/jobs` | JWT | `match_count` via one `LEFT JOIN`, no pagination. Each row also carries `required_language`/`min_language_cefr` (both `null` if the job has no requirement). **Milestone 36**: each row also carries `extraction_status` (`"clean"`/`"flagged"`, now also `"repaired"` — milestone 37) and `extraction_warnings` (list of `{field, message}`), read from the job's `JobDescription` via an outer join — falls back to `"clean"`/`[]` defensively if that join has no match (shouldn't happen; `create_job_with_description` always creates both atomically). |
| `POST /v1/jobs` | JWT | JD structuring (Gemini) → deterministic validation/normalization (`jd_validation.py`) → **[milestone 37] one automatic Gemini repair attempt if flagged** → Second Brain retrieval → scoring → persists one `MatchResult` per candidate → **[milestone 38] post-match anomaly check (`match_anomaly.py`)**. Optional `required_language`/`min_language_cefr` (both or neither) set the job's language eligibility requirement — recruiter-set only, Gemini never sees these fields. **Milestone 36**: the structured JD is normalized (trim/dedupe/required-preferred-overlap-removal — scoring-neutral, since `compute_skill_score` targets `required ∪ preferred`) and checked against 4 deterministic rules (phrase-like required skills, total under-extraction, experience >25 years, >6 title variants) before being persisted or matched. **Milestone 37**: if flagged, `main.py`'s `repair_structured_extraction` makes exactly one additional Gemini call (original JD + first extraction + exact warnings, same schema) and re-validates the result. A genuinely fixed repair persists as `extraction_status: "repaired"` with the *repaired* requirements actually used for matching; a repair that fails outright (`LLMProviderError`) or whose own output still fails validation falls back to the *original* extraction persisted as `"flagged"` — never blocks job creation either way, never a retry loop. **Milestone 38**: after every `MatchResult` above is already scored and persisted, `match_anomaly.detect_match_anomaly` checks whether ≥5 candidates were retrieved, average `semantic_score` ≥ 0.5, zero qualified, and every one failed the required-skills hard gate specifically — a JD that extracted and validated perfectly cleanly but still produced a nonsensical match outcome (a required skill Gemini hallucinated that no real candidate will ever have verbatim). A fire promotes `"clean"`/`"repaired"` to `"flagged"` (an already-`"flagged"` job is unaffected) and idempotently appends a `field: "match_outcomes"` warning — purely observational, never rescoring, never touching any `MatchResult` row, never a second Gemini call. **Milestone 40**: the entire pipeline above is now one atomic transaction — every write only `db.flush()`es until a single `db.commit()` once everything has succeeded, so a failure anywhere (structuring, retrieval, a candidate lookup mid-loop) leaves nothing committed, not even the `Job` (the post-match anomaly write specifically runs inside its own `db.begin_nested()` SAVEPOINT, so *its* failure still can't block the commit of everything else). The response's `required_skills`/`preferred_skills`/`titles`/`min_years_experience` are always whatever was actually persisted and matched (normalized, or repaired-and-normalized), never Gemini's raw first-pass output. |
| `PATCH /v1/jobs/{job_id}/language-requirement` | JWT | Configures or clears (both null) an existing job's language requirement. Never touches any already-created `Application`'s own snapshot. **Milestone 36**: response now re-reads and surfaces the job's real `extraction_status`/`extraction_warnings` (via `get_job_description_for_job`) instead of hardcoding `"clean"`/`[]` — this naturally also surfaces `"repaired"` (milestone 37) with no further change needed here. |
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
| `POST /v1/applications/{id}/interview-rounds` | JWT | **Milestone 33.** Schedules a post-VoiceHR technical/managerial interview round — the stage after the language gate clears. 422 if the application is already `accepted`/`rejected`, or if `language_gate.status` is `pending`/`failed`/`needs_review` (same `BLOCKING_STATUSES` check `accept` already uses). Creates one `recruitment.interview_rounds` row (`scheduled`, no outcome yet) — never touches `Application.status`, which stays exactly `selected`/`invited`/`interviewed`/`accepted`/`rejected` as before; this is a parallel signal, not a new state-machine value, the same relationship the language gate itself has to that state machine. `interviewer_name` is free text, not a foreign key — this project has exactly one seeded recruiter account and no interviewer/user directory. **Milestone 41**: also requires `interviewer_email` (`EmailStr`, 422 if malformed) and accepts optional `location`; after creating the round, best-effort emails the candidate (contact fetched fresh from Resume Intelligence, never persisted) and the interviewer via `interview_round_mailer.py` (RWS's own Resend integration) — a failure at either step never blocks or rolls back the round, only sets the response's `notification_status: "failed"` with `notification_error` set. **Milestone 42**: that email now also carries a real ICS calendar attachment (`SEQUENCE: 0`, the round's own `id` as `UID`) so a real calendar client offers "Add to calendar". |
| `POST /v1/applications/{id}/interview-rounds/{round_id}/reschedule` | JWT | **Milestone 34.** Updates only `scheduled_at`; 409 if the round is not currently `scheduled` (names its actual status), 404 if the round doesn't exist or belongs to a different `application_id` than the URL's. **Milestone 42**: best-effort notifies both parties with an updated ICS (same `UID`, `SEQUENCE` incremented by one) — never blocks or reverts the reschedule itself, only sets the response's `notification_status`/`notification_error`. |
| `POST /v1/applications/{id}/interview-rounds/{round_id}/complete` | JWT | **New this session (milestone 34).** Sets `outcome` (required — `pass`/`fail`/`needs_review`, 422 if missing/invalid, checked before ever reaching the DB) and optional `notes`, `status → completed`; only from `scheduled` (409/404 as above). Omitting `notes` preserves whatever was set at scheduling time rather than clearing it. |
| `POST /v1/applications/{id}/interview-rounds/{round_id}/cancel` | JWT | **Milestone 34.** `status → cancelled`, no body; only from `scheduled` (409/404 as above). Terminal — no un-cancel. **Milestone 42**: best-effort notifies both parties with a `METHOD:CANCEL` ICS (same `UID`, `SEQUENCE` incremented once more) — never blocks or reverts the cancellation itself. |
| `POST /v1/applications/{id}/interview-rounds/{round_id}/resend-notification` | JWT | **Milestone 41.** Explicit resend of the round's candidate+interviewer email — always attempts delivery, never creates a new round, only callable while `scheduled` (409 otherwise), same 10s in-memory cooldown `resend-invite` already uses (429 on a rapid repeat). Unlike creation, a delivery failure here **is** this request's own `502` (resending has no other value if delivery fails) — `notification_status`/`notification_error` are still updated to the real outcome either way, so a later read never shows a stale pre-attempt status. **Milestone 42**: always re-delivers with `"scheduled"` wording and the round's *current* `SEQUENCE` unchanged, even after a prior reschedule — a resend is a re-delivery of the current state, never itself a change. |
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

- **RESOLVED (milestone 40): the partial-write risk in `submit_job`'s
  per-candidate scoring loop milestone 39's audit found.** `create_job_with_
  description`/`create_match_result`/`update_job_description_extraction_metadata`
  now only `db.flush()`; `submit_job` issues one `db.commit()` at the very
  end, once the whole pipeline has actually succeeded. A mid-request failure
  (Second Brain unreachable mid-loop, JD structuring failing, anything) now
  leaves nothing committed, verified by a real integration test that forces
  `SecondBrainClientError` on the 3rd of 5 candidates and confirms zero
  `Job`/`MatchResult` rows survive. See milestone 40's own entry above.
- **RESOLVED (milestone 40): milestone 38's post-match anomaly check has now
  been triggered live.** Gemini's daily quota reset (a new day) mid-session;
  a real `POST /v1/jobs` (`required_skills` genuinely extracted as
  `["Python", "REST APIs"]`, clean on the first pass) retrieved 8 real
  candidates (average semantic relevance 0.69), zero of which passed the
  required-skills hard gate — a real, live, first-time trigger of
  `match_anomaly.py` through the actual full pipeline, confirmed via
  container logs (one `generate_structured` call, no repair needed) and a
  real-browser Playwright pass. See milestone 40's own entry above.
- **Milestone 37's `extraction_status: "repaired"` outcome was not observed
  against real Gemini this session.** Two real, controlled live submissions
  did exercise the repair pass genuinely (a flagged extraction triggering a
  real repair call, including one real transient repair-call failure that
  correctly fell back to `"flagged"` without blocking job creation), but
  neither JD tried happened to produce a flagged-then-successfully-fixed
  result — real Gemini either extracted cleanly on the first attempt or
  failed the repair attempt outright for the deliberately-incoherent JD used.
  The `"repaired"` path itself is proven deterministically by 3 mocked
  integration tests (a fixed repair, a repair whose own output still fails
  validation, and a failing repair call), and the frontend's "Auto-repaired"
  badge/note were checked by direct code inspection against `StatusBadge`'s
  existing API, not an actual browser render of that specific state (no real
  job in this environment currently has `extraction_status: "repaired"`).
  Low risk, but a real one worth confirming if a future session hits it
  naturally or has quota to spare chasing it deliberately.
- **RESOLVED 2026-09-19: VoiceHR's Gemini API key/project was denied access
  by Google from milestone 25 onward; a user-provided key rotation that same
  day fixed it.** Full arc, since the history matters for anyone who hits
  this again: milestone 25 first found VoiceHR's `GEMINI_API_KEY` returning
  `403 PERMISSION_DENIED — "Your project has been denied access. Please
  contact support."` on both a Live connect and a plain evaluator call. A
  first key rotation the same day (2026-09-19) did **not** fix it — re-tested
  the same way (a real `POST /api/admin/reports/{token}/retry` against the
  one real stored report, plus raw `curl` calls straight to
  `generativelanguage.googleapis.com` bypassing all app code) and got the
  identical 403, even though the new key could list models fine (ruling out
  "wrong model name") — conclusively a project-level denial that survives
  rotating the key within that same project. A **second** key rotation,
  minutes later, resolved it: the same `/retry` call returned a real `200`
  with a genuine evaluation (`evaluation_status: "insufficient_evidence"` —
  a real, correct rubric outcome for this transcript's short length, not an
  error), confirmed independently with two raw, app-code-bypassing checks —
  a `generateContent` call (`200`, real generated text) and, critically, a
  **real Gemini Live session** using VoiceHR's own exact model
  (`gemini-3.1-flash-live-preview`) and config shape (`AUDIO` response
  modality, output transcription) via the `google-genai` SDK directly inside
  the container: real audio bytes generated, output transcription read back
  `"OK."` — the same capability the autonomous interview itself depends on,
  not just the evaluator's plain-text path. **Scope, reconfirmed**:
  RWS/Resume Intelligence/Second Brain's separate `GEMINI_API_KEY` was
  unaffected throughout, on a different project. **What changed for the
  demo**: a live VoiceHR invite/interview is a real option again, not just
  the fallback of showing an already-completed report — see "Demo readiness"
  below, updated accordingly. **One side effect worth knowing**: retrying
  the one real stored report (`70f3068e-...`, interview_id 1, "Amine Sridi")
  as part of this verification changed its stored `evaluation_status` from
  `"failed"` (the old access-denied error, now cleared) to
  `"insufficient_evidence"` (a real, working evaluation of that same short
  transcript) — the report's `overall_score`/`cefr_level` are now `null`
  instead of the old placeholder `85`/`"C1"`. This is a real, correct result
  replacing a fake error, not data loss, but it changes what that report
  shows in a live demo — see "Demo readiness."
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
- **RESOLVED (milestone 43): `RESEND_API_KEY` is now configured, in both
  VoiceHR's and RWS's own `.env` (each service needs its own — they're
  independent Resend integrations, milestones 27 and 41-42 respectively;
  only VoiceHR's was initially added, RWS's was missing and added the same
  session once this was noticed). **Live-verified real delivery**, not just a
  presence check: called each service's own mailer function directly
  (`voicehr/backend/api/routes/interview.py:_send_email`,
  `recruitment-workflow/interview_round_mailer.py:send_interview_round_notification`,
  the latter with a real ICS attachment) against the Resend account's own
  verified address — both returned success and the emails genuinely arrived.
  A real `POST /v1/applications/{id}/invite` call against a live demo
  application (`8fc853eb-bc32-445e-9e74-691ce05a81d3`) now genuinely reaches
  Resend (previously: instant "not configured" 500) and correctly leaves
  `Application.status` at `selected`/`invite_attempt_number` at `0` on
  failure, per the existing contract. **What still blocks an arbitrary real
  candidate address, and is not a code defect**: no verified custom domain is
  configured, so Resend's sandbox mode rejects everything except its account
  owner's own registered address — confirmed live, two distinct real Resend
  rejections: `@example.com` addresses (all of this environment's demo
  candidate emails) get a `422 validation_error` ("Please use our testing
  email address instead of domains like `example.com`"), and any other
  real-but-unverified address would get a `403` ("You can only send testing
  emails to your own email address"). A verified custom domain
  (`RESEND_FROM_ADDRESS` pointed at it) is required to actually invite
  arbitrary real candidate addresses; see `services/voicehr/README.md`'s
  "Resend email configuration" section and `.env.example`. **Operational
  lesson from this session**: `docker compose restart` does **not** reload a
  changed `.env` — it restarts the existing container's already-baked-in
  environment. `docker compose up -d` (which recreates a container whose
  config changed) is required after editing any service's `.env`; confirmed
  by observing `settings.resend_api_key` read back empty after a `restart`
  and correctly populated only after `up -d`.
  `ADMIN_EMAIL` is now also set (VoiceHR only; RWS has no equivalent
  setting) — harmless either way, it only gates the separate "report ready"
  admin notification, never a candidate-facing path.
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
  tests already prove. **Partially closed 2026-09-19**: Gemini access itself
  is now restored (see Known Limitations' `RESOLVED` entry), and retrying
  the one pre-existing real report against the new evaluator did produce a
  genuine `insufficient_evidence` result (not a mocked one) — but that
  retry ran the new rubric against an *old*, already-recorded short
  transcript, not a fresh interview conducted through the new Section E
  adaptive-questioning prompt end to end. The specific gap this bullet
  named — a real candidate actually experiencing Section E, producing a
  real `interaction_spontaneity` score — is still open; one controlled live
  interview is now possible to actually run, just hasn't been yet.

---

## Demo readiness

**Verdict: DEMO READY, with one remaining known gap (not the one this
section used to name).** The whole-system audit that originally produced a
DEMO READY verdict is still architecturally accurate (no code-level
`CRITICAL`/`HIGH` defect). The demo data that was wiped is restored (see
"Current demo data" under "Current state"), and the fixture responsible can
no longer repeat the incident (see the test-isolation invariant).
**VoiceHR's Gemini access, the previous headline blocker, is resolved**
(see Known Limitations' now-`RESOLVED` entry) — a live interview is a real
option again, not just a fallback story. **RESEND_API_KEY is now also
resolved (milestone 43)** — both services have a real key, and live delivery
to a deliverable address is confirmed working end to end (see Known
Limitations). **What's still genuinely blocked, and is a Resend account
setting, not a code gap**: no custom domain is verified on the Resend
account, so a live "Invite" click against any of *this environment's*
current demo candidates (all `@example.com` addresses) will still get a
real, clean `502` — Resend itself rejects `@example.com` outright with a
`422`. The only address that can currently receive a real invite is the
Resend account's own verified one. This does not block the demo sequence
below — it already routes around a live invite by using the
pre-reconnected, already-`interviewed` application for the Report Detail
leg — but a *live*, visibly-succeeding "Invite" click still needs either a
verified custom domain or a demo candidate created with the account's own
verified email address (neither exists yet). The infrastructure fix from
the original audit (`restart: unless-stopped` on all four app containers) is
still in place and still correct — note also that a changed `.env` needs
`docker compose up -d`, not `restart`, to actually take effect (see Known
Limitations).

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
re-run, so this is instant and free) → Applications → **Invite really
succeeding is now demonstrable for real** — use `Smoketest Live Invite Demo
Engineer` (milestone 44)'s already-qualified candidate/application
(`095731d3-...`) whose email is the Resend account's own verified address
(every *other* restored demo candidate is still `@example.com` and will
still 502, see the verdict above); either way, continue to → Report
Detail on application `a2769e7b-f63b-4d73-8ac3-a2e443b3d46b` (the
already-`interviewed` one, reconnected to VoiceHR's real report). **Know
before presenting it — this changed since the last time this section was
written**: this specific report's own `evaluation_status` is now
`"insufficient_evidence"`, not `"failed"` — retrying it as part of
re-confirming Gemini access (see Known Limitations' `RESOLVED` entry) ran a
real, working evaluation against its real (short) transcript, and Gemini
correctly determined there wasn't enough usable language evidence for a
confident CEFR verdict, rather than erroring out. `AssessmentPanel`
therefore shows the amber "Insufficient evidence" badge and the
`INSUFFICIENT_EVIDENCE_EXPLANATION` text instead of a score, while the real
transcript, strengths/concerns, and recording still render normally below
it — this is the exact state milestone 31/32's own frontend work was built
to handle gracefully, and now has a real (not synthetic) example to show.
Frame it the same resilience-story way as before, just with the accurate
current wording: the rubric correctly recognized a too-short interview
instead of forcing a number onto it, and the report stays fully usable
regardless. → **optional: Schedule a
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
- An invite fails because email delivery failed (Resend's sandbox rejecting
  an unverified/`@example.com` address is the expected case now that the key
  itself is configured — see Known Limitations) → a clean `502` naming the
  reason, verified live in a real browser (error banner, Invite/Accept/
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
"Email Delivery Smoke Test Engineer"`); a live, visibly-*succeeding* "Invite"
click is now demonstrable using the milestone 44 application
(`095731d3-...`, already `invited`, or re-invite it live if you want the
click itself on stage) — its candidate's email is the Resend account's own
verified address (`RESEND_API_KEY` itself is configured in both services'
`.env` — see Known Limitations — every *other* candidate here is still
`@example.com` and will still `502`, which is Resend's sandbox recipient
restriction, not a missing key); do not click Invite/Accept on
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
6. *Can you run a live candidate interview right now?* — The Gemini side is
   fixed (see Known Limitations' `RESOLVED` entry — verified with a real
   Gemini Live session generating real audio, not just a text call), and the
   *recruiter-facing* invite path can now genuinely send a real email too
   (milestone 43) — the one remaining constraint is Resend's own sandbox
   mode (no verified custom domain yet), which limits real delivery to the
   Resend account's own verified address rather than an arbitrary candidate.
   The honest answer: "both halves are confirmed working for real; sending
   to an arbitrary candidate address specifically needs a verified sending
   domain, which is a Resend account setting, not a code gap."
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
35. **VoiceHR's Gemini access denial (open since milestone 25) is
    resolved.** Not code work — the user rotated VoiceHR's `GEMINI_API_KEY`
    to a key from a working project. Verified thoroughly, not assumed: the
    container was force-recreated to guarantee the new key was actually
    loaded (checked via `printenv`, not inferred), then re-ran the exact
    historical verification (a real `POST /api/admin/reports/{token}/retry`
    against the one real stored report) plus two new checks that bypass all
    of this codebase's own code entirely — a raw `curl generateContent` call
    straight to `generativelanguage.googleapis.com` (`200`, real generated
    text) and a real Gemini Live session via the `google-genai` SDK using
    VoiceHR's own exact model (`gemini-3.1-flash-live-preview`) and config
    shape (`AUDIO` response modality, output transcription) — real audio
    bytes generated, transcription read back correctly. A *first* rotation
    that same day had not fixed it (same 403, isolated via the same
    raw-`curl` technique to a project-level denial, not a key- or
    model-specific one — documented, then superseded, in Known
    Limitations); the second rotation did. Retrying the one real stored
    report as part of this check changed its stored result from the old
    `"failed"` (access-denied error) to a genuine `"insufficient_evidence"`
    — a real, correct rubric outcome for that transcript's actual length,
    not an error and not data loss, but a change worth knowing about before
    presenting that report (see "Demo readiness," updated accordingly).
    **Not done this session**: an actual fresh end-to-end live interview
    through the real candidate-facing flow (mic, WebSocket session
    lifecycle, the new Section E adaptive-questioning prompt from milestone
    31) — only the raw Gemini connectivity was verified, deliberately
    scoped to answering "does the key work now," not "does the whole
    autonomous interview experience still work end to end." See milestone
    36's own `[NEXT]` — the VoiceHR live-interview check below was
    deliberately deferred behind it, not dropped.
36. **Deterministic JD-extraction validation (no mandatory recruiter
    review).** New pure module `jd_validation.py`, run immediately after
    Gemini's JD structuring and before persistence/matching: normalizes
    (trim/dedupe/drop required-preferred overlap — scoring-neutral) then
    checks 4 rules (phrase-like required skills, total under-extraction,
    experience >25 years, >6 title variants). New `JobDescription` columns
    `extraction_status`/`extraction_warnings` (migration `0008`, applied live
    to both the demo and test databases this session — all 8 pre-existing
    rows backfilled to `clean`/`[]` cleanly). A flagged extraction is never
    blocked, only made visible — surfaced via `GET /v1/jobs`'s existing
    response, no new endpoint. Frontend: a "Needs review" badge on the Jobs
    list, a non-blocking warning banner on Job Detail, no confirmation step.
    Live-verified end to end against the real running stack, real Gemini,
    real Postgres: a deliberately vague JD came back genuinely flagged by
    Gemini's own under-extraction, with 5 real `MatchResult`s still created.
    159 RWS unit tests passed (+24), 99 repo-root integration tests passed
    (+3), frontend build and its 31 Node tests passed, new UI strings
    confirmed present in the built bundle actually served by the container,
    and both the badge (Jobs list) and banner (Job Detail) confirmed
    rendering correctly in a real Chromium browser (Playwright, cached
    binary on this machine) — flagged job shows the badge, every clean job
    doesn't, the banner shows the exact warning message, zero console
    errors.
37. **Automatic JD repair pass for flagged extractions.** When
    `validate_and_normalize`'s first pass returns `"flagged"`, `main.py` now
    makes exactly one additional Gemini call
    (`repair_structured_extraction`) — the original JD, the first
    extraction, and the exact deterministic warnings it triggered, asking
    Gemini to fix only those specific problems and return the same
    `StructuredJobDescription` schema — then re-runs
    `validate_and_normalize` on the result. A genuine fix persists as
    `extraction_status: "repaired"` with the *repaired* requirements
    actually used for matching (migration `0009` widens the `CHECK`
    constraint to allow it); a repair call that itself raises
    `LLMProviderError`, or whose own output still fails validation, falls
    back to persisting the *original* pre-repair extraction as `"flagged"` —
    never blocks job creation either way, never a retry loop (at most one
    repair call, ever). `jd_validation.py` itself gained no Gemini
    dependency — the repair call lives in `main.py`, the one place already
    allowed to call Gemini for JD structuring. Frontend: a distinct
    "Auto-repaired" badge (violet/accent tone) on the Jobs list and a small
    informational note on Job Detail, entirely separate from `"flagged"`'s
    existing warning UX, which is unchanged. Verified: 159 RWS unit tests
    unchanged (repair is only exercisable through the full `submit_job`
    pipeline), 102 repo-root integration tests passed (+3: a mocked repair
    that succeeds, one whose own output still fails validation, one where
    the repair call itself fails), frontend build and 31 Node tests passed,
    a real-browser Playwright regression pass confirmed the existing
    flagged/clean UI is unaffected. **Live-verified against real Gemini**:
    a deliberately incoherent JD triggered a real flagged extraction, a real
    repair attempt, and a real transient repair-call failure (`503 Service
    Unavailable`, confirmed via structured logs showing two
    `generate_structured` calls under one correlation ID) that correctly
    fell back to `"flagged"` without blocking job creation; two more
    realistic JDs both extracted cleanly on the first attempt, confirming
    zero repair calls when none are needed. A genuine flagged-then-fixed
    `"repaired"` outcome was not observed live (real Gemini didn't happen to
    produce one for the JDs tried) — proven instead by the 3 mocked
    integration tests; see Known Limitations.
38. **Post-match JD anomaly detection.** Milestones 36-37 catch bad
    *extractions*, before matching ever runs; this catches a JD that
    extracts and validates perfectly cleanly but still produces a
    nonsensical *matching outcome*. New pure module `match_anomaly.py`,
    called once from `submit_job` immediately after every retrieved
    candidate's `MatchResult` is already scored and persisted — fires only
    when ≥5 candidates were retrieved, average `semantic_score` ≥ 0.5, zero
    qualified, and every one failed the required-skills hard gate
    specifically (both thresholds grounded in this project's own real data —
    see the API surface entry above and Known Limitations for the exact
    numbers). A fire promotes `"clean"`/`"repaired"` to `"flagged"`
    (an already-`"flagged"` job is unaffected) and idempotently appends a
    `field: "match_outcomes"` warning via a new, narrowly-scoped `db.py`
    function that updates only `extraction_status`/`extraction_warnings` on
    the already-committed `JobDescription` — never any `MatchResult` row
    (no update path exists for that table anywhere in `db.py`, confirmed by
    grep), never a rescore, never a second Gemini call. Frontend: zero code
    changes — reuses `JobDetailPage`'s existing warning banner and
    `JobsListPage`'s existing "Needs review" badge exactly as they already
    render for any other flagged job, per this feature's own requirement.
    Verified: 174 RWS unit tests (+15, exhaustive pure-function coverage of
    `match_anomaly.py`'s every branch plus `merge_anomaly_warning`'s
    idempotency), 107 repo-root integration tests (+5, real Postgres): fires
    under the intended conditions, doesn't fire for weak semantic relevance/
    a passing hard gate/too small a pool, and a direct DB comparison proves
    every `MatchResult` scoring field is byte-for-byte unchanged by the
    anomaly check. A real-browser regression pass confirmed the pre-existing
    warning banner still renders correctly with this new code path wired in.
    **Not done this session**: a fresh live end-to-end trigger through a new
    `POST /v1/jobs` call — Gemini's free-tier daily quota was exhausted
    partway through this session's own verification attempts (a real `429`,
    confirmed not assumed); the real, already-existing `Backend Engineer
    (Demo)` job's real historical data was independently confirmed (via a
    read-only query) to already satisfy every one of this feature's
    conditions, but a same-session attempt to demonstrate the feature by
    writing to that live data was correctly stopped by the auto-mode
    permission system's shared-resource-modification check rather than
    worked around — see Known Limitations for the full reasoning.
39. **JD extraction reliability end-to-end audit.** Audited the full
    pipeline (Gemini extraction → deterministic validation → optional
    repair → persistence → retrieval → scoring → post-match anomaly →
    recruiter warnings) against a fixed checklist: contradictory states,
    warning loss/duplication, repair using the wrong extraction, the
    anomaly incorrectly promoting status, backward compatibility,
    transaction/partial-write risk, quota/failure behavior, `MatchResult`
    reproducibility, and frontend consistency. Checked existing tests/real
    data first (174+107 green baseline; every real job's
    `extraction_status`/`extraction_warnings` combination inspected directly
    via SQL — no contradictions found). **Two MEDIUM defects found and
    fixed**: (1) the post-match anomaly's own metadata write had no error
    handling — a transient DB failure there (which only ever runs *after*
    the job and every `MatchResult` are already committed) would have
    turned a fully successful submission into an opaque `500`, violating
    this feature's own "must never block job creation" promise; now wrapped
    in `try/except SQLAlchemyError`, rolling back and responding with the
    real pre-anomaly state on failure rather than a promised-but-unwritten
    promotion. (2) frontend messaging — both `JobDetailPage`'s banner and
    `JobsListPage`'s badge tooltip hardcoded "extraction looks suspicious,"
    which stopped being universally true the moment milestone 38 added a
    second, unrelated cause (a match-outcome-only flag); fixed with a
    minimal, generic wording change in both places, no new component. **One
    MEDIUM finding reported but deliberately not fixed**: a pre-existing
    (predates milestones 36-38) partial-write risk in `submit_job`'s
    per-candidate scoring loop — see Known Limitations and `[NEXT]`.
    Everything else checked (contradictory states, warning duplication,
    repair using the wrong extraction, `MatchResult` reproducibility
    confirmed by grep) came back clean, verified rather than assumed. One
    real, controlled `POST /v1/jobs` was attempted per this task's own "if
    quota allows" instruction — a real `429 RESOURCE_EXHAUSTED` confirmed
    the quota was still exhausted; per the task's explicit "otherwise do not
    work around quota," no further attempts were made, so the two
    live-verification gaps milestones 37-38 left open remain open. Verified:
    174 RWS unit tests unchanged, 108 repo-root integration tests (+1, the
    anomaly-persist-failure regression), frontend build + 31 Node tests
    passed, real-browser Playwright pass confirmed the corrected wording
    renders with zero console errors.
40. **Atomic job submission — eliminated the partial-write risk milestone 39
    found.** `create_job_with_description`/`create_match_result`/
    `update_job_description_extraction_metadata` now only `db.flush()`,
    never commit; `submit_job` issues exactly one `db.commit()`, right
    before constructing the response, once JD structuring, validation,
    optional repair, Second Brain retrieval, every candidate's scoring, and
    post-match anomaly detection have all actually succeeded. If anything
    raises before that point, `get_db`'s own `finally: db.close()` discards
    the whole uncommitted transaction — a mid-request failure now leaves
    *nothing* behind, not an orphaned `Job` with a partial `MatchResult`
    set. Chose this over the alternative milestone 39 posed (per-candidate
    commits + an idempotency key): simpler, no new schema/API surface, and
    directly closes the defect rather than working around it — matches
    CLAUDE.md's "boring over clever" and "no infra ahead of demonstrated
    need." The one wrinkle: milestone 39's own anomaly-write fix (a failure
    there must not block job creation) still needed to hold *inside* this
    new single-transaction design, so that one write now runs inside its
    own `db.begin_nested()` SAVEPOINT — a failure there rolls back only that
    nested block, never the pending job/matches still waiting for the outer
    commit. Verified: 174 RWS unit tests unchanged (the loop's commit timing
    isn't visible to the mocked-DB suite), 109 repo-root integration tests
    (+1: a real mid-loop `SecondBrainClientError` on the 3rd of 5 candidates,
    proving zero `Job`/`MatchResult` rows survive — this needed
    `db_session.rollback()` before the assertion to simulate the real
    per-request session cleanup the test harness's shared session otherwise
    skips; without the fix, that same rollback is a no-op and the row
    survives, confirming the test genuinely discriminates old vs. new
    behavior), the pre-existing retrieval-failure test extended with the
    same DB-state check. **Live-verified against real Gemini, and this
    closed a real, previously-open gap**: Gemini's daily quota had reset
    (new day) — a real `POST /v1/jobs` (`"Smoketest Atomic Submission"`,
    required_skills genuinely extracted as `["Python", "REST APIs"]`, clean
    on the first pass, no repair needed) retrieved 8 real candidates
    (average semantic relevance 0.69) of which **zero** passed the
    required-skills hard gate — a real, live, first-time trigger of
    `match_anomaly.py` through the actual full pipeline (structuring →
    validation → persistence → retrieval → scoring → anomaly → one atomic
    commit), closing the exact live-verification gap milestone 38's Known
    Limitations recorded as open. Confirmed via real container logs (one
    `generate_structured` call, i.e. no repair attempted, since the first
    extraction was already clean) and a real-browser Playwright pass (badge
    and banner both render correctly for this genuinely-live-flagged job).
    A real `"repaired"` outcome still wasn't observed (not what this session
    was chasing) -- see Known Limitations, now narrowed to just that one gap.
41. **Interview-round notifications, phase 1: RWS-owned email delivery, no
    ICS yet.** Automates the first slice of "candidate schedules a human
    interview round → both parties actually get notified" (the post-VoiceHR
    stage has existed since milestones 33-34, but a round only ever existed
    inside RWS until now). Migration `0010` adds `interviewer_email`
    (nullable at the DB level — no sensible backfill for pre-existing rows;
    required at the API layer for every new round, `EmailStr`-validated),
    `location` (free text, optional — a meeting URL or physical address,
    recruiter's choice), and `notification_status`/`notification_error`/
    `notification_sequence` (the last reserved, unused, for phase 2's ICS
    `SEQUENCE`) to `interview_rounds`. New `interview_round_mailer.py` —
    RWS's own thin Resend integration, mirroring
    `services/voicehr/backend/services/mailer.py`'s shape rather than
    sharing code with it (two different concerns, two different services).
    On creation, RWS fetches the candidate's contact fresh from Resume
    Intelligence (never persisted — the same transient-lookup pattern
    `invite_application` already uses) and emails both the candidate and the
    interviewer; a failure at either step (contact lookup or delivery) is
    recorded as `notification_status: "failed"` with the real error and
    **never blocks or rolls back round creation** — the round is the
    durable, valuable result, notification is advisory on top of it, the
    same relationship `jd_validation`/`match_anomaly` already have to job
    submission. New `POST .../interview-rounds/{round_id}/resend-notification`
    — unlike creation, a resend's entire purpose *is* delivery, so (mirroring
    `POST .../resend-invite`'s own precedent exactly) a failure here **is**
    the request's own `502`, though the round's `notification_status` is
    still updated to the real outcome either way; only callable while
    `scheduled`, with the same 10s in-memory cooldown `resend-invite` already
    uses. Frontend: the scheduling form gained required interviewer-email and
    optional location fields; each round now shows a second badge
    (`NOTIFICATION_STATUS_LABELS`/`notificationStatusTone`,
    `constants/interviewRounds.js`) plus a "Resend notification" button that
    only appears once `notification_status === "failed"`.

    **Verification**: 185 RWS unit tests (+11: interviewer-email validation,
    provider failure still creates the round, candidate-contact failure
    still creates the round, no candidate PII in any persisted field, and
    the 6 resend-endpoint cases — success/failure/cooldown/404s), 134
    repo-root integration tests (+7, real Postgres, real Resume Intelligence
    mock): the same scenarios against a real DB, plus a dedicated "missing
    `RESEND_API_KEY`" test using the real, unmocked mailer (this test
    environment has none configured, so this doubles as that exact live
    contract). Frontend build clean, 34 Node tests (+3) passed. Migration
    `0010` applied to both the live and disposable test databases (all 8
    pre-existing rows backfilled to `pending`/`0`/null/null cleanly).
    **Live-verified end to end against the real running stack**: scheduled a
    real round via the actual browser form (real interviewer email/location
    fields) against a real, already-curated demo application — created
    successfully, `notification_status` correctly showed `"failed"` (no
    `RESEND_API_KEY` configured live either, exactly the designed contract),
    the "Resend notification" button correctly appeared, zero console errors
    beyond one pre-existing, unrelated candidate-identity 404 (a synthetic
    test candidate with no real Resume Intelligence contact record — the
    same already-documented "(name unavailable)" fallback this app has
    always had, not something this session introduced).
42. **Interview-round notifications, phase 2: ICS calendar attachments +
    reschedule/cancel notifications.** Closes the feature phase 1 deferred.
    `interview_round_mailer.py` gained `build_ics` (one RFC 5545 VEVENT,
    `\r\n` line endings, `UID` derived only from the round's own `id` —
    stable across its whole lifetime, so a reschedule updates the same
    calendar entry rather than creating a duplicate) and `email_content`
    (wording keyed by `kind: "scheduled"|"rescheduled"|"cancelled"`), both
    promoted to public functions specifically so they get the same direct,
    no-mocking pure-function test treatment `jd_validation.py`/
    `match_anomaly.py` already do (new `test_interview_round_mailer.py`,
    16 tests). Every notification email now carries the same ICS as an
    attachment (`text/calendar; method=REQUEST` or `method=CANCEL`,
    base64-encoded per Resend's attachment API). `reschedule`/`cancel`
    (previously silent) now both best-effort notify, same "never blocks the
    underlying action" contract creation already has — a delivery failure
    never un-reschedules or un-cancels a round, it only shows up in
    `notification_status`/`notification_error`. `SEQUENCE` (`notification_
    sequence`, reserved-but-unused since migration 0010) increments by one
    on reschedule and on cancel — the event itself genuinely changed — but
    *not* on create's first send or a plain resend, both of which re-deliver
    the *current* state rather than changing it (RFC 5545's own distinction
    between an update and a duplicate). Cancel sends `METHOD:CANCEL` on the
    same `UID` with the incremented `SEQUENCE`, the standard mechanism a
    real calendar client uses to remove an event it already added. No new
    external dependency — still a plain ICS text attachment via the same
    Resend call, not a calendar-provider API integration; Google Calendar
    remains explicitly deferred, exactly as the feature's own original
    design specified.

    Found and fixed one real bug while live-testing this: `notification_
    sequence` was never actually added to `InterviewRoundResponse` in phase
    1 (the DB column and mailer logic were correct, but the API response
    silently omitted it) — caught by a real `curl`-equivalent live check
    against the running container, not by any automated test, since none of
    phase 1's own tests happened to assert on that specific response field.
    Fixed and back-filled with explicit `resp.json()["notification_sequence"]`
    assertions at every point that matters, specifically to close that class
    of gap going forward.

    **Verification**: 201 RWS unit tests (+16, all pure `interview_round_
    mailer.py` coverage: UID stability across a reschedule, different round
    ids get different UIDs, SEQUENCE rendered verbatim, REQUEST/CONFIRMED vs
    CANCEL/CANCELLED, DTSTART/DTEND from the default 60-minute duration,
    RFC 5545 text escaping, location included/omitted correctly, every
    `kind`'s wording). 141 repo-root integration tests (+9, real Postgres,
    mocked mailer + real Resume Intelligence contact lookup): create sends
    `sequence=0`; reschedule sends `kind="rescheduled"`/`sequence=1` and
    persists it; rescheduling twice increments to 2, never resets, same
    `UID` throughout; cancel sends `kind="cancelled"`/`sequence=1` and marks
    the round cancelled; a resend after a reschedule uses `"scheduled"`
    wording and the *unchanged* current sequence; missing `RESEND_API_KEY`
    fails cleanly on both reschedule and cancel without blocking either.
    Frontend build/34 Node tests unaffected (zero frontend changes needed —
    `reschedule`/`cancel` already returned the same `InterviewRoundResponse`
    shape milestone 41's badge/resend-button logic already renders
    generically). **Live-verified end to end against the real running
    stack**: a real create → reschedule → cancel sequence against a real
    curated demo application showed `notification_sequence` go `0 → 1 → 2`
    exactly as designed (confirmed via the API directly, which is what
    caught the missing-field bug above), and a real-browser pass confirmed
    the notification badge keeps rendering correctly through a real
    reschedule and cancel, zero new console errors beyond the same
    pre-existing orphaned-candidate 404. A real calendar client (Gmail/
    Outlook) actually offering "Add to calendar" from a real received email
    was not observed live — no `RESEND_API_KEY` is configured in this
    environment, so no real email has ever actually been delivered; the
    ICS's own correctness is instead fully proven by direct inspection (a
    real generated ICS was printed and hand-verified against RFC 5545) and
    the 16 dedicated unit tests, not by a real inbox.

**This session (real `RESEND_API_KEY` configured, milestone 43)**: a real
Resend key was added to VoiceHR's `.env`; RWS's own `.env` had no
`RESEND_API_KEY` entry at all (each service needs its own — they're
independent Resend integrations) and was missing it, so it was added there
too. **Caught one real operational trap while doing this**: `docker compose
restart` does not reload a changed `.env` file — it restarts the existing
container's already-baked-in environment unchanged. Confirmed directly:
`settings.resend_api_key` read back empty (`''`) inside the container after
a `restart`, and only populated correctly after `docker compose up -d`
(which recreates the container because its config changed). Both services
were then live-verified with **real Resend deliveries, not just a
credential-presence check**: called VoiceHR's `_send_email` and RWS's
`send_interview_round_notification` (the latter with a real ICS attachment)
directly against the Resend account's own verified address —
both genuinely succeeded and the emails arrived. Then verified the real
*product* path: a live `POST /v1/applications/{id}/invite` against demo
application `8fc853eb-bc32-445e-9e74-691ce05a81d3` now reaches Resend for
real (previously an instant "not configured" failure) and correctly still
leaves `Application.status` at `selected`/`invite_attempt_number` at `0` on
failure — the failure itself is now a genuine Resend `422 validation_error`
("Please use our testing email address instead of domains like
`example.com`"), since every candidate email in this environment's demo
data uses `@example.com`. This is expected, not a bug: Resend's sandbox mode
(no verified custom domain configured) only accepts the account's own
registered address as a recipient — confirmed both failure shapes live,
`422` for a disallowed placeholder domain and `403` for a real-but-unverified
one. No code changed this session — this was configuration plus live
verification of already-implemented milestones 27/41-42, closing the gap
those milestones' own write-ups left open ("no real email has ever actually
been delivered"). See Known Limitations and `[NEXT]` for what this does and
does not unblock for the live demo.

**This session (a real, deliverable invite fully set up end to end,
milestone 44)**: milestone 43 proved the mailers work in isolation; this
went one step further and drove the actual recruiter-facing pipeline for
real, with a genuinely deliverable candidate. A new candidate was created
through the real upload path (`POST /v1/resumes`, a hand-built minimal PDF
whose text content gives the candidate's email as the Resend account's own
verified address) — real Gemini structured extraction ran and correctly
pulled that email out (confirmed via `GET
/v1/candidates/{id}/identity`), candidate `e7e2f34f-5c61-4eb8-8ee6-67c80e161b75`.
A new job, `Smoketest Live Invite Demo Engineer`
(`f7d2f474-5284-4d6e-81ef-6fcb31d89c0d`), was created with required skills
matching this candidate's resume exactly, so a real Gemini JD-structuring +
real Second Brain retrieval + real scoring pass genuinely qualified this
candidate (`final_score: 0.909`, hard gate passed) alongside the existing
candidate pool, no scoring shortcuts taken. That match was selected into a
real application (`095731d3-a1ba-4738-82b4-3c2a9182602f`), then a real
`POST /v1/applications/{id}/invite` call succeeded outright —
`status: "invited"` — the **first successful live "Invite" click in this
project's history**; every prior session's attempt failed, first because no
key existed, and even after milestone 43's key existed because every demo
candidate's email is `@example.com`. Confirmed directly in VoiceHR's own
SQLite (not just trusting the 200 response): `invite_email_sent_at` is set,
`invite_email_error` is `NULL`, for this interview row specifically — the
same query against this project's history of invite attempts had never once
returned a non-null `invite_email_sent_at` before. **A real VoiceHR
interview invitation email, containing a real, working interview link, is
sitting in the Resend account's own inbox right now.** No code changed this
session either — this was entirely live data setup through already-existing
real endpoints (resume upload, job creation, select, invite), deliberately
not a direct DB write, specifically so every step genuinely exercises real
Gemini + real Second Brain + real Resend, not a shortcut. This candidate/
job/application are left in place as new, real demo data (same convention
as every other session's smoketest artifacts) — `e7e2f34f-...` is a
**usable, non-orphaned candidate** (unlike `0a699701-...`), and this
specific application is now the one to point to for demonstrating a live,
succeeding "Invite" click.

**This session (candidate interview page redesign, milestone 45)**: the
VoiceHR candidate-facing interview page (`services/voicehr/frontend/
interview.html` + its rules in `theme.css`) carried its own bespoke dark
"midnight" theme — different hex values, `Inter`/`Georgia` fonts, its own
button gradient — visually disconnected from the actual, current design
system the rest of the product runs (RWS's V2 "AI recruitment command
center": deep charcoal base, translucent glass panels, one signature
cyan-violet gradient, aurora-bloom+grid+noise backdrop, plain system-ui
font, documented in `services/recruitment-workflow/frontend/src/styles/
variables.css`). Ported the real `--v2-*` token values into `theme.css`
under a new, clearly-commented `:root` block, scoped so only the interview
page's own selectors read them — `.linen-page` (admin/report, a separate,
deliberately-untouched light theme) never does, confirmed by a live
regression screenshot of `/admin` showing it completely unaffected. Rebuilt:
same three-layer aurora/grid/noise backdrop as the recruiter Dashboard, glass
card/task panels, the exact cyan-to-violet gradient CTA button pattern, a
brand mark identical to the recruiter sidebar's (same icon, same "Company
Second Brain" wordmark) so a candidate arriving from the invite email
recognizes it as the same product, and the live-transcript panel (previously
a stray light-themed box hardcoded inline, inconsistent with the dark page
around it even before this change) moved into `theme.css` as a proper V2
glass panel. Consolidated two near-duplicate override sections that both
targeted `.midnight-page`'s selectors into one canonical section, and
removed the CSS custom properties (`--midnight`, `--night-*`, `--line-dark`,
`--surface-dark`, `--shadow-dark`) that redesign left with zero remaining
uses (confirmed by grep before removing). Kept the orb/wave audio
visualizer concept as-is, re-tinted — a re-skin, not a UX rethink. Zero JS
behavior changed; interview.html's inline `<style>` now holds only the
step-visibility rules the page's own script logic depends on. **One real
bug found and fixed during live verification**: the wizard-step glass-panel
rule (`.midnight-page .step:not(#step-interview)`) was a bare descendant
selector that also matched the small task-progress-dot elements deep inside
`.task-card` (they share the plain `.step` class for their current/done
state) — that gave them a full card's padding/background instead of staying
thin 26px bars, caught via a real-browser screenshot showing them rendered
as large squares. Fixed by scoping to `.midnight-page > .card > .step` (a
direct-child combinator the nested dots can't satisfy). Verified live via
Playwright screenshots (desktop + the existing 560px mobile breakpoint)
through consent, mic-test, interview-in-progress (orb/wave/task-card/live-
transcript rendered via direct `showStep()` calls rather than a real
Gemini Live session, deliberately, to avoid consuming a real interview
token), done, terminated, retry, mic-lost, and error states — all render
correctly, no console errors beyond a pre-existing unrelated 404. Used a
throwaway `pending`-status interview row inserted directly for the
screenshot pass, deleted immediately after. No RWS frontend files touched.

**This session (whole-project audit, milestone 46)**: a full 12-area audit
(architecture, backend correctness, security/privacy, AI/ML, workflow,
VoiceHR, frontend, database, infra/ops, tests, performance,
maintainability), written to `PROJECT_AUDIT_AND_IMPROVEMENT_PROPOSAL.md` at
the repo root. Every claim that mattered was re-verified against source
rather than taken from these handoffs — several handoff claims were
confirmed accurate (the HNSW index really does use `vector_cosine_ops`
matching the `<=>` operator actually queried; every FK that is filtered on
really is indexed; `process_interview`'s `finalized_at` claim really does
have no `await` before it; transcript preservation across a reconnect really
is the primary path, since `gemini_live.py` catches `WebSocketDisconnect`
internally). **No CRITICAL findings**; the central architectural claim (the
LLM never decides anything) was attacked specifically and held. Three HIGH
findings, all at the seams *between* services, plus 13 MEDIUM and 14 LOW.
See that file for the full findings, severities, and the phased roadmap.

**This session (audit phase 1 — integrity & privacy, milestone 47)**:
implemented the audit's own phase 1. Four fixes, each with a test that was
first proven to fail against the pre-fix code:

1. **H1 (HIGH, privacy defect): candidate email no longer reaches RWS's
   database.** `interview_round_mailer._send_email` built its errors as
   `f"Email send to {to} failed: {response.text}"` — where `to` *is* the
   candidate's address on the candidate send, and where Resend's own
   rejection body echoes the address back as well. `main.py` persists that
   string verbatim into `recruitment.interview_rounds.notification_error`
   (and returns it in the API response), so any delivery failure wrote
   candidate PII into RWS's own storage — directly against CLAUDE.md's
   "candidate PII lives only in Resume Intelligence". Errors now carry a
   category plus the provider's HTTP status only
   (`"candidate: Email delivery rejected by provider (HTTP 403)"`), which
   still distinguishes the real cases (403 unverified sending domain, 422
   disallowed recipient, transport error) without naming anyone. The same
   sanitization was applied to `resume_intelligence_client.get_contact`,
   whose error reaches the same column by the same path and came from the
   PII-owning service. **Live check before the fix**: the four existing
   failed rows contained only pre-`RESEND_API_KEY` error types, so nothing
   had leaked yet — but with a real key now configured and Resend's sandbox
   rejecting every non-verified recipient, the next round scheduled for a
   normal demo candidate would have been the first.
2. **H2(b) (HIGH, idempotency defect): the webhook receipt and the state
   change it authorizes are now one transaction.** `insert_webhook_event`
   used to commit on its own, *before* `record_interview_result` ran in a
   separate transaction. A crash or DB error between the two left the
   `delivery_id` permanently recorded while the application was never
   updated — and since that UNIQUE `delivery_id` **is** the dedup mechanism,
   every VoiceHR retry then returned `duplicate_ignored`, discarding a real
   interview result forever with no recovery path (the application stays
   `invited`, the language gate stays `pending`, and `pending` blocks accept
   while reject still works — a silently one-sided outcome). Both
   `insert_webhook_event` and `mark_webhook_event_processed` now flush only;
   `record_interview_result` no longer commits or rolls back; the webhook
   route owns the transaction and commits deliberately on each path
   (including the `no_op`/`unknown_application` paths, where the receipt is
   still worth keeping). A concurrent duplicate now blocks on the unique
   index until the first transaction resolves and then correctly loses with
   `IntegrityError`, exactly as before.
3. **M1 (MEDIUM, correctness defect): a candidate with no email no longer
   500s.** `resume_intel.candidates.email` is nullable and `store_candidate`
   explicitly inserts such rows, but `ContactLookupResponse.email` was a
   required `EmailStr` and the route constructs that model directly — so a
   null email raised `ValidationError` *inside* the handler and surfaced as
   an opaque 500, which RWS reported as "Resume Intelligence unavailable".
   The field is now `Optional`, and the two RWS callers that genuinely need
   an address (invite, interview-round notification) check for it explicitly
   and report that specific reason (a 422 "Candidate has no email address on
   file" for invite). Both halves of this were already tested separately;
   their intersection was not, and that is the gap that hid it.
4. **Three inaccurate documentation claims corrected.** `CLAUDE.md`'s PII
   invariant now names its one real exception (VoiceHR persists
   `candidate_name`/`candidate_email` on its own `interviews` rows, because
   it owns emailing and greeting the candidate) while explicitly keeping
   that exception scoped and non-precedent-setting;
   `candidate_summary._synthesize_raw_text`'s docstring no longer claims no
   email "can" reach the embedding text (the allow-list is field-level, so
   free-text `work[].description` could carry one — stated precisely instead
   of overclaimed); `GeminiProvider`'s docstring no longer names a stale
   default model.

**The test that claimed to guard H1 did not.**
`test_create_interview_round_never_persists_candidate_contact` asserted
`notification_status == "sent"` (success path only) and checked four columns
— `interviewer_name`, `interviewer_email`, `location`, `notes` — **excluding
`notification_error`**, the only column that can hold a failure message.
Fixed: `notification_error` added to that loop, plus a new failure-path
integration test asserting the persisted column and the API response stay
clean, plus 5 new unit tests that run the **real** `_send_email` /
`send_interview_round_notification` against a stubbed httpx transport and
assert the recipient address, the candidate's name, and the provider body
never appear in what they raise.

**Also found and fixed while verifying: three integration tests had been
making live HTTP calls to `api.resend.com` on every run.**
`test_{create,reschedule,cancel}_missing_resend_api_key_fails_cleanly*`
depended on the *ambient* absence of a key ("no RESEND_API_KEY is configured
in this test environment"), which silently stopped being true when milestone
43 configured a real one. They were failing, and — worse — hitting the real
provider with `@example.com` recipients and asserting on whatever it
answered. A new `resend_api_key_unset` fixture pins
`main.settings.resend_api_key = None` for exactly those three, so they test
the branch they name, deterministically and offline. Verified afterwards
that the whole suite makes zero requests to `api.resend.com`.

**Verification**: 206 RWS unit tests (+5), 139 repo-root unit tests, 80
Resume Intelligence tests (+1, the untested no-email/contact intersection),
and **146 repo-root integration tests (+5, from 141), all passing** — run
in-container against the disposable `company_second_brain_test` database.
The +5: the no-email invite 422, the failure-path PII guard, and three
webhook-receipt durability tests covering the processed/no-op/unresolvable
paths. Two existing tests were also strengthened rather than added to:
the PII guard now includes `notification_error` in its column loop, and
`test_webhook_unexpected_error_returns_500` now asserts the receipt is
**not** left behind after a failed state change — the exact lost-result
window this phase closed. Each new guard was additionally proven to fail
against the pre-fix code by temporarily reverting the fix, running it, and
restoring — so none of them is a test that would pass either way.

**Live-verified against the real running stack, not just tests**: scheduled a
real interview round on demo application
`8fc853eb-bc32-445e-9e74-691ce05a81d3` (candidate
`smoketest.rws.notify@example.com`) with an `@example.com` interviewer, so
Resend's sandbox genuinely rejected both sends. The round was created
(`201`), `notification_status` came back `failed`, and the persisted
`notification_error` is exactly `"candidate: Email delivery rejected by
provider (HTTP 422); interviewer: Email delivery rejected by provider (HTTP
422)"` — no address, no provider body, while still naming which side failed
and why. A regex scan of `notification_error` across the **entire**
`interview_rounds` table then returned **0** rows containing anything
matching an email address. `GET /v1/jobs`, `GET /v1/applications`, and
`GET /v1/graph/candidates-jobs` all still return `200` after the webhook
transaction change, and the sanitized error is what the API hands the
frontend. That round (`2141e4a6-...`, status `scheduled`,
`notification_status: failed`) is left in place as this session's smoketest
artifact, same convention as every prior session's.

**This session (audit phase 2 — cross-service recoverability, milestone 48)**:
closed the two remaining HIGH findings, both at the VoiceHR→RWS seam. The
completion webhook is sent exactly once, best-effort, and RWS only ever
accepted a result for an `invited` application — so a single badly-timed
restart stranded the application at `invited` with its language gate
`pending`, which **blocks accept while leaving reject available**. A
recruiter could reach only the negative outcome, with nothing on screen
explaining why, while VoiceHR held a complete report the whole time.

1. **H2 — a lost result is now recoverable.** New service-key-gated
   `POST /api/rws/interviews/{external_reference_id}/resend-result` on
   VoiceHR re-delivers an already-evaluated result over the same signed
   webhook, rebuilt from the stored `reports` row
   (`rws_client.scores_from_report_row`) — **no Gemini call**, which matters
   given this project's repeatedly-exhausted free-tier quota. Each
   re-delivery generates a fresh `delivery_id` on purpose: RWS dedups by
   that id, so reusing the original would make every resend a no-op on its
   side, which is exactly the stuck state this exists to leave. Deliberately
   *not* a retry queue or a reconciliation poller — both are explicitly out
   of scope per CLAUDE.md, and the data is already stored, so re-sending it
   is one call.
2. **H2(c) — RWS now accepts a corrective result.** `record_interview_result`
   matches `invited` **or** `interviewed`, so a late or better result lands
   instead of being dropped. `accepted`/`rejected` are still never
   overwritten: a terminal decision was made on the evidence available then,
   and silently rewriting that evidence afterwards would make the audit trail
   lie about what the recruiter actually saw. The delivery is still recorded
   in `interview_webhook_events` either way, so a late correction after a
   decision is auditable rather than invisible.
3. **H3 — `retry_report` now re-notifies RWS.** The admin re-evaluation
   rewrote every score locally and told RWS nothing, so the two systems
   silently diverged: VoiceHR showed the new report while RWS kept the old
   assessment — and RWS's language gate decides on *its* copy, so a recruiter
   could be blocked from accepting a candidate whose report visibly passes.
   (This is why fix 2 was a prerequisite: without it the notification would
   have been a no-op.)
4. **Recruiter-facing recovery**: `POST /v1/applications/{id}/refresh-interview-result`
   (JWT) asks VoiceHR to re-deliver, so this is reachable without a service
   key. Deliberately a re-delivery request rather than RWS pulling the report
   and applying it itself — VoiceHR stays the only place that shapes an
   assessment and the signed webhook stays the only path that applies one; a
   pull would fork both. The webhook is received and applied *during* that
   call, which is why the endpoint re-reads the application before responding.

**First real cross-service contract tests (audit M8)**: until now every
cross-service call was tested with the client module monkeypatched away, so
a renamed route, a changed auth header, or a dropped status code would pass
every test in the repository and only break live. `services/voicehr/tests/
test_rws_contract.py` drives **RWS's genuine client against VoiceHR's
genuine ASGI app**, replacing only the network (`httpx.ASGITransport`).
Proven to work: temporarily renaming the route in `voicehr_client.py` makes
it fail. Scoped to this one boundary on purpose; the helper in that file is
the pattern to extend to the invite/report calls.

**Verification**: 219 VoiceHR (+14: 10 re-delivery, 4 contract), 206 RWS
unit, 139 repo-root unit, 80 Resume Intelligence, **153 integration (+7:
two corrective-webhook cases and five for the new endpoint)** — all passing.
**Live-verified the whole loop against the real running stack**: called
`refresh-interview-result` for application
`095731d3-a1ba-4738-82b4-3c2a9182602f`, which was already `interviewed`.
RWS called VoiceHR, VoiceHR re-sent the signed webhook with a fresh
`delivery_id`, and RWS **applied** it — `interview_completed_at` moved from
`2026-09-21 18:36:59` to `2026-09-23 15:29:02`, a second
`interview_webhook_events` row landed with `processed = true`, and VoiceHR
recorded the matching `webhook_delivery_id`/`webhook_delivered_at`. Before
this change that second delivery was a silent `no_op` and the endpoint did
not exist.

**Still open from the audit** (see
`PROJECT_AUDIT_AND_IMPROVEMENT_PROPOSAL.md`): phase 3 (AI cost caps M3/M4,
dependency pinning M7, automated migrations M6, container hardening), phase
4 (skill normalization M13, embedding `task_type` M11 — both change scores,
so both need versioning), phase 5 (maintainability). One phase-2 item was
deliberately left for the frontend: surfacing "interviewed but no result" in
the UI so a recruiter can see the stuck state and click the new endpoint —
the backend is ready for it, and `FRONTEND_HANDOFF.md` owns that work.

**This session (audit phase 3 — cost, reproducibility, ops, milestone 49)**:
the remaining MEDIUM/LOW findings that protect the demo itself rather than
fixing a defect.

1. **M4 — the evaluator's two retry loops no longer multiply.** They retried
   for different, individually sensible reasons (inner: a transient
   429/503/timeout; outer: a response that parsed but was missing dimension
   scores) and neither knew about the other, so their budgets multiplied:
   3 x 2 meant **one interview could burn six full-transcript evaluation
   calls** — a third of this project's documented 20/day free-tier quota.
   Both now draw from one shared `_CallBudget`
   (`_MAX_GEMINI_CALLS_PER_EVALUATION = 3`), keeping each loop's own
   behaviour and capping the product instead of the factors. **Worth noting
   how the test for this was found to be worthless and fixed**: the obvious
   scenario (every call returns 503) caps at 3 under the *old* code too, so
   the first version of the test passed against the very bug it was meant to
   catch. Only transient errors that *eventually* yield an incomplete
   response make the outer loop restart a fully-consumed inner one; the test
   now uses exactly that sequence, and was re-verified to fail against the
   old independent budgets.
2. **M3 — one read of a job's matches can no longer fire N sequential Gemini
   calls.** `GET /v1/jobs/{job_id}/matches` generated an explanation per
   uncached match, serially, in one request: with `top_k` defaulting to 20
   that was 20 serial calls from a single page view, against that same
   20/day quota, and could time out before finishing. Now capped at
   `_MAX_EXPLANATIONS_GENERATED_PER_REQUEST = 5` *new* generations per
   request — cached explanations are always returned in full and cost
   nothing. Since matches are ordered by `final_score` descending the capped
   slice is the top of the ranking, and each later read fills in the next
   few, so the whole set still ends up explained. No frontend change needed:
   `JobDetailPage` already renders the explanation block conditionally, so a
   not-yet-generated one simply doesn't appear.
3. **M7 — every dependency is now pinned.** `fastapi`, `uvicorn[standard]`,
   `pydantic`, `pydantic-settings`, `python-multipart`, `httpx` and `pytest`
   were unpinned across all four services, so two builds of the same commit
   could install different majors. They are pinned to the versions actually
   running and tested (read out of the live containers, not guessed) —
   which also surfaced a real drift: `uvicorn` was 0.53.0 in two services
   and 0.52.4 in the other two, purely because they were built on different
   days. **Deliberately not done: removing `pytest` from the runtime
   images.** The audit recommended it; that recommendation was wrong for
   this project and is now corrected in the requirements files themselves.
   The repo-root integration suite *must* run inside the container, because
   host-side psycopg cannot reach the Docker Postgres through the WSL2 port
   proxy — dropping pytest would trade a few MB for the entire integration
   suite. **Scope of this pin, stated honestly**: direct dependencies only.
   Transitive ones (e.g. `starlette`, pulled by `fastapi`) are still resolved
   at build time, so two builds far enough apart could still differ there.
   Fully closing that needs a lock file (`pip-compile`/`uv`), which is a new
   tool and workflow — worth it if this ever needs byte-identical rebuilds,
   but pinning what this project's own files actually declare removes the
   realistic failure (a FastAPI/Pydantic major landing mid-project) at no
   added tooling cost.
4. **M6 — migrations now run automatically, before the app serves.**
   Nothing ran them before: a clean-volume `docker compose up` produced
   containers reporting healthy (`/health` never touches the DB) while every
   real request 500'd, until someone manually ran `alembic upgrade head` in
   each of three services — a step encoded in no file. Each Dockerfile's
   `CMD` is now `alembic upgrade head && exec uvicorn ...` (`exec` so uvicorn
   still receives SIGTERM directly; a failed migration exits non-zero rather
   than serving against a wrong schema).
5. **L7 — the two internal services no longer publish host ports.**
   `resume-intelligence` (8001) and `second-brain` (8002) are only ever
   called by RWS over the compose network and never by the browser;
   publishing them only widened the attack surface, and for a PII-owning
   service at that. Verified first that nothing in the repo referenced those
   host ports.

**Deliberately not done: L8, running containers as a non-root user.** Checked
what it would actually take: RWS and Second Brain write nothing to disk at
runtime and Resume Intelligence only uses `tempfile` (so `/tmp`), meaning
three of the four would take a one-line `USER`. VoiceHR would not — it writes
to `/app/storage`, a root-owned named volume, so it needs ownership plumbing
the others don't. That asymmetry is the argument against doing it now: a
hardening applied to three services and quietly skipped on the one that
actually persists data is more misleading than no hardening at all, and by
CLAUDE.md's own test ("what concrete problem does this solve in this
project") a localhost-only demo stack gains nothing concrete from it. Worth
doing properly — all four, with the volume ownership handled — if this ever
runs anywhere shared; it is recorded here rather than silently dropped.

**Proving M6 without risking the demo data.** A true clean-volume test would
mean `docker compose down -v`, and this project has already lost its demo
database once to a careless destructive command. So instead: created a
throwaway `company_second_brain_migrationcheck` database on the same
Postgres with the same three schemas/roles/extension the init scripts
create, ran each service's real `alembic upgrade head` against it exactly as
the new container startup does, and confirmed a fully provisioned system
came out of an empty one — **11 recruitment tables, 2 resume_intel, 3
second_brain, plus the seeded recruiter and scoring config**. Then dropped
it and confirmed both real databases were still present. No volume was
touched.

**Verification**: 220 VoiceHR (+1, the shared-budget test), 206 RWS unit,
139 repo-root unit, 80 Resume Intelligence, **154 integration (+1, the
explanation cap)** — all passing, the integration suite run against the
freshly rebuilt image. **Live-verified on the real stack**:

- *M6/M7*: recreated `csb-recruitment-workflow` from the newly built image.
  Its startup log shows the `alembic.runtime.migration` lines **before**
  uvicorn's, the container reaches healthy, and `/proc/1/cmdline` is
  `uvicorn main:app` rather than `sh` — confirming `exec` worked, so uvicorn
  is PID 1 and still receives SIGTERM directly on `docker stop`. Installed
  versions inside the image match the pins exactly (`fastapi==0.141.1`,
  `uvicorn==0.53.0`, `pydantic==2.13.5`, `httpx==0.28.1`, `pytest==9.1.1`).
  The upgrade is a no-op here because the demo database is already at head —
  which is the point: it is safe to run on every boot.
- *L7*: `curl` to `localhost:8001`/`8002` now fails from the host, while
  `docker exec csb-recruitment-workflow` reaches both over the compose
  network (200), and the recruiter endpoints that actually proxy through
  them (`/v1/candidates` via Resume Intelligence,
  `/v1/candidates/{id}/knowledge` via Second Brain) still return 200.

`csb-resume-intelligence` was recreated from its rebuilt image too and shows
the identical pattern (alembic lines first, healthy, `/proc/1/cmdline` =
`uvicorn api:app`) — a second, independent confirmation of the same
mechanism. All five containers are healthy and every recruiter path
(`/v1/jobs`, `/v1/candidates`, `/v1/applications`,
`/v1/graph/candidates-jobs`, VoiceHR `/health`) returns 200.

**Honest gap, environmental not code**: `second-brain` and `voicehr` are
**still running their previous images** — this machine's DNS failed partway
through the rebuilds (`lookup registry-1.docker.io: no such host`, i.e.
Docker could not even resolve the registry to pull the base image), and
three retries each did not get through. What those two images are therefore
missing: second-brain's `alembic upgrade head` startup step (its code change
is committed and is character-for-character the same one already verified
live on the other two services), and VoiceHR's shared evaluator call budget
(M4 — covered by its 220 passing host-side tests, just not yet running in
the container). Neither gap affects anything currently demonstrable: both
services are healthy and fully functional on their existing images. Re-run
`docker compose build second-brain voicehr` once the network is back; build
services **sequentially**, for the reason in the next paragraph.

**One real operational finding worth keeping**: pinning invalidated pip's
layer cache, so the first full rebuild reinstalls everything — and on this
machine's connection (~70 KB/s to PyPI) four parallel service builds
saturated the link and one failed with `Could not find a version that
satisfies the requirement fastapi==0.141.1 (from versions: none)`. That
message reads like a bad pin but is not: the *same* version downloaded
successfully in another service's build in the same run — "from versions:
none" means pip could not reach the index at all. Building the services
sequentially avoids it. Worth remembering before assuming a pin is wrong.

---

## [NEXT]

**A real, fresh end-to-end VoiceHR interview.** Every automatable
prerequisite is now done — the last remaining step is not a coding task.
**Real finding this session**: the milestone 44 interview link was, in fact,
already used once (found live via `/admin`'s Completed Reports list while
regression-checking the redesign above) — "Amine Verified Demo Candidate"
shows `status: inconclusive` in VoiceHR's own SQLite (`started_at`/
`completed_at` only ~30 seconds apart) and the admin reports list shows
"Evaluation unavailable" / `OVERALL: N/A`. This was not a fresh, complete,
five-section attempt — 30 seconds cannot have reached Section E — so this
task's actual goal (confirm Section E's adaptive follow-up and a real
`interaction_spontaneity` score) is still open. Whether that short attempt
was a deliberate quick check of the page/link or a real failed attempt is
not something this session determined; either way, a genuine, complete,
five-section attempt still has not been observed since milestone 31's
rubric rewrite. A fresh interview would need a new invite (the existing
application is now past `invited`) or VoiceHR's own direct link/admin
surface as a fallback (see VoiceHR's README).

Milestone 35 confirmed VoiceHR's Gemini access itself works again (a real
Live session generating real audio, a real evaluator call producing a real
result) — but only at the raw connectivity level. Two things have never been
observed together since milestone 31's rubric rewrite: a *fresh* interview
actually reaching the new Section E adaptive follow-up question, and a real
transcript producing the new `interaction_spontaneity` dimension score.
Concrete scope when picked up: using the real invite link already sent
(milestone 44) or VoiceHR's own direct interview link/admin surface as a
fallback (see VoiceHR's README), conduct one real,
controlled interview end to end in a real browser — mic permissions, the
full five-section flow including Section E, a real completion — then check the
resulting report: does `interaction_spontaneity` get a real score, does the
rest of the rubric look sane, does the webhook reach RWS correctly, does the
language gate compute correctly against it.

Separately, also opportunistic and also needing a human with inbox access,
not more code: send one real interview-round notification (milestones
41-42) to a deliverable address and confirm a real calendar client (Gmail/
Outlook) actually offers "Add to calendar" from the received email, and that
a follow-up reschedule/cancel updates or removes that same calendar entry
rather than creating a duplicate. The ICS's own correctness is fully proven
by direct inspection and 16 unit tests, and milestone 43 proved the email
itself genuinely arrives — only the visual "does a real calendar client
render it right" step remains.

Everything else in Known Limitations above (no pagination, Second Brain's
missing test suite, the write-only `candidate_feedback` sink, VoiceHR's
unauthenticated `admin.html`) remains deliberately deferred per this
project's own "no infra/refactor ahead of demonstrated need" rule, not a gap
waiting on the next session.
