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
 * Invites (or re-invites) an application's candidate to a VoiceHR interview. RWS
 * itself enforces when this is allowed -- only "selected" (first invite) or "invited"
 * (resend) are accepted; anything else is a 409. The frontend's Invite/Resend-Invite
 * button being shown only for those two statuses is a UX hint that mirrors this rule,
 * never a substitute for it (§22: don't duplicate backend business rules).
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
