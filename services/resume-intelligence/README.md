# Resume Intelligence (service)

Owns candidate PII. Extends the existing Resume Intelligence extraction pipeline — does not
rewrite it. `candidate_id` is minted here.

**Status:** skeleton only. Not yet implemented. Requires the actual existing ResumeParser
source code before extraction logic can be integrated (see `handoff.md` → Known Limitations).

Planned surface (see `handoff.md` for the authoritative next-steps order):
- `candidates` table in the `resume_intel` Postgres schema, normalized-email dedup
- `GET /v1/candidates/{candidate_id}/contact` (service-key auth, response not persisted by caller)
