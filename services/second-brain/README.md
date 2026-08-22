# Second Brain (service)

Owns PII-free CandidateKnowledge and CompanyKnowledge, including embeddings, in the
`second_brain` Postgres schema (pgvector, HNSW index). Schema must reject PII-shaped fields.

**Status:** skeleton only. Not yet implemented.

Planned surface:
- `POST /v1/knowledge/candidates` — upsert by `candidate_id`, embeds via gemini-embedding-001
- `POST /v1/retrieve/candidates` — pgvector cosine-distance ranking + metadata filter,
  returns `similarity_score` per candidate
