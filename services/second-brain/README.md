# Second Brain

The platform's searchable memory of candidates. It stores the **professional** knowledge extracted
from each resume, with its embedding, and answers one question for the workflow service: *which
candidates are semantically closest to this text?* It never knows who a candidate is.

It owns the `second_brain` Postgres schema (pgvector, HNSW index, cosine similarity).

## Responsibilities

- **Store candidate knowledge** keyed by the opaque `candidate_id`: skills, years of experience,
  job titles and a privacy-safe text summary of the profile, plus a 768-dimension embedding
  (`gemini-embedding-001`). The request schema uses `extra="forbid"`, so a stray name, email or
  phone number is rejected with a `422` instead of being stored.
- **Retrieve candidates** by cosine similarity to a query text (in practice, a job description).
  Retrieval is recall only: it returns `candidate_id` and `similarity_score` and makes no
  qualification decision. The Recruitment Workflow Service applies the scoring and the threshold.
- **Guard the embedding space.** Cosine similarity only means something between vectors from the
  same model, and two models can produce vectors of the same dimension. Each vector is stored with
  its embedding model and task type, and a query only compares against its own space, so a model
  change can never silently produce a meaningless ranking.
- **Record recruiter feedback** on a candidate, stored for later use.

## API

All routes except `/health` require the service key in an `X-Api-Key` header.

| Route | Purpose |
|---|---|
| `POST /v1/knowledge/candidates` | Create or update a candidate's knowledge (embeds the summary) |
| `POST /v1/knowledge/candidates/batch` | Fetch many candidates in one call (avoids one request per candidate) |
| `GET /v1/knowledge/candidates/{candidate_id}` | Fetch one candidate's knowledge |
| `POST /v1/retrieve/candidates` | Top-k candidates by cosine similarity to `query_text` |
| `POST /v1/knowledge/candidates/{candidate_id}/feedback` | Store recruiter feedback |
| `GET /health` | Liveness |

Interactive documentation is served at `/docs`.

## Layout

| Path | Contents |
|---|---|
| `main.py` | FastAPI app and routes |
| `db.py` | Models, the HNSW-backed search query and the embedding-space filter |
| `alembic/` | Migrations for the `second_brain` schema |
| `tests/` | Embedding-space versioning tests |

## Tests

```bash
cd services/second-brain
python -m pytest tests -q                  # 8 tests
```
