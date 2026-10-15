# Recruitment Workflow Service (RWS)

The coordination layer of the platform and the only service the recruiter's browser talks to. It
turns a job description into structured criteria, matches candidates, tracks each application
through its lifecycle, and records the recruiter's decision. It also serves the built React
administration interface, so one container is both the API and the UI.

It owns the `recruitment` Postgres schema: jobs, versioned job descriptions, versioned scoring
configurations, immutable `MatchResult`s, applications, recruiter decisions and human interview
rounds. It stores **no candidate identity**: it works with an opaque `candidate_id` and asks
Resume Intelligence for contact details only at the moment it needs them.

## What it does

1. **Structures a job description.** Gemini turns the pasted text into required skills, preferred
   skills, minimum experience and target titles. `jd_validation.py` then normalizes and checks the
   result (for example it caps mandatory skills at six), may ask for one repair attempt, and stores a
   warning instead of blocking when the extraction still looks unreliable.
2. **Retrieves and scores candidates.** Second Brain returns the most similar candidates; `scoring.py`
   computes the hybrid score and the qualification decision with pure functions (no database, no
   HTTP, no LLM). The whole submission is atomic: if anything fails, nothing is stored.
3. **Stores an immutable `MatchResult`** for every scored candidate, qualified or not, with the
   scoring-config version, embedding model version, threshold and a snapshot of the candidate data,
   so the decision can be explained and reproduced later. An advisory explanation is generated only
   after the score is saved and is stored separately.
4. **Runs the application lifecycle:** `selected` → `invited` → `interviewed` → `accepted` /
   `rejected`. Inviting calls VoiceHR; the interview result comes back through a signed webhook.
   `language_gate.py` compares the assessed CEFR level with the job's requirement and can block an
   acceptance, never a rejection. Human interview rounds (schedule, reschedule, complete, cancel)
   are advisory and do not change the application state.
5. **Notifies** candidates and interviewers about interview rounds, with a calendar (`.ics`)
   attachment.

How the score works is described in the [main README](../../README.md#how-a-match-is-scored).

## Modules

| File | Role |
|---|---|
| `main.py` | FastAPI app: routes, the job-submission pipeline, the webhook, the static frontend |
| `scoring.py` | Pure scoring and qualification functions |
| `jd_validation.py` | Validation, normalization and repair policy for structured job descriptions |
| `match_anomaly.py` | Flags a suspicious match outcome without changing any `MatchResult` |
| `language_gate.py` | Deterministic comparison of the assessed language level with the job requirement |
| `explanation.py` | Advisory explanation, generated after the score is persisted |
| `db.py`, `schemas.py` | SQLAlchemy models and Pydantic schemas |
| `auth.py` | Recruiter JWT and service-key checks |
| `clients.py`, `resume_intelligence_client.py`, `voicehr_client.py` | HTTP clients for the other services |
| `interview_round_mailer.py` | Interview-round notification emails |
| `alembic/` | Migrations for the `recruitment` schema |
| `frontend/` | The recruiter interface (React, Vite, three.js) |

## API

Interactive documentation is served at `/docs`. The main routes:

| Route | Purpose |
|---|---|
| `POST /v1/auth/login` | Recruiter login, returns a JWT |
| `POST /v1/resumes` | Upload a resume (forwarded to Resume Intelligence) |
| `GET /v1/candidates`, `GET /v1/candidates/{id}/knowledge` | Candidate list and professional knowledge |
| `GET /v1/candidates/{id}/identity` | Name and contact, fetched live from Resume Intelligence |
| `POST /v1/jobs`, `GET /v1/jobs` | Submit a job (structuring, retrieval, scoring) and list jobs |
| `GET /v1/jobs/{id}/matches` | Ranked matches with score components and explanation |
| `PATCH /v1/jobs/{id}/language-requirement` | Set or clear the language requirement |
| `POST /v1/matches/{match_id}/select` | Select a qualified candidate (creates the application) |
| `POST /v1/applications/{id}/invite`, `.../resend-invite` | Send or resend the VoiceHR invitation |
| `POST /v1/applications/{id}/accept`, `.../reject` | Recruiter decision |
| `POST /v1/applications/{id}/interview-rounds` | Schedule a human interview round (plus reschedule, complete, cancel) |
| `GET /v1/interviews/{id}/report`, `.../recording` | VoiceHR report and recording, proxied |
| `GET /v1/graph/candidates-jobs` | Data for the 2D and 3D knowledge graph |
| `POST /v1/webhooks/voicehr` | Signed VoiceHR result (HMAC-SHA256, deduplicated by delivery id) |

Recruiter routes require a JWT. Service-to-service calls use an `X-Api-Key` header. The webhook
verifies an HMAC signature instead of either.

## Configuration

Copy `.env.example` to `.env`. The variable names are in that file: database URL, the API keys for
the other services and their URLs, `WEBHOOK_HMAC_SECRET`, `JWT_SECRET`, the seeded recruiter
(`SEED_RECRUITER_EMAIL`, `SEED_RECRUITER_PASSWORD`), `GEMINI_API_KEY` and the Resend settings.
Never commit a real `.env`.

## Tests

```bash
cd services/recruitment-workflow
python -m pytest tests -q                  # 236 tests

cd frontend
npm install
npm test                                   # component and graph-model tests (vitest)
npm run dev                                # development server, proxies the API
```

See [docs/TESTING.md](../../docs/TESTING.md) for every suite and what each can and cannot observe.
