# Company Second Brain — Project Audit & Improvement Proposal

> ## Implementation status
>
> **Phase 1 (Integrity & privacy) — DONE, 2026-09-23.** See `handoff.md`'s
> milestone 47 for the full record.
>
> | Finding | Status |
> |---|---|
> | **H1** — candidate email persisted in `interview_rounds.notification_error` | **Fixed.** Errors now carry a category + HTTP status only. Verified live: a deliberately failed real send now stores `"candidate: Email delivery rejected by provider (HTTP 422); interviewer: …"`, and a regex scan of the whole column across the live database returns **0** rows containing an email address. |
> | **H2(b)** — receipt committed before the state change (lost-result window) | **Fixed.** Receipt + state change are now one transaction owned by the webhook route. |
> | **M1** — candidate with no email → 500 from `/contact` | **Fixed.** `ContactLookupResponse.email` is `Optional`; the two callers that need an address report that specific reason (422). |
> | Inaccurate doc claims (PII invariant, `candidate_summary` docstring, stale model name) | **Corrected.** |
> | The test that claimed to guard H1 but didn't | **Fixed and extended**, plus 5 new unit tests against the real mailer. |
>
> Verification: 206 RWS unit / 139 repo-root unit / 80 Resume Intelligence /
> **146 repo-root integration** tests passing. Every new guard was proven to fail
> against the pre-fix code before being accepted. One extra defect was found while
> verifying: three integration tests had been silently making **live HTTP calls to
> `api.resend.com`** since a real key was configured — fixed, and the suite now
> makes zero third-party calls.
>
> **H2(a)/H2(c) and H3 remain open** — they are Phase 2 (result re-delivery and a
> corrective-assessment path). Everything below is the original audit, unedited.

**Audit date:** 2026-09-22
**Scope:** Whole repository — 4 services (Recruitment Workflow / Resume Intelligence /
Second Brain / VoiceHR), shared contracts, React frontend, infrastructure, tests.
**Method:** `CLAUDE.md`, `handoff.md`, and `FRONTEND_HANDOFF.md` were read first, then
**every claim that mattered was re-verified against source**, against the Alembic
migrations, and (where possible) against the live Postgres database. Several handoff
claims were confirmed accurate; two were found to overstate what the code actually does,
and those are reported below.
**Constraint honoured:** no code was modified during this audit.

### How findings are labelled

| Label | Meaning |
|---|---|
| **DEFECT** | Code does something incorrect or unsafe today. Reproducible from source. |
| **WEAKNESS** | Works today, but the structure invites failure or blocks change. |
| **OPTIMIZATION** | Correct, but measurably wasteful. |
| **SCALABILITY** | Fine at demo scale; breaks at a larger one. Not urgent. |
| **EXTERNAL** | Not a code problem. Environment/credential/quota. |

Severity is judged **relative to this project's actual goals** (a defended final-year
internship project with production-*oriented* design), not against a hypothetical
large-scale SaaS deployment.

---

## Summary of findings

| # | Severity | Type | Area | Finding |
|---|---|---|---|---|
| H1 | **HIGH** | DEFECT | Privacy | Candidate email address gets persisted into RWS's own database via `interview_rounds.notification_error` |
| H2 | **HIGH** | DEFECT | Workflow / idempotency | An interview result can be permanently lost between VoiceHR and RWS, silently blocking acceptance forever |
| H3 | **HIGH** | DEFECT | Cross-service consistency | `retry_report` re-evaluates an interview but never re-notifies RWS — the two systems silently diverge |
| M1 | MEDIUM | DEFECT | Correctness | A candidate with no extracted email causes a 500 from Resume Intelligence `/contact` |
| M2 | MEDIUM | WEAKNESS | Transactions / perf | `submit_job` holds one DB transaction open across every remote call, plus N+1 cross-service fetches |
| M3 | MEDIUM | OPTIMIZATION | AI cost | `GET /v1/jobs/{id}/matches` can fire N sequential Gemini calls in a single request |
| M4 | MEDIUM | OPTIMIZATION | AI cost | Nested retry loops allow up to 6 full-transcript evaluation calls per interview |
| M5 | MEDIUM | WEAKNESS | Security | The webhook dedup key is outside the HMAC signature, while a signed copy exists and is ignored |
| M6 | MEDIUM | WEAKNESS | Operations | No automated migration step anywhere; fresh bring-up is manual and unverified |
| M7 | MEDIUM | WEAKNESS | Supply chain | Core dependencies unpinned; `pytest` ships inside RWS's runtime image |
| M8 | MEDIUM | WEAKNESS | Tests | No automated test ever runs two real services against each other |
| M9 | MEDIUM | WEAKNESS | Tests | 29 frontend components/pages, 0 with automated tests |
| M10 | MEDIUM | WEAKNESS | Privacy | VoiceHR is a second permanent PII store with unauthenticated, non-expiring report links |
| M11 | MEDIUM | OPTIMIZATION | Retrieval quality | Embeddings never set `task_type` for an asymmetric JD↔resume retrieval task |
| M12 | MEDIUM | WEAKNESS | Maintainability | `ApplicationsPage.jsx` — 890 lines, one component holding ~15 `useState` |
| M13 | MEDIUM | WEAKNESS | Matching quality | Three JD-quality layers exist to detect symptoms of one brittle exact-match skill gate |
| L1–L14 | LOW | mixed | various | See the Low-severity section |
| E1–E2 | — | EXTERNAL | — | Resend sandbox restriction; Gemini free-tier quota |

**No CRITICAL findings.** Nothing in this codebase corrupts data, exposes an
authentication bypass, or allows an unauthenticated actor to reach recruiter or
candidate data. That is a genuine result, not a courtesy — the auth boundaries were
checked individually and they hold (see §3).

---

## 1. Architecture

### Verified strengths (do not change)

- **Service boundaries are real, not nominal.** Three Postgres schemas, three
  least-privilege roles, each granted `USAGE, CREATE` on exactly its own schema and
  nothing on the others — verified in `infra/postgres/init/03_roles_and_grants.sh`. There
  is no cross-schema grant to abuse, so "services talk only over HTTP" is enforced by the
  database, not by convention.
- **The LLM genuinely cannot decide anything.** `scoring.py` is pure (no DB/HTTP/LLM),
  `qualified = hard_gate_passed and final_score >= threshold` is plain Python, and Gemini
  only ever writes the advisory `explanation` *after* the score is persisted. VoiceHR
  mirrors this: the evaluator returns dimension scores, and `weighted_overall()` /
  `cefr_level_for_score()` compute the verdict in code (`evaluator.py:240-264`). This is
  the project's strongest architectural claim and it survives inspection.
- **`MatchResult` reproducibility is well-designed** — snapshot of candidate data, JD
  version, scoring config version, threshold, and embedding model version all stored on
  the row. Confirmed by grep that no `UPDATE`/`DELETE` path exists for that table.
- **Parallel-signal modelling is consistent.** Both the language gate and interview
  rounds are deliberately *not* new `Application.status` values. Having applied the same
  pattern twice, the codebase now has a real, teachable convention rather than an ad-hoc
  state machine.

### M13 — Three JD-quality layers compensate for one brittle matcher · MEDIUM · WEAKNESS

**Files:** `services/recruitment-workflow/scoring.py:98-109`, `jd_validation.py`,
`match_anomaly.py`, `main.py:455-478`

**Evidence.** `check_required_skills_gate` requires *exact*, case-folded string equality
for every required skill. In the same module, `compute_title_score` (`scoring.py:58-71`)
deliberately uses bidirectional substring matching. So the file applies "fuzzy matching is
acceptable" to the low-stakes signal (title) and "exact only" to the one that can
disqualify a candidate outright.

Three subsystems exist to detect the fallout: `jd_validation.py` flags phrase-like
required skills, `main.py`'s repair pass spends a second Gemini call trying to fix them,
and `match_anomaly.py` detects "every candidate failed the hard gate despite relevant
profiles". `jd_validation.py:31-38` names the cause explicitly: *"check_required_skills_gate
requires an exact, case-insensitive match … A required-skill entry that reads like a
responsibility phrase will not match any real candidate, no matter how qualified."*

**Why it matters.** Roughly 400 lines plus ~20 tests, plus one extra Gemini call per
flagged job, exist to *observe* a problem rather than reduce it. `"PostgreSQL"` vs
`"Postgres"`, `"REST API"` vs `"REST APIs"` still silently disqualify.

**This is a deliberate deferral, and defensible** — `CLAUDE.md` explicitly defers "skill
taxonomy/synonym normalization". The finding is not "you were wrong to defer"; it is
that a *cheaper middle path* was never considered: deterministic token normalization
(case, punctuation, simple plural folding, whitespace) inside `_normalize`, with no
taxonomy, no synonym list, and no new dependency. That removes a large share of false
negatives while keeping the gate exact and explainable.

**Fix:** extend `scoring._normalize()` to fold punctuation/plurals; add golden tests
asserting the pairs above now match and that `"Java"` still does **not** match
`"JavaScript"`. **Effort: S. Risk: MEDIUM** — it changes qualification outcomes, so it
must be versioned (bump `scoring_config` or record a normalization version on
`MatchResult`) to keep historical results explainable.

### WEAKNESS — RWS `main.py` is 2,006 lines and owns everything

Routing, auth dependencies, two Gemini prompts, the notification orchestrator, three
cooldown caches, and the SPA mount all live in one module. It is well-organised and
heavily commented, but it is the single place every future change lands. Splitting by
router (`jobs`, `applications`, `interview_rounds`, `proxies`, `webhooks`) is mechanical
and low-risk. **Effort: M. Risk: LOW.**

### Correctly avoided over-engineering

No broker, no Redis, no repository layer, no DI framework. `interview_round_mailer.py`
duplicating ~30 lines of VoiceHR's mailer instead of sharing an abstraction is the right
call and is justified in its own docstring. **Keep this.**

---

## 2. Backend correctness

### H2 — An interview result can be permanently lost, silently blocking acceptance · HIGH · DEFECT

**Files:** `services/voicehr/backend/services/rws_client.py:104-108`,
`services/recruitment-workflow/main.py:1927-1957`, `db.py:1137-1172`, `db.py:947-949`

Three individually-verified gaps compound:

**(a) The webhook is sent exactly once, best-effort.** `notify_rws_interview_result`
catches `httpx.HTTPError`, logs, and returns `False`. No retry, no queue, no reconciler.

**(b) RWS commits the dedup row *before* applying the state change.**
`insert_webhook_event` ends in `db.commit()` (`db.py:1161`). `record_interview_result` is
a *separate* transaction (`db.py:961`). A crash, restart, or DB error between the two
leaves `delivery_id` permanently recorded with `processed = false` — and every retry of
that delivery now hits the `UNIQUE` constraint and returns `duplicate_ignored`
(`main.py:1937-1942`). The result is discarded forever.

**(c) Late deliveries are discarded by design.** `record_interview_result` only matches
`WHERE status = 'invited'`. Any other state → `rowcount 0` → `no_op`.

**Why it matters.** The failure is *asymmetric and silent*. The application stays
`invited`; `compute_language_gate` returns `pending`; `pending` is in `BLOCKING_STATUSES`,
so **accept is permanently blocked while reject still works**. A single network blip at
the wrong moment quietly pushes a candidate toward rejection, with no in-product signal
and no recovery path. VoiceHR holds a complete, correct report the whole time.

Note the irony: `interview_webhook_events.processed` — the exact column a recovery pass
would key on — is written on every delivery and, verified by grep, **never read by any
query**.

**Fix (small, does not violate the "no broker/no reconciler" rule):**
1. Make (b) one transaction: drop the `commit()` from `insert_webhook_event`, commit once
   after `record_interview_result`. A concurrent duplicate then blocks on the unique index
   and correctly loses — same guarantee, no window. **Effort: S. Risk: LOW.**
2. Add a service-key-gated `POST /api/rws/interviews/{external_reference_id}/resend-result`
   to VoiceHR (all data is already stored) and let RWS accept a corrective assessment for
   an application already past `invited`. **Effort: M. Risk: MEDIUM.**
3. Surface `pending`-gate-with-no-result in the recruiter UI so the state is visible.

### H3 — `retry_report` never re-notifies RWS · HIGH · DEFECT

**File:** `services/voicehr/backend/services/processing.py:132-212` (contrast `:116-119`)

`process_interview` ends with `await notify_and_record_interview_result(...)`.
`retry_report` — the admin "Retry" action — re-runs `evaluate()`, rewrites every score,
`cefr_level`, and `inconclusive`, updates the interview's own status … and never notifies
RWS. There is no call to the RWS client anywhere in that function.

**Why it matters.** After a retry, VoiceHR shows a good report while RWS still holds the
old failed/inconclusive assessment. The **language gate decides on stale data**, so the
recruiter can be blocked from accepting a candidate whose report visibly passes. This is
not hypothetical: `handoff.md` records a real retry performed on demo application
`a2769e7b-…` while re-confirming Gemini access.

Compounded by H2(c): even if the notification were added, RWS would reject it, because
the application is no longer `invited`. **Both halves must be fixed together.**

**Fix:** call `notify_and_record_interview_result` at the end of `retry_report`, and allow
RWS to apply a corrective assessment for an already-`interviewed` application.
**Effort: S–M. Risk: MEDIUM** (it makes the assessment mutable — a deliberate, documented
exception to write-once).

### M1 — A candidate with no email 500s Resume Intelligence's `/contact` · MEDIUM · DEFECT

**Files:** `services/resume-intelligence/api.py:285-290`,
`shared/schemas/__init__.py:41-47`, `services/resume-intelligence/db.py:83,118-139`

`ContactLookupResponse.email` is a **required** `EmailStr`. `candidates.email` is
**nullable**, and `store_candidate` explicitly supports it: *"If the parsed JSON has no
email, always insert a new row — we cannot dedup"*. The route constructs the model
directly, so `email=None` raises `ValidationError` **inside the handler** → unhandled →
**500**.

Downstream, RWS treats any non-200/404 as a transport failure:
- `invite_application` → `502 "Contact lookup failed: …"` instead of the designed,
  actionable `422 "Candidate contact information not found; cannot invite to interview"`
  (`main.py:1672-1676` — the intended branch is unreachable for this case).
- `get_candidate_identity` → `502 "Resume Intelligence unavailable"`, so the recruiter
  cannot even see that candidate's name.
- `_notify_interview_round` → `notification_status='failed'` with a misleading error.

**Test-coverage gap, precisely located.** `/contact` is tested for missing key (401),
wrong key (401), unknown id (404), known id (200), malformed id (422). Storing a
candidate with `email=None` is *also* tested
(`tests/integration/test_resume_intelligence.py:169-186`). **Their intersection is not
tested** — the one combination that fails.

**Fix:** make `ContactLookupResponse.email` `Optional`, and have RWS's invite pre-flight
explicitly 422 on a missing email (keeping identity/display working). Add the missing
test. **Effort: S. Risk: LOW.**

### M2 — One DB transaction held open across every remote call · MEDIUM · WEAKNESS

**File:** `services/recruitment-workflow/main.py:492-655`; `shared/http_client.py:78`

Milestone 40 correctly made `submit_job` atomic. The side effect is that the transaction
opens at `create_job_with_description` (`:492`) and stays open through Second Brain
retrieval (`:509`) and **one `get_candidate` HTTP call per retrieved candidate** (`:539`,
inside the loop, up to `top_k=100`), only committing at `:655`.

So a single job submission can hold a Postgres connection `idle in transaction` for the
full duration of 1 Gemini call + 1 + N HTTP round trips. Additionally `call_service`
constructs a **new `httpx.AsyncClient` per call** (`shared/http_client.py:78`), so there
is no connection reuse across those N fetches.

Also note `submit_job` is `async def` while using a **synchronous** SQLAlchemy session —
every DB call blocks the event loop of a single-worker uvicorn process.

**Why it matters.** Correct today at demo scale. But it couples transaction lifetime to
third-party latency: a slow Second Brain turns into a long-held transaction and a
connection-pool exhaustion mode, and `idle_in_transaction_session_timeout` would abort the
whole submission.

**Fix (preserves atomicity — do not regress milestone 40):** fetch all candidate data
*before* opening the write transaction — retrieve, then gather all `get_candidate` calls
(ideally `asyncio.gather` over a shared `AsyncClient`), score in memory, then open the
transaction and write everything in one short burst. **Effort: M. Risk: MEDIUM.**

### Verified correct (no action)

- **Conditional-`UPDATE`-with-`WHERE`-status** for every state transition
  (`transition_application`, `record_invite_attempt`, `record_interview_result`,
  `reschedule/complete/cancel_interview_round`) — genuinely race-safe; `rowcount == 0`
  correctly means "someone else got there first".
- **`select_candidate`** relies on the DB `UNIQUE` constraint and converts `IntegrityError`
  to 409, rather than check-then-act.
- **The post-match anomaly write is wrapped in `db.begin_nested()`** with a
  `SQLAlchemyError` fallback that responds with the genuinely-persisted pre-anomaly state
  (`main.py:627-652`). This is careful, correct work.
- **`invite_attempt_number` is computed before the VoiceHR call and persisted only after
  success**, which is what makes the idempotency key stable across a retry.
- **The interview-round lifecycle** correctly refuses to notify on `complete` (nothing
  changed for the calendar) and correctly does not increment `SEQUENCE` on a plain resend.

---

## 3. Security & privacy

### H1 — Candidate email is persisted into RWS's database · HIGH · DEFECT

**Files:** `services/recruitment-workflow/interview_round_mailer.py:218,221,281`,
`main.py:1130-1131`, `db.py:1039-1047`

The chain is unconditional:

1. `_send_email(..., to=candidate_email, ...)` raises
   `NotificationDeliveryError(f"Email send to {to} failed: {response.text}")` — **`to` is
   the candidate's email address**, and `response.text` is Resend's raw body, which in
   this exact environment contains an email address too (the sandbox 403 message).
2. `send_interview_round_notification` catches it and re-raises
   `f"candidate: {exc}"` (`:281`).
3. `_notify_interview_round` returns `("failed", str(exc))` (`main.py:1131`).
4. `update_interview_round_notification` writes that string into
   **`recruitment.interview_rounds.notification_error`** and commits (`db.py:1046-1047`).

It is then also returned by the API (`InterviewRoundResponse.notification_error`) and
rendered in the recruiter UI.

**Why it matters.** `CLAUDE.md` states as a non-negotiable, *"treat as strict, not a
guideline"* rule: *"Candidate PII (name, email, phone, address, raw resume) lives **only**
in Resume Intelligence"* and *"Every other service uses the opaque `candidate_id`."* This
writes candidate PII into RWS's own durable storage. The architecture goes to real lengths
elsewhere to avoid exactly this (transient contact lookups, `extra="forbid"` schemas,
identity endpoints with no `db` dependency) — this path quietly undoes it.

**Live verification.** I queried the running database. The four existing failed rows
contain only `"Candidate contact not found"` and `"Email delivery not configured…"` —
both raised *before* `_send_email`, so **no PII is in stored data yet**. That is luck of
timing: those failures predate the `RESEND_API_KEY` configured on 2026-09-21. Now that a
real key exists, **every** delivery failure takes the leaking path — and in this
environment Resend's sandbox rejects every recipient that isn't the account's own
address, so the *next* interview round scheduled for a normal demo candidate will write
their email address into the column.

**The test that claims to cover this does not.**
`tests/integration/test_interview_rounds_integration.py:380-406`,
`test_create_interview_round_never_persists_candidate_contact`, asserts
`notification_status == "sent"` (success path only) and then checks exactly four columns
— `interviewer_name`, `interviewer_email`, `location`, `notes` — **excluding
`notification_error`**, the only column that can hold it. This is a textbook case of a
test conferring false confidence.

**Fix:** never interpolate the recipient address or a provider response body into a
persisted error. Store a category plus the provider status code (e.g.
`"candidate: provider rejected (403)"`); log the detail, unlogged of PII, at `warning`.
Then extend the test to cover the **failure** path and to assert on `notification_error`
as well. **Effort: S. Risk: LOW.**

### M5 — Webhook dedup key is outside the signature · MEDIUM · WEAKNESS

**Files:** `main.py:1899,1910-1911,1928-1930`; `shared/schemas/__init__.py:147`

The HMAC covers the raw body only. Dedup uses the `X-Delivery-Id` **header**, which is
*not* signed — while `WebhookPayload.delivery_id`, a signed field carrying the same value,
exists in the body and is never used.

Practical impact is small (the body cannot be altered, so a replay re-applies an identical
transition, and `record_interview_result` no-ops after the first). But the security
property is weaker than it appears, and the fix is nearly free: use `payload.delivery_id`
as the dedup key, or require it to equal the header. There is also no timestamp/nonce
freshness check on `completed_at`. **Effort: S. Risk: LOW.**

### M10 — VoiceHR is a second permanent PII store · MEDIUM · WEAKNESS

**Files:** `services/voicehr/backend/database.py:38-49`,
`backend/api/routes/report.py:20-21,82-84`

`interviews.candidate_name` and `interviews.candidate_email` are stored permanently in
VoiceHR's SQLite. `GET /api/report/{report_token}` is **unauthenticated** — a bearer-URL —
and returns `candidate_name`, `candidate_email`, every score, and the **full transcript**.
The token is a `uuid4` (122 bits, `os.urandom`-backed — genuinely unguessable, not a
defect), but it **never expires and cannot be revoked**, and it is emailed to `ADMIN_EMAIL`
on completion.

So `CLAUDE.md`'s "PII lives only in Resume Intelligence" is, as literally written,
**not accurate**. The boundary is genuinely enforced for RWS and Second Brain; VoiceHR is
a real, necessary exception (it must email and greet the candidate) that no document
states. There is also no retention limit and no erasure path.

**Fix:** (1) correct the invariant's wording in `CLAUDE.md`/`handoff.md` to name VoiceHR
as a scoped, deliberate second PII store — an honest architecture is worth more at a
defence than an overstated one; (2) give report tokens an expiry consistent with the
interview link's 7 days. **Effort: S. Risk: LOW.**

### Verified correct (no action)

- **Three auth mechanisms are genuinely independent.** `require_recruiter` reads
  `Authorization` + `JWT_SECRET`; `require_service_key` reads `X-Api-Key` +
  `SERVICE_API_KEY`; the webhook uses HMAC only. Neither can satisfy the other.
- **JWT handling is correct**: `HS256` with an explicit `algorithms=[...]` allow-list
  (blocks `alg: none`/confusion), `exp` enforced by PyJWT, bcrypt password hashing.
- **`require_admin_auth`** uses `secrets.compare_digest` for *both* fields combined with
  non-short-circuiting `&` — timing-safe, and a deliberate detail.
- **`serve_recording`** normalises with `Path(filename).name`, defeating traversal, and is
  admin-gated because filenames are sequential and guessable.
- **The frontend has zero `dangerouslySetInnerHTML` / `innerHTML` / `eval`** (verified by
  grep), and no service API key ever reaches the browser.
- **Logging discipline is real**: `get_candidate_identity`, `get_candidates`, and
  `get_candidate_knowledge` log only the exception *type* and a non-PII id, never the
  message. `shared/llm.py` never logs prompts or completions.

**L2 (LOW, WEAKNESS)** — that discipline is inconsistent: `clients.py:39-41` and
`resume_intelligence_client.py:55-57` embed the upstream `resp.text` into an exception,
which `main.py:516` and `:1671` then return verbatim as a client-facing `detail`. Same
codebase, two standards.

---

## 4. AI / ML

### Verified strengths

- **The evaluator prompt is genuinely good** (`evaluator.py:47-74`): explicit rubric,
  fixed weights the model is told it must not apply itself, `<transcript>` delimiters, a
  dedicated **prompt-injection resistance paragraph** treating the transcript as untrusted
  data, an explicit refusal to score pronunciation without audio, and a
  `sufficient_evidence` boolean so a thin sample yields *no* verdict rather than a
  fabricated one. Enforced with `response_json_schema`.
- **`pronunciation_intelligibility` is nulled at read time, unconditionally**
  (`report.py:109-119`), so historical rows cannot resurrect a score the system no longer
  stands behind. That is unusually disciplined.
- **The repair pass is correctly bounded** — at most one extra call, only on `flagged`,
  never a loop, and the repaired output is re-validated deterministically rather than
  trusted (`main.py:455-478`).
- **Retrieval infrastructure is correct**: HNSW index with `vector_cosine_ops`
  (`0002_candidate_knowledge_table.py:31-35`) matching the `<=>` operator actually used —
  the docstring's claim was verified, not assumed.

### M3 — `GET /v1/jobs/{id}/matches` can fire N sequential Gemini calls · MEDIUM · OPTIMIZATION

**File:** `main.py:734-757`

On the first read of a job's matches, the loop calls `get_or_create_explanation` **once
per match, sequentially, inside one HTTP request**. With `top_k` defaulting to 20, a cold
job is 20 serial Gemini calls in one request — against a free tier documented in
`handoff.md` as **20 requests/day**. One page view can exhaust the daily quota, and the
request may time out before finishing.

**Fix:** generate explanations lazily per-match on demand (the recruiter expands a row),
or cap to the top N shown, or move generation to a `BackgroundTask`. **Effort: S–M.
Risk: LOW.**

### M4 — Nested retry loops allow 6 evaluation calls per interview · MEDIUM · OPTIMIZATION

**File:** `evaluator.py:162-186` inside `:392-422`

`_evaluate_with_gemini` retries up to **3** times with backoff; `evaluate` wraps it in a
**2**-attempt loop. Worst case: **6 full-transcript evaluation calls** for one interview —
on a 20/day quota. Neither loop knows about the other.

**Fix:** make the retry budget explicit and shared (one constant, one place).
**Effort: S. Risk: LOW.**

### M11 — Embeddings never set `task_type` · MEDIUM · OPTIMIZATION

**File:** `shared/llm.py:174-188`

`embed_text` passes only `output_dimensionality`. Both sides — the candidate summary
(document) and the JD query — are embedded identically. Gemini's embedding API supports
`RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY`, which exist precisely for asymmetric search like
"JD text vs. résumé text".

Because `semantic_score` is 50% of `final_score` by default, retrieval quality feeds
directly into qualification. And since changing it invalidates **every stored embedding**,
the cost of fixing it only grows.

*(Checked and dismissed: truncating to 768 dims without renormalising is harmless here,
because cosine distance is magnitude-invariant. Not a defect.)*

**Fix:** set the two task types; re-embed existing candidates once; record the task type
alongside `embedding_model_version` for reproducibility. **Effort: S (code) / M (re-embed).
Risk: MEDIUM** — historical `semantic_score` values become non-comparable, so it must be
versioned.

### L1 — `temperature=0.2` on deterministic extraction · LOW · WEAKNESS

`shared/llm.py:139` uses `temperature=0.2` for `generate_structured`, which is how JD
structuring and the repair pass run. The same JD submitted twice can therefore extract
different `required_skills` and produce different qualification outcomes — in a pipeline
whose headline property is reproducibility. `temperature=0` costs nothing here.

### EXTERNAL (operational impact only, not code defects)

- **E1 — Resend sandbox.** A real key is configured, and delivery is proven to work, but
  no verified domain exists, so Resend accepts only the account's own address. Every
  invite/notification to a normal demo candidate (`@example.com`) fails with a clean
  `422`/`403`. **This directly enables H1's leak path**, which is why H1 is urgent now
  rather than theoretical.
- **E2 — Gemini free-tier quota** (20 req/day, repeatedly exhausted per `handoff.md`).
  Amplified by M3 and M4; both are worth fixing for that reason alone.

---

## 5. Recruitment workflow (end-to-end)

Path traced in full: Resume → Candidate → JD → Matching → Selection → VoiceHR →
Language gate → Interview rounds → Decision.

**The state machine is coherent.** `selected → invited → interviewed → accepted|rejected`,
with accept/reject reachable from any non-terminal state — correct, because a recruiter
must always be able to decide. The language gate blocks only `accept`, never `reject`,
which is the right asymmetry.

### Gaps found

1. **H2/H3 (above)** are the significant workflow defects: an interview result can be lost
   or become stale, and the *only* consequence visible to the recruiter is that acceptance
   silently stops being possible.
2. **WEAKNESS — an assessment is write-once with no correction path.**
   `record_interview_result` matches only `status = 'invited'`. There is no supported way
   to correct, re-deliver, or amend an assessment. Fine when everything works; no recourse
   when it doesn't.
3. **WEAKNESS — no un-invite / no invitation expiry awareness.** VoiceHR expires links
   after 7 days; RWS never learns. An application can sit in `invited` forever with no
   in-product indication that the link is dead. A recruiter's only exit is reject.
4. **Observation, not a defect — `interview_rounds.outcome` is advisory.** A recruiter can
   accept a candidate who failed the technical round. This is deliberate and documented
   ("recruiter remains final decision-maker") and I agree with it; worth stating explicitly
   at the defence, because it will be asked.
5. **Friction — `POST /v1/jobs` has no idempotency key.** A recruiter who resubmits after a
   timeout creates a second job with a second full set of `MatchResult`s and a second
   Gemini spend. Milestone 40's atomicity means no *partial* job is created — but nothing
   prevents a *duplicate* one.

---

## 6. VoiceHR

### Verified strengths

- **Candidate-only by construction, not policy.** The recruiter frontend has zero
  WebSocket usage; VoiceHR's admin surface has no live-session route. The Gemini API key
  never leaves the container — the browser holds only a same-origin socket.
- **The exactly-once completion guard is correct.** `process_interview` claims the
  interview with `UPDATE … WHERE finalized_at IS NULL` **before any `await`**
  (`processing.py:39-47`), so on a single event loop a concurrent second caller reliably
  sees `rowcount == 0` and returns before doing any work. The reasoning in the comment is
  sound and I verified there is no `await` ahead of it.
- **The per-token `asyncio.Lock`** is a legitimate single-owner guard for a
  single-worker deployment, and re-checking row status *inside* the lock makes the
  check-then-acquire race benign.
- **Transcript preservation across reconnects is real** — `gemini_live.py` catches
  `WebSocketDisconnect` internally and returns `ended_reason="browser_disconnect"`, which
  is the branch that persists `pending_transcript` (`session.py:100-114`). I checked this
  specifically because the outer `except` branches do *not* preserve it; they are
  unreachable backstops for exceptions that escape the pipeline, where no transcript
  exists to save. Correct as written.
- **`evaluate()` never raises** — it always returns a shaped result — so a failed
  evaluation degrades to `evaluation_status: "failed"` rather than an exception, and the
  language gate fails safe to `needs_review`.

### Findings

- **H3** (retry does not re-notify) — see §2.
- **LOW · WEAKNESS — no recovery from a failed finalization.** The `finalized_at` claim
  happens *before* the report INSERT and file write. If one of those fails (DB/IO — not
  evaluation, which cannot raise), the interview is permanently claimed with no report,
  and any reconnect re-runs the whole interview only to be discarded by the claim check.
  Narrow, but there is no "unclaim on failure".
- **LOW · WEAKNESS — `/admin.html` is reachable unauthenticated** via
  `app.mount("/", StaticFiles(...))` (`main.py`), bypassing the `require_admin_auth` on
  `/admin`. Already documented; residual exposure is low because the page is an empty
  shell whose APIs are protected. Worth closing anyway by moving the static mount to
  `/static`.
- **LOW · WEAKNESS — ad-hoc schema migration.** `init_db()` does `ALTER TABLE … ADD
  COLUMN` in `try/except` on every boot, with no version tracking. Deliberate (VoiceHR is
  "pre-existing, unmigrated"), but the legacy rebuild branch copies only 11 columns and
  would drop the RWS integration columns if it ever fired.

---

## 7. Frontend

### Verified strengths

- A real design system (`variables.css`) with documented tokens, consistent status
  semantics, and an app-wide `:focus-visible` treatment.
- `client.js` is a genuinely good single choke point: JWT attachment, 401→login redirect,
  error normalisation, `FormData` passthrough, and a separate blob path for audio.
- `usePolling` is careful — pauses on `document.hidden`, uses a ref to avoid stale
  closures, swallows transient errors rather than blanking rendered data.
- **Code splitting is done where it matters**: `MatchGraph3D` is `React.lazy`-loaded, so
  three.js/3d-force-graph land in a separate 1.25 MB chunk.

### M12 — `ApplicationsPage.jsx`: 890 lines, ~15 `useState` in one component · MEDIUM · WEAKNESS

`ApplicationRow` (`:186-654`) alone owns invite, retry, resend, accept, reject, round
scheduling (6 form fields), and round lifecycle — roughly 15 independent `useState` hooks,
several mutually exclusive (`inviting`/`resending`/`deciding`/`scheduling` can never be
true together). `InterviewRoundItem` repeats the same busy/error pattern.

**Fix:** extract `ScheduleRoundForm` into its own component; collapse the mutually
exclusive flags into one `useReducer` status. **Effort: M. Risk: LOW.**

### L12 — 847 KB main bundle · LOW · OPTIMIZATION

Measured from the committed build: `dist/assets/index-*.js` is **847,110 bytes**
uncompressed. Cytoscape + `react-cytoscapejs` are imported eagerly by `GraphPage`, which
is itself not lazy — so every visitor downloads the 2D graph engine to view the login page.
Lazy-loading the `/graph` route would likely halve it. Low real-world impact for a
localhost admin tool; easy win. **Effort: S. Risk: LOW.**

### LOW · WEAKNESS — JWT in `sessionStorage`

A deliberate, documented choice, and reasonable given the app has no XSS sinks. Worth
knowing the tradeoff: an httpOnly cookie would remove token theft from the XSS blast
radius at the cost of CSRF handling. **Not recommended to change now** — the current
choice is defensible and the alternative adds moving parts.

---

## 8. Database

### Verified strengths

Schema quality is the strongest part of the backend:
- `CHECK` constraints on threshold range, both weight-group sums, status enums, CEFR
  values, and **paired** constraints (`(required_language IS NULL) = (min_language_cefr IS
  NULL)`, `(status='completed') = (outcome IS NOT NULL)`).
- `UNIQUE` where it protects a real invariant: `(job_id, version)`,
  `applications.match_result_id`, `interview_webhook_events.delivery_id`,
  `candidate_feedback.application_id`.
- **Indexes exist** on every FK actually filtered in a query (`match_results.job_id`,
  `applications.job_id`, `interview_rounds.application_id`,
  `recruiter_decisions.application_id`, `interview_webhook_events.application_id`), plus
  the composite `(job_id, version)` covering `job_descriptions` lookups. I checked this
  specifically because unindexed FKs are the common failure — they are not present here.
- `InterviewWebhookEvent.application_id` is deliberately **not** a FK so unresolvable
  references can still be logged. That is the right call and the docstring explains why.

### WEAKNESS — `MatchResult` immutability is application-level only

`recruitment_role` owns the table and therefore holds `UPDATE`/`DELETE`. The handoff states
this honestly. Since immutability is a headline architectural claim, it is worth making it
true at the database level: `REVOKE UPDATE, DELETE ON recruitment.match_results FROM
recruitment_role`, or a `BEFORE UPDATE OR DELETE` trigger that raises. **Effort: S.
Risk: LOW** (verified: no code path updates or deletes that table).

### L6 — Mixed transaction discipline in `db.py` · LOW · WEAKNESS

Some helpers commit (`create_application`, `create_interview_round`,
`record_invite_attempt`), some deliberately only `flush()` (`create_job_with_description`,
`create_match_result`, `update_job_description_extraction_metadata`). Each is documented,
but a caller cannot tell which is which without reading the docstring — and H2(b) is
exactly the bug this ambiguity produces. A naming convention (`add_*` vs `create_*`) would
make the contract visible at the call site.

### L5 — Dead code

`get_application_by_id` (`db.py:965`) has **zero callers**.
`interview_webhook_events.processed` is written but **never read**.

---

## 9. Infrastructure & operations

### M6 — No automated migration step · MEDIUM · WEAKNESS

Verified across all three Dockerfiles and `docker-compose.yml`: **nothing runs
`alembic upgrade head`**. Every Dockerfile's `CMD` goes straight to `uvicorn`. Postgres
init scripts create schemas/roles/extensions, but all tables come from Alembic.

So a fresh `docker compose up` on a clean volume produces three app containers that report
healthy (`/health` doesn't touch the DB) while every real request 500s, until someone
manually runs three separate `docker exec … alembic upgrade head` commands that are not
encoded anywhere in compose. There is also no startup schema-version assertion, so a new
image silently running against an old schema is possible.

**Fix:** run `alembic upgrade head && uvicorn …` in each service's entrypoint (single
replica per service, so no migration race), or add a one-shot `migrate` service that the
apps `depends_on`. **Effort: S. Risk: MEDIUM** — needs care not to run concurrently.

### M7 — Unpinned dependencies; `pytest` in the runtime image · MEDIUM · WEAKNESS

`services/recruitment-workflow/requirements.txt` pins `alembic`, `sqlalchemy`, `psycopg`,
`google-genai`, `bcrypt`, `PyJWT` — but leaves **`fastapi`, `uvicorn`, `pydantic`,
`pydantic-settings`, `python-multipart`, `httpx`, and `pytest` unpinned**. Two builds of
the same commit can therefore install different FastAPI/Pydantic majors. For a project
whose demo depends on a reproducible `docker compose build`, that is a real risk.

`pytest` also ships into the production image — inconsistent with VoiceHR, which
deliberately excludes it (and which `handoff.md` cites as good practice).

**Fix:** pin everything (or add a lock file). **Effort: S. Risk: LOW.**

### Verified good

- Healthchecks on all five containers with sensible `start_period`s.
- `restart: unless-stopped` everywhere.
- VoiceHR's SQLite correctly lives inside the `voicehr_data` named volume — a previously
  fixed, real bug.
- Build context and multi-stage frontend build are correct: no Node, no `node_modules`,
  no frontend source in the runtime image.

### L7/L8 — Hardening

- `resume-intelligence` (8001) and `second-brain` (8002) publish host ports they do not
  need; nothing outside the compose network should reach them. Removing those two
  mappings costs nothing.
- All containers run as **root**; no `USER` directive anywhere.
- No log rotation or resource limits — unbounded JSON logs on a long-lived demo host.

### Observability — adequate, honest

Structured JSON logging with a correlation ID propagated across service boundaries, and
`log_external_call` on every outbound call. Nothing is shipped anywhere, which is the
correct scope. No metrics/tracing, correctly deferred.

---

## 10. Tests

### Verified strengths

- **`tests/integration/db_safety.py` is excellent.** Fail-closed, no silent fallback,
  requires `TEST_DATABASE_URL` to name an unmistakably disposable database, denylists the
  real one, and documents the real incident that motivated it. This is exactly how to fix
  a destructive-fixture class of bug.
- The repo-root integration suite tests things unit tests structurally cannot: real DB
  `CHECK` constraints, real 409-after-terminal-transition, real partial-write rollback.
- `jd_validation` / `match_anomaly` / `scoring` / `language_gate` are pure and
  **directly** unit-tested, no mocking — the payoff of keeping them pure.

### M8 — No test runs two real services together · MEDIUM · WEAKNESS

Verified in `conftest_recruitment_workflow.py`: `clients.retrieve_candidates`,
`clients.get_candidate`, `resume_intelligence_client.get_contact`, and
`voicehr_client.invite_candidate` are all `monkeypatch`ed at the module boundary
(`:440-441, :494, :551`). The real `shared/http_client` layer is never exercised, and
**Second Brain has no test suite at all**.

Concretely: if Second Brain renamed `similarity_score`, **every test in this repository
would still pass** and the break would only appear live.

**Fix:** one contract test per boundary — spin the real service with `httpx.ASGITransport`
and assert the real client parses the real response. **Effort: M. Risk: LOW.**

### M9 — The frontend is effectively untested · MEDIUM · WEAKNESS

29 `.jsx` files; **0** covered. The only tests are two pure-constants modules via
`node --test`. Every page, the API client, `usePolling`, and both graph components rely
entirely on manual Playwright passes.

This is a defensible historical choice (no JS test framework was ever introduced), but
`client.js`'s 401-redirect logic and `usePolling`'s visibility handling are pure enough to
test with zero new dependencies, using the same `node --test` runner already in use.
**Effort: M. Risk: LOW.**

### Mocking that hides a real defect — confirmed, one instance

`test_create_interview_round_never_persists_candidate_contact` (see **H1**) asserts a
property it does not actually check: success path only, and it excludes the one column
that can hold the PII. Meanwhile the *failure*-path test that does exist happens to pick
the one failure mode whose message contains no address. The combination produces
confident, incorrect coverage.

**The counts are honest** (201 RWS unit / 141 integration / 205 VoiceHR were not
re-run here, but the suites and fixtures match what `handoff.md` describes). The gap is
in *shape*, not quantity.

---

## 11. Performance

| Finding | Where | Impact |
|---|---|---|
| **N+1 cross-service calls** — one `get_candidate` HTTP round trip per retrieved candidate, sequential, inside the write transaction | `main.py:536-548` | M2. Up to 100 serial calls |
| **New `httpx.AsyncClient` per call** — no connection reuse | `shared/http_client.py:78` | Cheap to fix: one module-level client |
| **N sequential Gemini calls on first matches read** | `main.py:734-737` | M3. Quota + timeout risk |
| **Up to 6 evaluation calls per interview** | `evaluator.py:162,392` | M4 |
| **N+1 query for interview rounds** on both application list endpoints | `main.py:829` via `_application_response` | Honestly documented; fine at demo scale |
| **`len(list_match_results_for_job(...))` to count** — loads every row to compute a count | `main.py:698` | Trivial; `COUNT(*)` instead |
| **Sync SQLAlchemy inside `async def`** — blocks the single worker's event loop | `main.py` (`submit_job`, all async routes) | SCALABILITY |
| **847 KB main JS bundle** | `dist/assets/index-*.js` | L12 |

None of these is a defect today. They are listed because together they describe the
system's actual scaling ceiling: roughly one recruiter, one job submission at a time.

---

## 12. Maintainability

- **Documentation quality is genuinely exceptional.** Docstrings explain *why*, name the
  incident that motivated a decision, and pre-empt the obvious objection. This is the
  project's biggest maintainability asset and should be preserved verbatim.
- **But three documented claims are inaccurate**, which is worth more than it sounds
  because the docs are otherwise trusted:
  1. `CLAUDE.md` — "PII lives **only** in Resume Intelligence" (VoiceHR stores name+email
     permanently — M10).
  2. `test_…never_persists_candidate_contact` — the test name asserts more than the test
     checks (H1).
  3. `candidate_summary.py:72-78` — "no name, email, phone, address, or links **can** reach
     this string". The allow-list is field-level, not content-level: a free-text
     `work[].description` extracted from the résumé could contain any of them. The design
     is still good; the guarantee is narrower than stated.
- **Stale references (L9):** `GeminiProvider`'s docstring says "Default model:
  gemini-2.5-flash" while `DEFAULT_MODEL = "gemini-3.6-flash"`; VoiceHR's `config.py`
  defaults (`gemini-2.5-flash`, `gemini-3.1-flash-lite-preview`) disagree with the `.env`
  actually in use.
- **`handoff.md` is 204 KB / 2,700+ lines.** It has become an append-only session journal
  where current state, history, and rationale are interleaved. Its accuracy is high, but
  finding the current truth requires reading far too much. Splitting it into
  `ARCHITECTURE.md` (stable), `STATUS.md` (current), and `CHANGELOG.md` (history) would
  preserve every word while making the current state findable.
- **`datetime.utcnow()`** (deprecated, naive) throughout VoiceHR, stored as ISO strings and
  compared against RWS's timezone-aware values.

---

# Executive assessment

**Technical maturity: high for its class — well above a typical final-year project, and
close to production-grade in the areas that were deliberately targeted.**

The architecture's central claim — *the LLM never decides anything* — is not marketing.
It is enforced structurally: pure scoring modules with no I/O, verdicts computed in Python
from model-provided evidence, database-level role isolation preventing cross-schema reads,
and `extra="forbid"` schemas at every boundary. I tried to break this claim and could not.

**The biggest strengths:**
1. **Deterministic decision-making with a real audit trail.** `MatchResult` captures
   enough to reproduce and defend any qualification outcome.
2. **Database schema design.** Constraints encode real invariants — paired nullability,
   status/outcome pairing, weight sums — rather than trusting application code.
3. **PII architecture, where it was actually designed.** `candidate_summary.py`'s positive
   allow-list is the right pattern, implemented properly.
4. **Documented reasoning.** Decisions record the incident behind them, which is why this
   audit could verify intent rather than guess at it.
5. **Honest failure handling.** `insufficient_evidence` over a fabricated CEFR level;
   `needs_review` over a guess; "observational, never blocking" applied consistently.

**The biggest weaknesses:**
1. **The cross-service completion path is the weak seam** (H2, H3). Everything inside a
   service is carefully transactional; the handoffs *between* services are single-attempt
   and one-directional, with no correction path — and their failure mode is silent and
   asymmetric (acceptance blocked, rejection unaffected).
2. **The PII boundary has one real leak and one undocumented exception** (H1, M10) — in a
   project where that boundary is a headline claim.
3. **Test shape, not test count.** No test exercises two real services together; the one
   test guarding the PII invariant does not actually guard it.
4. **Operational bring-up is undocumented** (M6, M7) — the gap between "runs on this
   machine" and "runs reproducibly".

The recurring pattern: **within a boundary the engineering is excellent; across a boundary
it thins out.**

---

# Top 10 improvements
*Ordered by impact, not ease.*

1. **Stop persisting candidate email in `notification_error`** (H1) — closes a live
   violation of a non-negotiable invariant, and fix the test that claims to cover it.
2. **Make the webhook receipt and the state change one transaction** (H2b) — removes the
   window where a received result is permanently discarded.
3. **Add a result re-delivery path and let RWS accept a corrective assessment** (H2, H3) —
   the single fix that makes the VoiceHR→RWS seam recoverable.
4. **Re-notify RWS from `retry_report`** (H3) — stops the two systems silently diverging
   on decision-relevant data.
5. **Fix the no-email 500** (M1) — a supported data state currently breaks three flows
   with a misleading error.
6. **Cap AI spend on the two amplifying paths** (M3, M4) — directly mitigates the quota
   exhaustion that has repeatedly blocked live verification.
7. **Automate migrations in the container entrypoint** (M6) and **pin all dependencies**
   (M7) — turns "works here" into "builds and boots reproducibly".
8. **Add one contract test per service boundary** (M8) — the highest-value missing test,
   given every cross-service call is currently mocked.
9. **Normalize skill matching deterministically** (M13) — the highest-leverage improvement
   to actual matching quality; reduces the need for the detection layers already built.
10. **Set embedding `task_type`** (M11) — improves the semantic half of every score, and
    gets more expensive to fix the longer embeddings accumulate.

---

# Immediate fixes
*Small, low-risk, worth doing before the next demo.*

| Fix | Files | Effort | Risk |
|---|---|---|---|
| Sanitize `notification_error` (no recipient address, no provider body) | `interview_round_mailer.py` | S | LOW |
| Extend the PII test to the failure path + `notification_error` | `test_interview_rounds_integration.py` | S | LOW |
| Single transaction for webhook receipt + state change | `db.py`, `main.py` | S | LOW |
| `retry_report` → notify RWS | `processing.py` | S | LOW |
| `ContactLookupResponse.email` → `Optional`; explicit 422 in invite pre-flight | `api.py`, `main.py` | S | LOW |
| Use the signed `payload.delivery_id` for dedup | `main.py` | S | LOW |
| Pin `fastapi`/`uvicorn`/`pydantic`/`httpx`; drop `pytest` from the runtime image | `requirements.txt` | S | LOW |
| `temperature=0` for structured extraction | `shared/llm.py` | S | LOW |
| Unify the evaluator's nested retry budget | `evaluator.py` | S | LOW |
| `REVOKE UPDATE, DELETE ON match_results` | new migration | S | LOW |
| Remove host port mappings for the two internal services | `docker-compose.yml` | S | LOW |
| Correct the three inaccurate doc claims | `CLAUDE.md`, `candidate_summary.py`, `llm.py` | S | LOW |
| Delete `get_application_by_id` | `db.py` | S | LOW |

---

# Medium-term improvements

1. **Result re-delivery + corrective assessment** (H2/H3 full fix) — a service-key-gated
   `resend-result` on VoiceHR plus an RWS path that accepts a late assessment. **M / MEDIUM.**
2. **Restructure `submit_job`**: fetch and score outside the transaction, write inside it.
   Preserves milestone 40's atomicity while decoupling transaction lifetime from network
   latency. **M / MEDIUM.**
3. **Contract tests per boundary** using `httpx.ASGITransport`. **M / LOW.**
4. **Deterministic skill normalization**, versioned so historical results stay
   explainable. **S code / MEDIUM risk.**
5. **Split `main.py` into routers** and **`ApplicationsPage.jsx` into components**.
   **M / LOW.**
6. **Frontend tests for the pure logic** (`client.js` 401 handling, `usePolling`) with the
   existing `node --test` runner — no new dependency. **M / LOW.**
7. **Migration automation + a startup schema-version assertion.** **S–M / MEDIUM.**
8. **Report-token expiry** and a documented PII retention position for VoiceHR. **S / LOW.**
9. **Split `handoff.md`** into architecture / status / changelog. **M / LOW.**
10. **Non-root containers; log rotation.** **S / LOW.**

---

# Deferred / avoid
*Correct calls already made — do not reverse them.*

- **Message broker, Temporal, Celery, Redis, Kubernetes.** The one genuine async boundary
  is a signed idempotent webhook. Adding a broker would add failure modes, not remove them.
- **A reconciliation poller for lost webhooks.** The targeted fix (re-delivery endpoint +
  corrective accept) solves H2 without a scheduler. Do not build the poller.
- **A full skill taxonomy / ontology / learned ranker.** M13's deterministic normalization
  captures most of the benefit at a fraction of the cost and stays explainable — which
  matters more here than marginal accuracy.
- **Migrating VoiceHR off SQLite.** Its persistence is adequate and isolated; a migration
  would be pure risk.
- **A shared mailer abstraction** across RWS and VoiceHR. Two callers, two concerns; the
  duplication is correct.
- **An interviewer directory / user management system.** Free-text interviewer fields are
  the right scope for one seeded recruiter account.
- **httpOnly-cookie auth rework.** `sessionStorage` is defensible given there are no XSS
  sinks; the change would add CSRF surface for little gain.
- **Pagination, caching layers, read replicas.** No dataset in sight justifies them.
- **CI/CD pipelines, secrets manager, multi-environment config.** Out of scope for a
  locally-demoed project; say so rather than half-building it.

---

# Proposed roadmap

### Phase 1 — Integrity & privacy (highest value, ~1 focused session)
Close the live PII leak (H1) and the lost-result window (H2b), fix the no-email 500 (M1),
fix the test that guards H1, and correct the inaccurate doc claims. Sign off by
re-running the RWS unit + integration suites and re-querying `notification_error` after a
deliberately failed send to confirm no address is stored.

### Phase 2 — Cross-service recoverability (~1 session)
Add VoiceHR's `resend-result` endpoint and RWS's corrective-assessment path; wire
`retry_report` into it (H3). Add the first contract test per boundary (M8) so the new path
is covered by something other than a mock. Surface "interviewed but no result" in the UI.

### Phase 3 — Cost, reproducibility, ops (~1 session)
Cap the Gemini amplification paths (M3, M4), pin dependencies and drop `pytest` from the
runtime image (M7), automate migrations (M6), harden the containers (L7/L8). Deliverable:
a clean-volume `docker compose up` that reaches a working system with no manual steps.

### Phase 4 — Matching quality (~1 session, needs care)
Deterministic skill normalization (M13) behind a recorded version, plus embedding
`task_type` with a one-off re-embed (M11). Both change scores, so both need golden tests
and an explicit note that historical `MatchResult`s used the prior version.

### Phase 5 — Maintainability (opportunistic)
Split `main.py` into routers, split `ApplicationsPage.jsx`, split `handoff.md`, add
frontend logic tests, lazy-load the graph route. No behaviour change; do it when touching
those files anyway.

**Sequencing rationale:** Phases 1–2 fix things that are wrong or unrecoverable. Phase 3
protects the demo itself. Phase 4 improves the product but perturbs scores, so it comes
after the safety net. Phase 5 is pure cleanup and should never block the others.

---

# Architecture verdict
*What is already well-designed and should not be changed.*

1. **The deterministic scoring core.** `scoring.py` — pure, dependency-free, the single
   place qualification is decided. The hard gate evaluated independently of `final_score`
   is exactly right. **Do not move logic into it, and do not let an LLM near it.**
2. **The three-schema / three-role Postgres isolation.** Enforced by grants, not
   convention. **Keep.**
3. **`MatchResult` immutability and its snapshot set.** The reproducibility story is the
   project's best answer to "how do you defend this decision?". Strengthen it at the DB
   level; do not dilute it.
4. **The parallel-signal pattern** for the language gate and interview rounds, rather than
   growing `Application.status`. Applied twice, consistently. **Keep, and reuse it for any
   future signal.**
5. **The PII allow-list in `candidate_summary.py`.** Positive allow-list, never a blocklist.
   **Keep** — just correct the docstring's overclaim.
6. **The LLM provider abstraction** (`shared/llm.py`) — thin, one implementation, no
   speculative plugin system.
7. **VoiceHR's evaluator contract** — evidence from the model, verdict from code,
   `insufficient_evidence` instead of a fabricated level, pronunciation nulled at read
   time. Genuinely principled.
8. **The candidate-only interview architecture** — no recruiter can join a live session,
   by construction rather than policy, and the API key never reaches the browser.
9. **`db_safety.py`'s fail-closed guard.** The correct response to a destructive incident:
   make it structurally impossible, in one shared place.
10. **The "observational, never blocking" pattern** (`jd_validation`, `match_anomaly`,
    round notifications). A consistent, teachable rule for advisory signals.
11. **Restraint about infrastructure.** No broker, no Kubernetes, no premature
    abstraction. The project is defensible precisely because it can justify every piece it
    has — and this audit's recommendations are deliberately scoped to preserve that.
