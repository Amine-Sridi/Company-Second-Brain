# Architecture decisions

The rules this platform was built under, and why. They are deliberately few and strict:
several of them are enforced by code structure or by the database rather than by convention,
and the source code cites this file wherever it relies on one.

The overriding goal is a **production-oriented design, not production infrastructure**. Every
piece of infrastructure has to answer one question: *what concrete problem does this solve in
this project?* "It is more scalable" and "it is industry standard" are not answers on their own.

## 1. Decisions that do not change

| # | Decision | Why |
|---|---|---|
| 1 | One PostgreSQL instance with `pgvector`, **three schemas** (`resume_intel`, `second_brain`, `recruitment`), three least-privilege roles, **no cross-schema grants**. Services reach each other's data only through HTTP APIs. | Data ownership is enforced by the database, not by good intentions. |
| 2 | VoiceHR keeps its own **SQLite** store. Its interview engine is not redesigned; only its API surface is extended (external reference id, invitation endpoint, signed outbound webhook). | It is a self-contained interview system; migrating it would add risk and no capability. |
| 3 | **Docker Compose** for local and demo orchestration. No Kubernetes. | One instance per service is enough for the measured load. |
| 4 | **FastAPI** for every service, **Alembic** per schema for migrations. | One stack to read and defend. |
| 5 | Service-to-service auth with **static API keys** (`X-Api-Key`). Recruiter auth on the Recruitment Workflow Service only, with a **JWT** and one seeded account. Webhooks are signed with **HMAC-SHA256**. | Enough to keep service keys out of the browser; a full IAM would be unjustified here. |
| 6 | **Deterministic + semantic hybrid scoring.** The LLM never determines `final_score`, `qualified`, threshold comparisons, workflow state or recruiter decisions. Its output (the explanation) is generated only *after* the score is computed and persisted, and is stored separately as advisory text. | A recruiter cannot rely on a score nobody can explain or reproduce. Enforced by code structure (`scoring.py` is pure functions with no LLM access), not by a prompt. |
| 7 | **`MatchResult` is immutable** and carries enough to reproduce and explain the decision: candidate snapshot, job-description version, scoring-config version, embedding model version, threshold, both scores, component scores, matched and missing requirements, and the qualification result. A later enrichment of a candidate never changes an existing result. | Historical decisions must stay interpretable under the rules that produced them. |
| 8 | **Gemini is the default AI provider** (extraction, JD structuring, explanations, embeddings, VoiceHR evaluation) behind a thin `LLMProvider` interface. | Switching provider is a configuration change, not a rewrite. |
| 9 | **No Kafka, RabbitMQ, Temporal, Celery, Kubernetes or ELK.** The one genuine asynchronous boundary (VoiceHR to the workflow service) is a signed, idempotent webhook. | No measured problem needs them. |

### Amendment: Redis and ARQ, for resume ingestion only

Redis and ARQ were originally excluded. They were allowed once the stated target became
100,000 resumes a day (about 3.5 a second over a working day): parsing a resume is a ~10 second
model call, and awaiting it inside the upload request held one HTTP worker per resume, a ceiling
reached in the low hundreds per day. The scope is deliberately narrow:

- Redis is a **queue broker and cache only**. It is never a source of truth and **never stores
  personal data**: jobs carry an opaque id, and the file stays on a volume that only Resume
  Intelligence and its worker mount.
- Durable job state lives in Postgres (`resume_intel.ingest_jobs`), so a flushed Redis loses
  throughput, never a candidate record.
- Everything else (matching, webhooks, notifications) still uses `BackgroundTasks` or an
  in-process scheduler, not the queue.
- Prometheus and Grafana stay deferred until there is a throughput number worth tuning.

## 2. Privacy boundaries

These are strict, not guidelines.

- Candidate personal data (name, email, phone, address, raw resume) lives **only in Resume
  Intelligence**, with one deliberate, scoped exception: **VoiceHR stores `candidate_name` and
  `candidate_email`** on its own `interviews` rows, because it owns sending the candidate their
  interview link and greeting them by name. The exception is limited to those two fields in
  that one table and is not a precedent.
- Every other service uses the opaque `candidate_id`. The Recruitment Workflow Service and
  Second Brain persist no identity fields, and nothing may start to, including indirectly in an
  error message, a log line or a status column.
- Second Brain must never become a shadow candidate database: no PII-shaped fields in its
  schemas, not even "temporarily".
- Shared Pydantic schemas in `shared/schemas` use `extra="forbid"`, so a stray `email` is
  rejected at the API boundary instead of being silently passed through.

## 3. Deliberately deferred

Not built, on purpose, and not to be added speculatively: GDPR erasure automation, full
IAM/OAuth/SSO, reconciliation polling for lost webhooks, a skill taxonomy and synonym
normalization, a learned ranking model, horizontal scaling, a secrets manager, and separate
Postgres instances per service. [SCALING_AND_FUTURE_IMPROVEMENTS.md](SCALING_AND_FUTURE_IMPROVEMENTS.md)
describes each one as a *trigger*: the observable condition that would make it worth its cost.

## 4. Code style

- Boring over clever: small functions, explicit SQL, typed Pydantic models, straightforward
  service boundaries, deterministic algorithms.
- No abstraction layer, generic framework, plugin system, event bus or repository that does not
  already have a concrete caller.
- Duplication across the independent services is acceptable, often preferable, to a premature
  shared abstraction. Something moves to `shared/` only once real duplication has been observed.
- A feature is done when it starts, its happy path works and its important failure paths are
  tested, not when the code merely exists. A few tests that prove a real invariant (a hard gate,
  an auth boundary, a PII exclusion) beat many that re-assert a mock.

## 5. Changing a decision

If a decision turns out to be wrong, the order is: describe the concrete technical problem,
explain why the existing decision cannot reasonably solve it, propose the smallest alternative,
and only then change it, in the same change that updates this file.

## A note on references to earlier documents

Source comments sometimes cite a "handoff" file and its milestone numbers. That was the
project's running status journal. It was removed when the repository was cleaned up for
publication; it is still available in the git history of `handoff.md` and
`FRONTEND_HANDOFF.md`. The design rules it recorded are the ones on this page.
