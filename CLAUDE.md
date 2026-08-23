# CLAUDE.md

Instructions for any Claude Code session working in this repository. Read this file in full
before touching anything else.

## What this project is

A distributed AI-assisted recruitment platform, built as a final-year Software Engineering
internship project (2 months, defended before an academic/company jury with a 4–6 minute
end-to-end demo). Four bounded contexts: **Resume Intelligence** (candidate PII + resume
extraction), **Second Brain** (PII-free candidate/company knowledge + pgvector retrieval),
**Recruitment Workflow Service / RWS** (matching engine, workflow state, recruiter decisions),
and **VoiceHR** (voice interviews — pre-existing, unchanged internals).

The target end-to-end story: Resume → Resume Intelligence → PII-safe Candidate Knowledge →
Second Brain (pgvector) → Recruiter submits JD → JD structured by Gemini → semantic retrieval
→ deterministic+semantic matching → immutable MatchResult → qualified candidate → recruiter
selection → VoiceHR invitation → voice interview → assessment webhook → recruiter decision →
knowledge feedback → complete auditable workflow.

**Goal is production-oriented design, not production infrastructure or maximum sophistication.**
Every piece of infrastructure must answer "what concrete problem does this solve in this
project" — "it's more scalable" / "it's industry standard" is not a sufficient reason on its
own.

## Where to look first, every session

1. **`handoff.md`** (repo root) — the single living source of truth for implementation status
   and the exact next step. It is kept accurate; this file (CLAUDE.md) is not re-synced with
   progress and should never be treated as a status report.
2. Each `services/*/README.md` — scope and current state of that specific service.
3. This file — the rules that don't change step to step.

## Non-negotiable architectural decisions

Do not change any of the following unless you hit a genuine blocking technical
contradiction — and if you do, follow the process in "If you think a decision is wrong" below.

- One PostgreSQL instance + `pgvector`, three schemas (`resume_intel`, `second_brain`,
  `recruitment`), three least-privilege roles, **no cross-schema grants**. Services reach each
  other's data only through HTTP APIs, never by querying another schema directly.
- VoiceHR keeps its own SQLite persistence, unmigrated. Its interview engine and internal
  architecture are not rewritten or redesigned — only its API surface is extended (see below).
- Docker Compose for local/demo orchestration. No Kubernetes.
- FastAPI for all new services. Alembic per schema for migrations.
- Service-to-service auth: static API keys via environment variables. Recruiter auth (RWS
  only): JWT, one seeded account, no registration system. Webhooks: HMAC-SHA256 signatures.
- Deterministic + semantic hybrid scoring. **The LLM never determines `final_score`,
  `qualified`, threshold comparisons, workflow state, or recruiter decisions** — this is an
  architectural constraint enforced by code structure, not a prompt instruction. LLM output
  (explanation) is generated only *after* the score is computed and persisted, and is stored
  separately as advisory text.
- `MatchResult` is immutable and must contain enough to reproduce/explain the decision:
  `candidate_snapshot`, job description version, scoring config version, embedding model
  version, threshold snapshot, both scores, component scores, matched/missing requirements,
  qualification result. A later candidate enrichment must never change an existing
  `MatchResult`.
- Gemini is the default AI provider (extraction, JD structuring, explanation, embeddings,
  VoiceHR evaluation), behind a thin `LLMProvider` interface so switching providers is a config
  change, not a rewrite.
- No Kafka / RabbitMQ / Temporal / Redis / Celery / Kubernetes / ELK / Prometheus-Grafana. The
  one genuine async boundary (VoiceHR → RWS interview completion) is a signed, idempotent
  webhook — no broker. Background work uses FastAPI `BackgroundTasks` and in-process
  `APScheduler` only.
- Explicitly deferred, do not implement speculatively: GDPR erasure automation, full
  IAM/OAuth/SSO, reconciliation polling for lost webhooks, skill taxonomy/synonym
  normalization, learned ranking model, horizontal scaling, secrets manager, separate Postgres
  instances per service.

## Privacy boundaries (treat as strict, not a guideline)

- Candidate PII (name, email, phone, address, raw resume) lives **only** in Resume
  Intelligence.
- Every other service uses the opaque `candidate_id` — never raw identity fields.
- Second Brain must never become a shadow candidate database. Do not add PII-shaped fields to
  its schemas, even "just for convenience" or "temporarily."
- Shared Pydantic schemas in `/shared/schemas` use `extra="forbid"` where specified, so an
  accidental extra field (e.g. a stray `email`) is rejected at the API boundary, not silently
  passed through.

## How to work, step by step

1. **Inspect before implementing.** Look at the existing repo state, existing
   ResumeParser/VoiceHR code (once present — see Known Limitations below), existing
   dependency files, env config, DB models, and API routes before writing anything. Do not
   recreate functionality that already exists.
2. **One implementation step at a time**, taken from `handoff.md` → "Exact Next Steps", in
   order. Do not implement multiple steps at once, and do not build speculative future
   infrastructure ahead of where the plan currently is.
3. For each step: state the objective → inspect what's relevant → identify files to
   create/modify → implement → verify (see below) → report what actually passed → update
   `handoff.md` → only then move on.
4. **Verification is part of implementation, not optional.** A feature counts as done when it
   starts, its happy path works, and important failure paths are tested — not when the code
   merely exists. If your environment can't actually run something (no Docker daemon, no
   network, no live Postgres), say so plainly in `handoff.md` rather than assuming success.
   Don't claim a command passed without having actually run it.
5. Realistic target: ~30–50 meaningful tests for the whole project. Quality over count.

## Known limitations / things to check before certain steps

- Real ResumeParser and VoiceHR source code is expected to be dropped into
  `services/resume-intelligence/` and `services/voicehr/` respectively. Check whether it has
  actually landed before starting any step that extends either — do not assume it's present
  just because the folder exists.
- Once VoiceHR's real source is present: do not migrate its SQLite, do not rewrite its
  interview engine. Only add what integration requires — `external_reference_id` column, the
  invitation endpoint, and the outbound signed webhook.
- Once ResumeParser's real source is present: extend its extraction pipeline, don't rewrite
  it.
- Check `handoff.md`'s "Known Limitations" section for anything discovered mid-implementation
  in prior sessions (e.g. commands that were written but never actually executed in a working
  Docker/Postgres environment, and still need real verification).

## Code style

- Boring over clever: clear Python, small functions, explicit SQL, typed Pydantic models,
  straightforward service boundaries, deterministic algorithms.
- Avoid unnecessary abstraction layers, generic frameworks, metaprogramming, elaborate
  dependency injection, plugin systems, event buses, or repositories/services that don't
  already have a concrete caller. This code will be read and defended live by a student in
  front of a jury — optimize for that reader.
- Duplication across the three independent services (e.g. a small DB health-check snippet) is
  acceptable and often preferable to a premature shared abstraction. Promote something to
  `/shared` only once real duplication is observed, not in anticipation of it.

## If you think a decision is wrong

Don't silently change it. Instead: (1) explain the concrete technical problem you hit, (2)
explain why the existing decision can't reasonably solve it, (3) propose the smallest
alternative, (4) wait for approval before changing the architecture or `handoff.md`'s
non-negotiable decisions.

## Maintaining `handoff.md`

After every completed step, update, in `handoff.md`:
- **Current Implementation Status** — move the item from NOT STARTED → IN PROGRESS → DONE,
  based only on what you actually verified working, with an honest caveat if verification was
  partial (e.g. syntax-checked but not run against a live service).
- **Exact Next Steps** — remove what's finished, keep the next unfinished step marked
  `[NEXT]` at the top.
- **Known Limitations** — add only real, implementation-discovered limitations. Don't invent
  hypothetical ones.
