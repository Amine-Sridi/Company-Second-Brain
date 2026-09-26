import { request } from "./client.js";

/**
 * Full interview report for one application -- RWS's transient proxy to VoiceHR
 * (GET /v1/interviews/{application_id}/report). Self-sufficient for rendering a
 * report page: candidate_name/candidate_email, scores/dimensions, strengths/
 * concerns/evidence, stats, transcript, and recording.available/recording.url
 * (already rewritten by RWS to a same-origin URL -- never a VoiceHR one). Not
 * cached here; call fresh each time a report is viewed.
 *
 * @param {string} applicationId
 * @returns {Promise<object>} the report, shaped by VoiceHR (see
 *   services/voicehr/backend/api/routes/report.py: build_report)
 */
export function getInterviewReport(applicationId) {
  return request(`/v1/interviews/${applicationId}/report`);
}
