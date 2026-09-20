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

/**
 * Schedules a new post-VoiceHR technical/managerial interview round. RWS itself
 * enforces both preconditions -- the application must not already be
 * accepted/rejected, and the language eligibility gate must not be
 * pending/failed/needs_review (422 naming which, mirroring accept's own gate) --
 * this frontend only disables the triggering button as a UX hint
 * (constants/interviewRounds.js: describeScheduleBlockedReason), never a
 * substitute for RWS's own check (§22).
 *
 * Phase 1 of automating human interview scheduling (see handoff.md): RWS also
 * best-effort emails the candidate and interviewer once the round is created
 * (interviewerEmail is required for this reason -- RWS's own request schema
 * rejects a malformed/missing address with a 422). A notification failure never
 * fails this call or the round's own creation -- the returned InterviewRoundResponse
 * always has `notification_status` ("sent"/"failed") for the caller to render.
 *
 * @param {string} applicationId
 * @param {object} payload
 * @param {"technical"|"managerial"} payload.roundType
 * @param {string} payload.interviewerName
 * @param {string} payload.interviewerEmail
 * @param {string} [payload.location] - a meeting URL or a physical address, recruiter's choice
 * @param {string} payload.scheduledAt - ISO 8601 datetime string
 * @param {string} [payload.notes]
 * @returns {Promise<object>} InterviewRoundResponse (status: "scheduled")
 */
export function createInterviewRound(
  applicationId,
  { roundType, interviewerName, interviewerEmail, location, scheduledAt, notes }
) {
  return request(`/v1/applications/${applicationId}/interview-rounds`, {
    method: "POST",
    body: {
      round_type: roundType,
      interviewer_name: interviewerName,
      interviewer_email: interviewerEmail,
      scheduled_at: scheduledAt,
      ...(location ? { location } : {}),
      ...(notes ? { notes } : {}),
    },
  });
}

/**
 * Updates an existing round's scheduled_at. RWS itself only allows this while
 * the round is "scheduled" (409, naming its current status, otherwise) and
 * never touches Application.status or the round's own round_type/
 * interviewer_name/notes.
 *
 * @param {string} applicationId
 * @param {string} roundId
 * @param {object} payload
 * @param {string} payload.scheduledAt - ISO 8601 datetime string
 * @returns {Promise<object>} InterviewRoundResponse (status stays "scheduled")
 */
export function rescheduleInterviewRound(applicationId, roundId, { scheduledAt }) {
  return request(`/v1/applications/${applicationId}/interview-rounds/${roundId}/reschedule`, {
    method: "POST",
    body: { scheduled_at: scheduledAt },
  });
}

/**
 * Records a round's outcome. RWS itself requires a valid outcome (422
 * otherwise -- Literal["pass", "fail", "needs_review"]) and only allows this
 * while the round is "scheduled" (409, naming its current status, otherwise);
 * never touches Application.status. Omitting notes leaves whatever was
 * recorded at scheduling time untouched -- RWS's own rule (db.py:
 * complete_interview_round), not re-implemented here.
 *
 * @param {string} applicationId
 * @param {string} roundId
 * @param {object} payload
 * @param {"pass"|"fail"|"needs_review"} payload.outcome
 * @param {string} [payload.notes]
 * @returns {Promise<object>} InterviewRoundResponse (status: "completed")
 */
export function completeInterviewRound(applicationId, roundId, { outcome, notes }) {
  return request(`/v1/applications/${applicationId}/interview-rounds/${roundId}/complete`, {
    method: "POST",
    body: { outcome, ...(notes ? { notes } : {}) },
  });
}

/**
 * Cancels a round. RWS itself only allows this while the round is "scheduled"
 * (409, naming its current status, otherwise) -- terminal, no un-cancel.
 *
 * @param {string} applicationId
 * @param {string} roundId
 * @returns {Promise<object>} InterviewRoundResponse (status: "cancelled")
 */
export function cancelInterviewRound(applicationId, roundId) {
  return request(`/v1/applications/${applicationId}/interview-rounds/${roundId}/cancel`, { method: "POST" });
}

/**
 * Explicit resend of a round's candidate+interviewer notification email. RWS
 * itself only allows this while the round is "scheduled" (409 otherwise) and
 * rate-limits rapid repeat clicks (429, same 10s cooldown pattern
 * resendInvite's own endpoint already uses) -- unlike creating a round, a
 * failed resend rejects with a 502 (its entire purpose is delivery; there is no
 * separate "the round still exists" consolation the way create has).
 *
 * @param {string} applicationId
 * @param {string} roundId
 * @returns {Promise<object>} InterviewRoundResponse (notification_status: "sent")
 */
export function resendInterviewRoundNotification(applicationId, roundId) {
  return request(
    `/v1/applications/${applicationId}/interview-rounds/${roundId}/resend-notification`,
    { method: "POST" }
  );
}
