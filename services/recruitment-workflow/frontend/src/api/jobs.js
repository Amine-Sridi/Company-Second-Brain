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
 * required_language/min_language_cefr configure the optional VoiceHR language
 * eligibility gate (language_gate.py) -- both or neither, never just one; RWS itself
 * enforces that pairing (422 otherwise). Recruiter-set only: never derived from the
 * raw description or Gemini's JD structuring.
 *
 * @param {{ title: string, raw_description: string, threshold?: number, top_k?: number, required_language?: "en"|"fr", min_language_cefr?: "A1"|"A2"|"B1"|"B2"|"C1"|"C2" }} params
 * @returns {Promise<object>} the full JobResponse (job_id, requirements, matches, ...)
 */
export function createJob({ title, raw_description, threshold, top_k, required_language, min_language_cefr }) {
  const body = { title, raw_description };
  if (threshold !== undefined) body.threshold = threshold;
  if (top_k !== undefined) body.top_k = top_k;
  if (required_language !== undefined) body.required_language = required_language;
  if (min_language_cefr !== undefined) body.min_language_cefr = min_language_cefr;
  return request("/v1/jobs", { method: "POST", body });
}

/**
 * Configures (or clears, by passing both as null) an existing job's VoiceHR language
 * eligibility requirement. Never touches any Application already selected for this
 * job -- each one keeps the requirement snapshot it took at its own selection time
 * (see handoff.md's language_gate.py notes), so editing a job here can never
 * silently change an already-reported gate result.
 *
 * @param {string} jobId
 * @param {{ required_language: "en"|"fr"|null, min_language_cefr: "A1"|"A2"|"B1"|"B2"|"C1"|"C2"|null }} requirement
 * @returns {Promise<object>} JobSummaryResponse
 */
export function setJobLanguageRequirement(jobId, { required_language, min_language_cefr }) {
  return request(`/v1/jobs/${jobId}/language-requirement`, {
    method: "PATCH",
    body: { required_language, min_language_cefr },
  });
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
