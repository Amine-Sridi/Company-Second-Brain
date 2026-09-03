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
