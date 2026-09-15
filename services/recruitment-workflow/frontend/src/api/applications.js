import { request } from "./client.js";

/**
 * Selects a qualified candidate for a job, creating an Application (status:
 * "selected"). RWS enforces the qualification rule itself (422 if the match didn't
 * qualify, 409 if this candidate is already selected for this job) -- the frontend
 * does not re-check `qualified` beyond disabling the button as a UX hint (§11: never
 * second-guess a MatchResult).
 *
 * @param {string} matchId
 * @returns {Promise<object>} ApplicationResponse
 */
export function selectMatch(matchId) {
  return request(`/v1/matches/${matchId}/select`, { method: "POST" });
}

/**
 * All applications for a job -- selections made so far and where each one stands.
 * Sorted newest-selected-first by RWS itself (db.py: list_applications_for_job); the
 * frontend never re-sorts.
 *
 * @param {string} jobId
 * @returns {Promise<Array<object>>} ApplicationResponse[]
 */
export function getApplications(jobId) {
  return request(`/v1/jobs/${jobId}/applications`);
}

/**
 * Every application across every job, newest-selection-first -- the global
 * counterpart to getApplications(jobId), for the Dashboard's pipeline/status counts.
 * Each row carries the same fields as ApplicationResponse plus job_title
 * (GlobalApplicationResponse). Sorted by RWS itself; the frontend never re-sorts.
 *
 * @returns {Promise<Array<object>>} GlobalApplicationResponse[]
 */
export function getAllApplications() {
  return request("/v1/applications");
}

/**
 * Invites an application's candidate to a VoiceHR interview -- or, while still
 * "selected" after a failed delivery, retries that same attempt (RWS/VoiceHR reuse
 * the same interview/token via their own idempotency key; this call is identical
 * either way). RWS itself enforces when this is allowed -- only "selected" or
 * "invited" are accepted; anything else is a 409. The frontend only shows this for
 * "selected" rows (Invite / Retry) -- once "invited", further re-sends go through
 * resendInvite() below, a UX hint that mirrors the backend's own rules, never a
 * substitute for them (§22: don't duplicate backend business rules).
 *
 * @param {string} applicationId
 * @param {"en"|"fr"} [language]
 * @returns {Promise<object>} ApplicationResponse (status: "invited")
 */
export function invite(applicationId, language = "en") {
  return request(`/v1/applications/${applicationId}/invite`, {
    method: "POST",
    body: { language },
  });
}

/**
 * Explicit resend: intentionally re-sends the candidate's existing interview
 * invitation email without creating a new interview. Only callable while "invited"
 * -- RWS itself enforces this (409 otherwise) and applies a short per-application
 * cooldown server-side (429) so a double-click can't trigger two real sends; the
 * frontend disabling the button while a request is in flight is a UX layer on top of
 * that, not a substitute for it.
 *
 * @param {string} applicationId
 * @returns {Promise<object>} ApplicationResponse (status stays "invited")
 */
export function resendInvite(applicationId) {
  return request(`/v1/applications/${applicationId}/resend-invite`, { method: "POST" });
}

/**
 * Terminal recruiter decisions. RWS itself enforces which states are decidable
 * (db.py: _DECIDABLE_STATUSES -- "selected", "invited", or "interviewed"; anything
 * else, including an already-decided application, is a 409). Showing these buttons
 * only for those statuses in the UI is a mirror of that rule, not a substitute for it
 * (§22: don't duplicate backend business rules) -- the 409 is the real backstop.
 *
 * @param {string} applicationId
 * @returns {Promise<object>} ApplicationResponse (status: "accepted")
 */
export function accept(applicationId) {
  return request(`/v1/applications/${applicationId}/accept`, { method: "POST" });
}

/**
 * @param {string} applicationId
 * @returns {Promise<object>} ApplicationResponse (status: "rejected")
 */
export function reject(applicationId) {
  return request(`/v1/applications/${applicationId}/reject`, { method: "POST" });
}
