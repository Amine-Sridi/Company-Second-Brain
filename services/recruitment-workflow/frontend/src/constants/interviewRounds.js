/**
 * Shared vocabulary for the post-VoiceHR interview-rounds stage (RWS:
 * db.InterviewRound / main.py's .../interview-rounds endpoints, see handoff.md
 * milestones 33-34). A round's own status/outcome are RWS's own recorded facts --
 * nothing here computes or second-guesses either, only labels/tones them, the same
 * "display vocabulary only" rule constants/language.js already follows for the
 * language gate.
 */
import { describeLanguageGate } from "./language.js";

export const ROUND_TYPE_LABELS = {
  technical: "Technical",
  managerial: "Managerial",
};

export const ROUND_OUTCOME_LABELS = {
  pass: "Pass",
  fail: "Fail",
  needs_review: "Needs review",
};

/**
 * A round's own badge label -- "Scheduled"/"Cancelled" while there's no verdict
 * yet, otherwise the recorded outcome ("Pass"/"Fail"/"Needs review"). A completed
 * round with no recognized outcome value (shouldn't happen -- RWS's own DB CHECK
 * constraint pairs outcome with status='completed' -- but never trusted blindly
 * here either) falls back to the generic "Completed" rather than rendering
 * nothing.
 */
export function roundStatusLabel(round) {
  if (round.status === "scheduled") return "Scheduled";
  if (round.status === "cancelled") return "Cancelled";
  return ROUND_OUTCOME_LABELS[round.outcome] ?? "Completed";
}

export function roundStatusTone(round) {
  if (round.status === "scheduled") return "primary";
  if (round.status === "cancelled") return "neutral";
  if (round.outcome === "pass") return "success";
  if (round.outcome === "fail") return "danger";
  return "warning"; // needs_review, or a defensively-unknown outcome while completed
}

export function formatScheduledAt(scheduledAt) {
  if (!scheduledAt) return null;
  const date = new Date(scheduledAt);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Formats an ISO timestamp for a native `<input type="datetime-local">`'s
 * `value` (`"YYYY-MM-DDTHH:mm"`, local wall-clock time, no timezone) -- used to
 * pre-fill the Reschedule form with a round's current `scheduled_at`. This is
 * the exact inverse of the conversion the schedule/reschedule forms already do
 * on submit (`new Date(inputValue).toISOString()`): both treat the input's
 * value as local time, matching how the browser itself interprets it.
 */
export function toDatetimeLocalInputValue(scheduledAt) {
  if (!scheduledAt) return "";
  const date = new Date(scheduledAt);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/**
 * One-line tooltip for a round's own badge -- type, interviewer, and (while still
 * scheduled) when. A completed/cancelled round's notes are deliberately not
 * included here (this is a hover hint, not the full record).
 */
export function describeRound(round) {
  const type = ROUND_TYPE_LABELS[round.round_type] ?? round.round_type;
  const when = formatScheduledAt(round.scheduled_at);
  const parts = [`${type} interview`, `interviewer: ${round.interviewer_name}`];
  if (round.status === "scheduled" && when) parts.push(`scheduled for ${when}`);
  return parts.join(" · ");
}

/**
 * Mirrors main.py's create_interview_round_endpoint's own two gates exactly
 * (application not accepted/rejected, language_gate not blocking) so "Schedule
 * interview" is never offered as clickable when RWS would 422 it anyway -- RWS's
 * own check remains the real authority regardless (§22 of this project's own
 * "don't duplicate backend business rules, only mirror them" rule). Returns null
 * when scheduling is allowed, otherwise a one-line reason for the button's
 * disabled title.
 */
export function describeScheduleBlockedReason(application) {
  if (application.status === "accepted" || application.status === "rejected") {
    return `Cannot schedule an interview round: application is already ${application.status}.`;
  }
  const gate = application.language_gate ?? { status: "not_required" };
  if (gate.status === "pending" || gate.status === "failed" || gate.status === "needs_review") {
    return describeLanguageGate(gate) ?? "Cannot schedule an interview round: language screening is not clear yet.";
  }
  return null;
}
