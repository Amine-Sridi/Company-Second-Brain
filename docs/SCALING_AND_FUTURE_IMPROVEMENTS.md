# Scaling and Future Improvements

## How to read this document

This is **not** a wishlist, and it is deliberately not a list of technologies to adopt.
`CLAUDE.md` sets the rule this document obeys: every piece of infrastructure must answer
*"what concrete problem does this solve in this project"*, and "it's more scalable" is not
an answer. Adding Kafka to a system with 8 candidates would be a defect, not an
improvement.

So each item below is written as a **trigger**: the observable condition that makes the
change worth its cost, what to do when it fires, and what it costs. Until the trigger
fires, the current design is the correct one — and being able to say *why* you have not
added something is worth more at a defence than having added it.

Everything here is grounded in this specific codebase. Where a limit was measured or a
line of code identified, it is named.

---

## 1. Where the system actually is today

Measured, not estimated:

| Dimension | Current | Notes |
|---|---|---|
| Candidates | 8 | `resume_intel.candidates` |
| Match results | 110 | `recruitment.match_results` |
| Knowledge vectors | 9 | `second_brain.candidate_knowledge`, 768-dim |
| Services | 4 + Postgres | one container each, single instance |
| Interviews | SQLite | `services/voicehr/storage/voicehr.db` |
| Vector index | **HNSW, cosine** | `candidate_knowledge_embedding_hnsw` — already correct |
| DB pooling | `pool_pre_ping=True` | no `pool_size` / `max_overflow` tuning |
| Async work | `BackgroundTasks` | in-process, dies with the worker |
| Orchestration | Docker Compose | no K8s, deliberately |

Two things are already right and should **not** be "improved" reflexively:

- **Retrieval is index-backed.** `second-brain/db.py` orders by `embedding <=> :vec` with
  a `LIMIT`, which uses the HNSW index. This is the one query that would otherwise
  dominate at scale, and it is already the correct shape.
- **The architecture is genuinely service-oriented**, with no cross-schema grants. That
  means the split below is a deployment change, not a rewrite — which is the whole payoff
  of the current design.

---

## 2. The scaling ladder

The system does not need to jump to a distributed architecture. It needs to climb, and
each rung has a trigger.

| Stage | Scale | What changes |
|---|---|---|
| **0 — today** | 10s of candidates, 1 recruiter | Nothing. Current design is correct. |
| **1 — pilot** | ~1k candidates, a few recruiters | Fix the N+1, paginate, move resume parsing off the request, tune the pool |
| **2 — production** | ~50k candidates, a team | Real job queue, managed Postgres, horizontal RWS, metrics + tracing, secrets manager |
| **3 — multi-tenant** | Multiple companies | Tenant isolation, per-tenant quotas, VoiceHR off SQLite, RLS or schema-per-tenant |

Most of this document is stage 1 and 2. Stage 3 is sketched because tenancy decisions are
expensive to retrofit and cheap to plan for.

---

## 3. Ingestion — resume parsing

### The limit

`services/resume-intelligence/api.py` awaits `parse_resume_text(...)` **inside the upload
request**. A resume upload therefore holds an HTTP worker for the entire duration of a
Gemini extraction (seconds, occasionally tens of seconds), and the client must hold the
connection open. At one upload at a time this is fine and is the simplest correct thing.

**Trigger:** bulk uploads (a recruiter dropping 50 CVs), or upload latency becoming a
complaint, or workers saturating under concurrent uploads.

### What to add

**A real job queue.** The honest recommendation here is the one `CLAUDE.md` currently
forbids, and it is forbidden *correctly* for now:

- **Recommended: [ARQ](https://arq-docs.helpmanual.io/) or Celery + Redis.** ARQ is the
  better fit — asyncio-native, tiny, and the whole API is `enqueue_job` / a worker
  function, so it does not impose Celery's conceptual weight on a codebase this size.
- Upload becomes: store the file, enqueue, return `202 Accepted` with a job id. The
  frontend polls or subscribes.
- This also fixes a real correctness gap: today, if the container restarts mid-parse, the
  upload is simply lost with no record. A queue makes it retryable.

**Cost:** one new service (Redis), one new failure mode (queue backlog), and a genuinely
more complex local setup. Do not pay it for one upload at a time.

**Cheaper intermediate step** (worth doing first): move parsing to `BackgroundTasks` and
return immediately with a status row in Postgres. Same UX benefit, no new infrastructure,
but still lost on restart — which is exactly the honest trade-off to state.

### Also at this stage

- **Deduplicate resumes by content hash.** Re-uploading the same CV currently re-runs a
  full Gemini extraction. A SHA-256 of the extracted text, checked before calling the LLM,
  is a few lines and removes the single most wasteful repeat cost.
- **File storage.** Resumes currently live on the container filesystem/volume. At stage 2
  move to object storage (S3/MinIO) with presigned URLs — volumes do not survive a move to
  multiple hosts.

---

## 4. Matching — the sharpest bottleneck

### The limit (audit M2, still open)

`main.py`'s `submit_job` does this:

```python
for entry in retrieved:                       # top_k, default 20
    candidate = await clients.get_candidate(  # one HTTP round-trip, sequential
        settings.second_brain_url, ...
    )
```

Three compounding problems, all in one request:

1. **N+1 over HTTP.** 20 retrieved candidates = 20 sequential cross-service calls.
   > **MEASURED, and this estimate was wrong (milestone 64).** The round-trip is
   > **~3.0 ms, not ~50 ms** -- both services are on the same Docker network, so there is
   > no WAN hop to pay for. The N+1 cost about **60 ms at `top_k=20`**, not a second, and
   > ~300 ms at `top_k=100`, not five seconds. Batching it was still worth doing (12.2x on
   > the fetch at n=40, and it shortened the DB transaction, which was always the stronger
   > argument) but calling it "the single highest-value change in this document" rested on
   > a figure ~17x too pessimistic that nobody had measured. `submit_job` is now **flat in
   > `top_k`**: -0.3 ms per extra candidate from `top_k=5` to 40. The dominant cost is the
   > single JD-structuring LLM call (~1.3 s, fixed), so item 3 below (async submission) or
   > caching that call are the only levers that matter; item 4 would optimise 7 ms.
2. **One DB transaction is held open across all of it** — including every remote call.
   Connections are the scarcest resource in a Postgres-backed system, and this design
   holds one for the worst possible duration.
3. **It is all synchronous to the recruiter**, who waits for JD structuring *plus*
   retrieval *plus* N fetches *plus* scoring *plus* persistence.

**Trigger:** `top_k > 25`, or submit latency above a couple of seconds, or the first
"job submission timed out" report.

### What to do, in order of value

1. **Batch the fetch.** Add `POST /v1/knowledge/candidates/batch` to Second Brain taking a
   list of ids. One round-trip instead of N. This is the single highest-value change in
   this document and needs no new technology.
2. **Shorten the transaction.** Do the remote work first, collect results, then open the
   transaction to write. Milestone 40 already moved to a single commit at the end; this
   completes the idea by not holding the connection during I/O.
3. **Then, if still slow:** make submission asynchronous (same queue as §3) and have the
   matches page populate progressively.
4. **Concurrency, carefully.** `asyncio.gather` over the fetches is tempting and would
   help, but it converts a polite sequential load into a burst against Second Brain. Only
   do this with a bounded semaphore, and only after batching proves insufficient.

### Matching quality (audit M13, open)

The deterministic gate is **exact string matching on skills**. Three separate JD-quality
layers exist to detect the symptoms. As the candidate pool grows this gets worse, not
better: "React.js" vs "React", "Postgres" vs "PostgreSQL" silently disqualify people.

- **Stage 1:** a curated synonym/alias table (a few hundred rows). Boring, explainable,
  and defensible in front of a jury — you can point at the row that matched.
- **Stage 2:** embedding-based skill similarity with a threshold, *as a fallback* after
  exact match fails, never replacing it. Keep the exact match as the primary so the
  explanation stays deterministic.
- **Do not** jump to a learned ranking model. You have no labelled outcome data yet, and
  a model you cannot explain is a liability in a hiring system — which is a legal
  exposure, not just an engineering one.

---

## 5. Retrieval, embeddings and the versioning gap

### Already right

HNSW + cosine, `ORDER BY ... LIMIT`. Do not change this until it demonstrably hurts.
Tune `m` / `ef_construction` only with measurements in hand.

### The real gap (audit M11, and it is bigger than the audit says)

`second_brain.candidate_knowledge` has **no `embedding_model_version` column**. That field
exists on RWS's `MatchResult` — where it describes the *score*, not the stored vector.

This matters more than it looks, and is documented at length in
`LOCAL_LLM_BENCHMARK_AND_FALLBACK.md` §8: cosine similarity is only meaningful *within one
embedding space*. Any of these silently corrupt retrieval, returning plausible nonsense
rather than an error:

- Setting `task_type` on embeddings (the M11 "optimization" — it changes the space)
- Upgrading the Gemini embedding model
- Ever using a local embedding model

The locally-available `nomic-embed-text` produces **exactly 768 dimensions**, matching the
column, so even a dimension check passes.

**Do this before any embedding change, not after:**

1. Add `embedding_model_version` (and `task_type`) to `candidate_knowledge`.
2. Refuse to mix generations at query time — filter by the current version.
3. Write a re-embedding job that backfills into the new space, then flips the active
   version atomically.

With 9 vectors this is an afternoon. With 50,000 it is a migration with a rollback plan.
**Do it while it is cheap.** This is the single most important item in this document for
anyone who intends to touch embeddings.

### Retrieval quality at scale

- **Hybrid retrieval.** Pure vector search degrades for rare exact terms (a specific
  certification, an uncommon framework). Add Postgres full-text search (`tsvector`) and
  fuse the two rankings (Reciprocal Rank Fusion is ~15 lines and needs no new dependency).
  **Trigger:** recruiters reporting "it missed an obvious candidate".
- **Chunking.** Long CVs are currently embedded whole, so a 10-year career averages into
  one vector and specifics wash out. Chunk per role/section and aggregate.
  **Trigger:** senior candidates ranking worse than juniors.
- **Reranking.** A cross-encoder over the top ~50 gives a real precision jump.
  **Trigger:** only once you have hybrid + chunking and still need better ordering.
  Runs locally on the hardware already benchmarked.

---

## 6. The LLM layer

### What exists

Gemini primary, with a benchmarked local fallback (`granite4.1:3b` structured,
`llama3.2:3b` text) that fires only on availability failures. This is genuinely good and
unusual for a project this size.

### Real weaknesses to address

**The fallback is only as available as a desktop process.** Proven live during
development: a genuine Gemini `503` correctly triggered the fallback, which then failed
because **Ollama was not running** — it does not auto-start. Fixes, cheapest first:

1. Run Ollama as a service (systemd unit / Windows service).
2. Or containerise it in the Compose file with `restart: unless-stopped` — this
   contradicts the current "Ollama lives on the host" note, and is the right trade once
   the fallback is load-bearing rather than a demo feature.
3. Add a startup probe that logs loudly when `LOCAL_LLM_ENABLED=true` but the runtime is
   unreachable. Silent unavailability is the worst property a fallback can have.

**No response caching.** Identical JD structuring calls re-hit Gemini every time. A cache
keyed on `hash(prompt + model + schema_version)` is trivial and cuts both cost and latency.
**Trigger:** immediately — this is nearly free and quota is a documented recurring pain.

**No token accounting.** There is no record of what a job submission costs. Log
`prompt_tokens`/`eval_tokens` per call and aggregate. Without it, cost optimisation is
guesswork. **Trigger:** before any paid tier.

**Per-tenant quotas.** At stage 3, one tenant can exhaust a shared key. Needs a budget per
tenant enforced before the call.

### Provider strategy

The `LLMProvider` interface means adding a second cloud provider (Anthropic, OpenAI) as a
*quality* fallback is a config change, not a rewrite. Worth doing at stage 2, because the
local 3–4 B models — measured honestly — are good enough for structured extraction and
**not** good enough for interview evaluation (best of seven models: 0.600 against a 0.70
usability floor, including a 7 B model).

---

## 7. VoiceHR — the least scalable component

This is the honest weak point, and it is weak by deliberate decision (`CLAUDE.md` forbids
migrating it), so the goal here is to name the ceiling rather than pretend it is not there.

### SQLite

`storage/voicehr.db` is a single file on a single volume. It is fine for sequential
interviews and will not survive horizontal scaling: SQLite's single-writer model plus a
volume that cannot be shared across hosts.

**Trigger:** more than one VoiceHR instance, or concurrent interviews causing
`database is locked`.

**Migration:** the schema is small (`interviews`, `reports`, `recordings`). Moving to the
existing Postgres as a fourth schema is a contained, mechanical change — and would also
close the anomaly that VoiceHR is a second, separate PII store with its own lifecycle.

### Gemini Live constraints (measured this cycle)

- Sessions die at roughly **10 minutes** with `APIError 1006 keepalive ping timeout`.
- `process_interview` only runs on a **clean close** — so an interview that runs to the
  720 s cap produces **no report at all**. That is a real data-loss path.
- **Fix:** persist the transcript incrementally rather than only at the end, so a dropped
  session still yields an evaluable record. Currently only a `pending_transcript` recovery
  blob exists, bounded by `MAX_INTERVIEW_RECONNECTS=2`.

### Concurrency

`_active_interview_locks` is an **in-process dict**. It prevents two tabs joining the same
interview on *one* instance and provides no protection across instances.
**Fix at stage 2:** a Postgres advisory lock or a row-level claim.

### Cost

Every interview is a live bidirectional audio session — by far the most expensive
per-unit operation in the system. At scale, add: a pre-screen gate so only shortlisted
candidates interview, a hard concurrency cap, and per-tenant budgets.

---

## 8. Data layer

### Keep one Postgres longer than instinct suggests

One instance with three schemas and least-privilege roles is the right call and will
remain right well past stage 2. Managed Postgres (RDS/Cloud SQL) with read replicas gets
you very far.

**Split only when** one service's write load demonstrably starves another — measure first.
The service boundaries already exist, so the split is deployment, not redesign.

### Concrete items

- **Connection pooling.** Only `pool_pre_ping=True` is set; defaults otherwise. At stage 2
  set `pool_size`/`max_overflow` deliberately and put **PgBouncer** in front (transaction
  pooling). Postgres connections are expensive and FastAPI workers multiply them.
- **Pagination.** List endpoints return everything. At 50k candidates a single request
  returns 50k rows. Keyset pagination (`WHERE id > :cursor ORDER BY id LIMIT n`), not
  `OFFSET` — offset degrades linearly.
- **Dangling cross-schema references — a real integrity gap.** Observed live: **4 of 5**
  `selected` applications referenced `candidate_id`s that no longer exist in
  `resume_intel.candidates`. There is deliberately no cross-schema FK (that is the
  architecture working as intended), but nothing compensates for it. Add either a periodic
  reconciliation job or a soft-delete + tombstone contract so a deleted candidate is
  *knowable* rather than merely absent. Today, inviting such an application fails with a
  confusing 502 at contact lookup.
- **Archival.** `match_results` grows as `jobs × candidates` — the fastest-growing table by
  construction. Partition by `created_at` or archive cold rows once it passes a few
  million.
- **`MatchResult` immutability is application-level only.** At stage 2, enforce with a
  database trigger or `REVOKE UPDATE`. It is an auditable record; the guarantee should not
  depend on every future developer remembering.

---

## 9. State that blocks horizontal scaling

Before running two instances of anything, these must move. They are small and easy to
miss, which is exactly why they are listed:

| State | Location | Fix |
|---|---|---|
| `_resend_cooldowns` | `main.py:1789` | Postgres row, or Redis with TTL |
| `_interview_round_resend_cooldowns` | `main.py:1390` | same |
| `_active_interview_locks` | `session.py:33` | Postgres advisory lock |
| `BackgroundTasks` work | RWS accept/reject/webhook | real queue (§3) |
| In-process `_llm_provider` singleton | all services | harmless — per-process is fine |

Each is correct today (single instance, documented as such). Each silently breaks with a
second replica — cooldowns stop cooling, interview locks stop locking. **This table is the
checklist for the day someone sets `replicas: 2`.**

---

## 10. Observability

Structured JSON logging with correlation IDs already exists and is genuinely good.

**What is missing, in order:**

1. **Metrics.** No counters, no histograms. Add `prometheus-fastapi-instrumentator` (one
   line per service) plus custom metrics for: LLM calls by provider/outcome, fallback
   activations, match scores, interview completions. **Trigger:** the first "is it slow?"
   question you cannot answer.
2. **Tracing.** One recruiter action fans out across RWS → Second Brain → Resume
   Intelligence → Gemini. Correlation IDs let you *grep* that; OpenTelemetry lets you
   *see* it, with per-span timing. **Trigger:** debugging a slow request across services
   takes more than a few minutes.
3. **Error tracking.** Sentry or equivalent. Exceptions currently land in container logs
   and are noticed only if someone looks.
4. **Alerting.** At minimum: Gemini quota exhaustion, fallback activation rate, webhook
   delivery failures, interview completion rate dropping. The `history_config` defect that
   silently broke *every* interview would have been caught in hours by an alert on
   completion rate, instead of persisting across several sessions.

That last point is the strongest argument in this section, and it is not hypothetical —
it actually happened in this project.

---

## 11. Security, privacy and compliance

The PII boundary is genuinely well enforced. These are the gaps that matter as it grows:

- **GDPR erasure** is explicitly deferred and will become mandatory with real candidates.
  The hard part is not deleting rows — it is that a candidate's data is spread across
  Resume Intelligence, VoiceHR's SQLite, recordings, transcripts, and *embeddings derived
  from* their text. Design the erasure path before the first real candidate, not after the
  first request. An embedding derived from deleted PII is still derived from PII.
- **Retention.** Nothing expires except (now) report tokens. Recordings and transcripts
  accumulate indefinitely. Add a retention policy with an automated sweep.
- **Secrets** are environment variables in `.env` files. At stage 2 move to a secrets
  manager (Vault, AWS/GCP Secret Manager) with rotation. The webhook HMAC secret and JWT
  secret have never been rotated.
- **Auth.** One seeded recruiter, no registration, no roles. Real use needs multiple
  recruiters, an admin/recruiter distinction, and an audit trail of who decided what.
  `recruiter_decisions` exists — extend it rather than inventing something new.
- **JWT in `sessionStorage`** (audit, LOW) is XSS-exposed. Move to an httpOnly cookie with
  CSRF protection when auth is revisited.
- **VoiceHR's `admin.html` is unauthenticated** at the page level. It exposes PII.
- **Rate limiting.** None anywhere. The public interview endpoints and the login endpoint
  are the obvious first targets — add SlowAPI or handle it at the gateway.

---

## 12. Testing and CI

**667 tests pass**, which is a real strength. Gaps, ordered:

1. **No CI.** Everything runs locally, by hand. A GitHub Actions workflow running all four
   suites on push is the highest-value item here by a wide margin. Every regression this
   project has hit would have been caught at push time.
2. **No cross-service test** (audit M8). No automated test runs two real services against
   each other; `tests/integration` mocks the far side. Compose-based integration tests
   (spin the stack, exercise the real chain) would have caught the cross-schema dangling
   references.
3. **Frontend coverage is thin** (audit M9). 34 tests exist for pure display logic, run via
   Node's built-in runner — good — but **zero component tests** across 29 components. Add
   Vitest + Testing Library for the components that carry real logic.
4. **No load testing.** Nothing establishes where this actually breaks. A Locust or k6
   script hitting job submission would turn every number in this document from an estimate
   into a measurement.
5. **`tests/integration`'s conftest defaults `DATABASE_URL` to the live database.** This
   project has already lost its demo database once to exactly that pattern. Make the
   safe-database guard unconditional.

---

## 13. Frontend

- **`ApplicationsPage.jsx` is 890 lines with ~15 `useState`** (audit M12). Extract data
  fetching into hooks and split the page. Trigger: the next time a bug there takes more
  than an hour to find.
- **No state management.** Prop drilling and local state work at this size; React Query
  (TanStack Query) would remove most of the hand-rolled fetch/loading/error code and give
  caching for free. Trigger: the third component that needs the same server data.
- **847 KB main bundle.** Route-level code splitting, and lazy-load the 3D graph — `three`
  and `3d-force-graph` are the bulk and are used on one screen.
- **No accessibility audit.** For a hiring product this is a genuine risk, not a nicety:
  candidates with disabilities must be able to complete an interview.
- **No error boundary.** One render error blanks the app.

---

## 14. Technologies: what to add, and what to refuse

### Worth adding, with the trigger

| Technology | Solves | Trigger |
|---|---|---|
| **ARQ or Celery + Redis** | Parsing/matching blocking requests; lost work on restart | Bulk uploads, or upload timeouts |
| **PgBouncer** | Connection exhaustion | Multiple app replicas |
| **Prometheus + Grafana** | No visibility into latency/cost/failure rate | First unanswerable "is it slow?" |
| **OpenTelemetry** | Cross-service debugging | Slow-request triage exceeding minutes |
| **Sentry** | Unnoticed exceptions | Real users |
| **Object storage (S3/MinIO)** | Files on a container volume | More than one host |
| **Secrets manager** | Plaintext `.env`, no rotation | Real candidate data |
| **GitHub Actions** | Manual testing | **Now** — cheapest high-value item here |
| **Vitest + Testing Library** | Zero component tests | Now-ish |
| **Redis (cache)** | Repeated identical LLM calls | Now — quota is a known pain |

### Deliberately refuse, and be ready to say why

| Technology | Why not |
|---|---|
| **Kubernetes** | Compose is sufficient to well past stage 2. K8s adds an operational burden with no current problem to solve. Revisit only for genuine multi-node HA. |
| **Kafka / RabbitMQ** | The one real async boundary (VoiceHR → RWS) is a signed idempotent webhook and works. A broker solves a fan-out problem you do not have. |
| **Microservice-per-feature** | Four bounded contexts match four real domains. Splitting further multiplies network calls and transactions for no benefit. |
| **A learned ranking model** | No labelled outcomes, and unexplainable ranking in hiring is a legal exposure. |
| **A separate vector database** | pgvector + HNSW handles millions of rows. A second datastore means a second consistency problem. |
| **GraphQL** | The REST surface is small and stable. |

The refusals are as defensible as the additions — and at a defence, being able to explain
why you *didn't* add something is often the stronger answer.

---

## 15. If you only do five things

1. **Add CI.** GitHub Actions running all four suites. Cheapest, highest value.
2. **Add `embedding_model_version` to `candidate_knowledge` and version the vector
   space** — before anyone touches `task_type` or upgrades the embedding model. Trivial at
   9 vectors, a migration at 50,000.
3. **Batch the Second Brain candidate fetch.** Removes the N+1 and shortens the
   transaction. No new technology.
4. **Make Ollama a managed service and probe it at startup.** A fallback that is silently
   unavailable is worse than no fallback — this already happened.
5. **Alert on interview completion rate.** The `history_config` defect broke every
   interview and went unnoticed for sessions. One alert would have caught it in hours.

---

## 16. Honest caveats

- **Most triggers are still judgement, but ingestion throughput is now measured.**
  See §17. The remaining thresholds ("`top_k > 25`", "~1k candidates") are still reasoned
  estimates.
- **Current volumes are tiny** (8 candidates, 110 match results). Nothing here is urgent.
  The purpose is to make the *order* of work clear when it becomes urgent, and to record
  the cheap-now/expensive-later items — the embedding versioning being the clearest.
- **The VoiceHR recommendations conflict with `CLAUDE.md`'s rule** that its persistence is
  never migrated. That rule is correct for the project as scoped; §7 describes what would
  have to change if that scope ever changes, and should be treated as a conditional, not a
  proposal to act on now.

---

## 17. Measured: ingestion throughput (added after the queue landed)

`benchmarks/load/ingest_load_test.py` enqueues N synthetic CVs and polls
`resume_intel.ingest_jobs` until every one is terminal. It measures how fast jobs
**drain**, not how fast the endpoint returns 202 -- accepting work quickly is the easy
half.

| Run | max_jobs | Result | Per-job (min / median / max) |
|---|---|---|---|
| 4 CVs, conc 2 | 4 | 674.7 CV/h | 10.4s / 11.1s / 16.2s |
| 24 CVs, conc 8 | 4 | **908.8 CV/h** | 8.4s / 48.2s / 92.0s |
| 24 CVs, conc 8 | 12 | 822.7 CV/h | 7.6s / 53.7s / 103.6s |

All runs: 100% succeeded, one worker container, developer laptop.

### The finding that matters, because it contradicts this document's own advice

§3 and §9 imply the way to more CVs/hour is more concurrency. **Measurement says
otherwise.** Raising `max_jobs` from 4 to 12 made throughput slightly *worse*
(908.8 -> 822.7) and per-job median *longer*.

The worker log explains it: in that run **23 of 24 extractions were served by the local
Ollama fallback**, because Gemini was quota-exhausted. Ollama serialises on a single
laptop GPU, so extra concurrency queues at a saturated resource instead of using an idle
one. Classic saturation, and invisible without measuring.

Two consequences worth carrying forward:

1. **~909 CV/h is the local model's ceiling on one GPU, not the system's.** Extrapolating
   it to "21,811/day" is misleading: it describes a degraded mode. The healthy-Gemini
   path is still unmeasured, because quota never allowed a clean run.
2. **It prices the local-first idea from §6.** If extraction ran local-first to avoid
   Gemini cost at 100k/day, one GPU buys roughly 900 CV/h -- so ~4,167 CV/h needs on the
   order of **five GPUs**, continuously. That is a real capital and operational number to
   weigh against the API bill, and it is now grounded rather than asserted.

### What to measure next

- The same run with Gemini quota available, to separate the two regimes. Until then
  `max_jobs` is tuned only for fallback mode and is left at 4.
- Horizontal worker replicas, once the in-memory state in §9 is dealt with.
- ~~`submit_job` end-to-end now that the N+1 is gone, to confirm §4's claim.~~
  **Done (milestone 64)** -- and it refuted §4's estimate rather than confirming it;
  see the measured note in §4 itself. `benchmarks/load/matching_latency_test.py`.

---

## 18. Partitioning `match_results`: investigated, and deliberately not done yet

§8 lists partitioning this table as a Phase B item ("cheap now, expensive later"). On
investigation that framing was **wrong for this table**, and the reason is worth recording
so nobody re-derives it.

### What partitioning would actually cost

Postgres requires the partition key in every unique constraint, so partitioning by
`created_at` forces the primary key from `(id)` to `(id, created_at)`. That breaks every
foreign key referencing `match_results(id)`, because a foreign key must point at a unique
constraint. There are two:

```
recruitment.applications       .match_result_id -> match_results(id)
recruitment.match_explanations .match_result_id -> match_results(id)
```

So the real change is not "partition one table". It is:

1. add a denormalised `match_result_created_at` to **both** referencing tables,
2. backfill it,
3. rewrite both foreign keys as composite,
4. update the ORM models and every insert to carry the extra column,
5. then partition.

Steps 1-4 are **permanent complexity**, paid immediately and forever, and they are the
same amount of work at 140 rows as at 50 million. Only step 5's data movement gets more
expensive with time. That is the opposite of the embedding-versioning case (§5), where the
fix was cheap *and* closed a live correctness hole.

### Why the trigger has not fired

| | Measured |
|---|---|
| Rows today | **140** |
| Total size | **184 kB** (~1.3 kB/row) |
| Hot query (`WHERE job_id = ?`) | already indexed: `match_results_job_id_idx` |

Growth is `jobs x top_k`, **not** CVs/day -- 100,000 CVs/day does not multiply this table
unless jobs are posted at the same rate. Realistic projections:

| Jobs/day | top_k | Rows/year | Size/year |
|---|---|---|---|
| 10 | 20 | 73,000 | ~0.1 GB |
| 50 | 100 | 1.8M | ~2.5 GB |
| 200 | 100 | 7.3M | ~10 GB |

Postgres handles a well-indexed 10M-row table without partitioning, and the one index
that matters already exists. §8's stated trigger -- "a few million rows" -- is years away
even at the busiest projection.

### The plan, ready to execute when it does fire

Trigger: `match_results` passes ~5M rows, **or** a retention policy requires dropping old
matches cheaply (partition drop is O(1); `DELETE` of millions of rows is not).

1. `ALTER TABLE ... ADD COLUMN match_result_created_at timestamptz` on `applications` and
   `match_explanations`; backfill from `match_results`; set NOT NULL.
2. Create `match_results_partitioned` with `PRIMARY KEY (id, created_at)`, `PARTITION BY
   RANGE (created_at)`, monthly partitions.
3. Copy data, swap names in one transaction.
4. Recreate both foreign keys as composite
   `(match_result_id, match_result_created_at) -> (id, created_at)`.
5. Automate partition creation (pg_partman, or a scheduled `CREATE TABLE ... PARTITION OF`).

Until then the honest engineering answer is that this table needs nothing: it is small,
correctly indexed, and growing slowly. Adding the complexity now would be exactly the
"it's more scalable" reasoning `CLAUDE.md` rejects.
