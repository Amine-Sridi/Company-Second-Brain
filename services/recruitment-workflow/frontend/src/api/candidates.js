import { request } from "./client.js";

/**
 * Resolves a candidate's display identity via RWS's transient proxy to Resume
 * Intelligence -- GET /v1/candidates/{candidate_id}/identity. RWS never persists this;
 * it is fetched fresh per call. Returns only { candidate_id, name, email } -- no phone,
 * no other PII field (see FRONTEND_HANDOFF.md §9). Prefer the useCandidateIdentity
 * hook over calling this directly from a component, so lookups are cached in-memory
 * for the session and a failure never breaks the page.
 *
 * @param {string} candidateId
 * @returns {Promise<{ candidate_id: string, name: string, email: string|null }>}
 */
export function getCandidateIdentity(candidateId) {
  return request(`/v1/candidates/${candidateId}/identity`);
}

/**
 * Every candidate ever ingested, newest first -- RWS's transient proxy to Resume
 * Intelligence's own GET /v1/candidates. Sorted newest-first by the backend itself;
 * the frontend never re-sorts. No pagination/search yet (FRONTEND_HANDOFF.md §21).
 *
 * @returns {Promise<Array<{ candidate_id: string, name: string, email: string|null, created_at: string }>>}
 */
export function getCandidates() {
  return request("/v1/candidates");
}

/**
 * A candidate's professional knowledge -- RWS's transient proxy to Second Brain's
 * own GET /v1/knowledge/candidates/{candidate_id}. RWS never persists this. Returns
 * only { candidate_id, skills, job_titles, years_of_experience } -- no raw_text, no
 * source_version, no embedding (see FRONTEND_HANDOFF.md §9 and
 * schemas.CandidateKnowledgeResponse on the backend).
 *
 * @param {string} candidateId
 * @returns {Promise<{ candidate_id: string, skills: string[], job_titles: string[], years_of_experience: number|null }>}
 */
export function getCandidateKnowledge(candidateId) {
  return request(`/v1/candidates/${candidateId}/knowledge`);
}
