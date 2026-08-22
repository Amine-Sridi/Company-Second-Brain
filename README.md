# Company Second Brain — Recruitment Platform

Internship project: distributed AI-assisted recruitment platform integrating Resume
Intelligence, Second Brain, Recruitment Workflow Service, and VoiceHR.

**Read `handoff.md` before doing anything else.** It is the living source of truth for
architecture decisions, implementation status, and next steps. This README will not be
kept in sync with progress — `handoff.md` is.

## Layout

```
services/
  resume-intelligence/     Candidate PII, resume extraction (existing pipeline to be integrated)
  second-brain/            PII-free candidate/company knowledge + pgvector retrieval
  recruitment-workflow/    Matching engine, workflow state, recruiter decisions
  voicehr/                 Integration surface only — VoiceHR's own engine/DB is untouched
shared/
  schemas/                 Shared Pydantic contracts (extra="forbid")
  http_client.py           Shared service-to-service HTTP client
infra/                     Docker Compose, Postgres init, Alembic config
tests/e2e/                 End-to-end happy-path test against the full stack
```
