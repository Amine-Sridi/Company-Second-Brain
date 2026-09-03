import { request } from "./client.js";

/**
 * Recruiter-facing job list -- GET /v1/jobs. Newest-first, lightweight rows
 * ({ job_id, title, created_at, match_count }); no pagination/filtering on either side.
 * @returns {Promise<Array<{ job_id: string, title: string, created_at: string, match_count: number }>>}
 */
export function getJobs() {
  return request("/v1/jobs");
}

/**
 * Submits a raw job description to RWS's full JD-to-matches pipeline (JD structuring,
 * Second Brain retrieval, deterministic+semantic scoring, persisted MatchResults).
 * `explanation` on the returned matches is always null here -- it is only populated by
 * getJobMatches(), generated lazily server-side on first read.
 *
 * @param {{ title: string, raw_description: string, threshold?: number, top_k?: number }} params
 * @returns {Promise<object>} the full JobResponse (job_id, requirements, matches, ...)
 */
export function createJob({ title, raw_description, threshold, top_k }) {
  const body = { title, raw_description };
  if (threshold !== undefined) body.threshold = threshold;
  if (top_k !== undefined) body.top_k = top_k;
  return request("/v1/jobs", { method: "POST", body });
}

/**
 * Persisted MatchResults for a job, sorted final_score desc by RWS itself -- the
 * frontend never re-sorts or re-scores. Includes the lazily-populated `explanation`
 * (unlike createJob's response).
 * @param {string} jobId
 * @returns {Promise<Array<object>>} MatchResultResponse[]
 */
export function getJobMatches(jobId) {
  return request(`/v1/jobs/${jobId}/matches`);
}
