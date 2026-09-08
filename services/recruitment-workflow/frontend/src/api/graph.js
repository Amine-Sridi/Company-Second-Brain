import { request } from "./client.js";

/**
 * The candidate/job knowledge graph -- RWS's own native read of its already-persisted
 * MatchResult/Job rows (GET /v1/graph/candidates-jobs), not a proxy to another service.
 * No candidates[] array is included: candidate nodes are derived client-side from the
 * unique candidate_id values on edges, with display names resolved via the existing
 * getCandidates() call (see GraphPage) -- exactly the shape the backend's own docstring
 * (schemas.CandidateJobGraphResponse) expects the frontend to build.
 *
 * @returns {Promise<{
 *   jobs: Array<{ job_id: string, title: string }>,
 *   edges: Array<{
 *     match_id: string, candidate_id: string, job_id: string, final_score: number,
 *     qualified: boolean, hard_gate_passed: boolean,
 *     matched_requirements: string[], missing_requirements: string[]
 *   }>
 * }>}
 */
export function getCandidatesJobsGraph() {
  return request("/v1/graph/candidates-jobs");
}
