/**
 * Shared vocabulary for the VoiceHR language eligibility gate (RWS: language_gate.py).
 * CEFR_LEVELS is the full A1-C2 scale in ascending order -- the same ordering
 * language_gate.CEFR_RANK encodes server-side; this is display/option-list use only,
 * never used here to compute pass/fail (that is exclusively a backend decision).
 */
export const LANGUAGE_LABELS = {
  en: "English",
  fr: "French",
};

export const CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"];

export function formatLanguageRequirement(requiredLanguage, minLanguageCefr) {
  if (!requiredLanguage || !minLanguageCefr) return null;
  return `${LANGUAGE_LABELS[requiredLanguage] ?? requiredLanguage} · ${minLanguageCefr}`;
}

export const LANGUAGE_GATE_LABELS = {
  not_required: "Not required",
  pending: "Pending",
  // "Meets requirement" (not the older, terser "Passed") -- one canonical wording
  // reused everywhere a gate result is shown (ApplicationsPage's column,
  // ReportDetailPage's Language Screening summary, ...), per this project's own
  // "do not introduce multiple names for the same gate state" rule.
  passed: "Meets requirement",
  failed: "Below requirement",
  needs_review: "Needs review",
};

export const LANGUAGE_GATE_TONE = {
  not_required: "neutral",
  pending: "primary",
  passed: "success",
  failed: "danger",
  needs_review: "warning",
};

/**
 * One-line explanation for the Applications-page tooltip and the Accept button's
 * disabled title -- mirrors the wording RWS itself returns in a 422 (main.py:
 * accept_application), kept in sync manually since the frontend never re-derives
 * the gate status itself, only renders what RWS already computed.
 */
export function describeLanguageGate(gate) {
  if (!gate || gate.status === "not_required") return null;
  const required = `${gate.required_cefr} ${LANGUAGE_LABELS[gate.required_language] ?? gate.required_language}`;
  if (gate.status === "pending") {
    return `Language screening pending (requires ${required}).`;
  }
  const assessed = gate.assessed_cefr
    ? `${gate.assessed_cefr} ${LANGUAGE_LABELS[gate.assessed_language] ?? gate.assessed_language ?? ""}`.trim()
    : "no usable result";
  if (gate.status === "needs_review") {
    return `Language screening needs review (required ${required}, assessed ${assessed}).`;
  }
  if (gate.status === "failed") {
    return `Below required level (required ${required}, assessed ${assessed}).`;
  }
  return `Meets requirement (required ${required}, assessed ${assessed}).`;
}

/**
 * VoiceHR's own assessment-completion status (interview_assessment.evaluation_status
 * / VoiceHR report.evaluation_status) -- a different axis from the gate's own status
 * above: this describes whether VoiceHR produced a usable result at all, the gate
 * describes whether that result meets a job's requirement. Shared here (not
 * redefined per-component) so AssessmentPanel, InterviewsPage, and ReportsPage all
 * show the same word for the same state -- this project's own "report consistency"
 * rule applied to this second vocabulary, not just the gate's.
 */
export const EVALUATION_STATUS_LABELS = {
  completed: "Completed",
  inconclusive: "Inconclusive",
  failed: "Failed",
  // A transcript that produced a response but not enough usable language evidence
  // for a defensible CEFR verdict -- distinct from "failed" (a technical evaluator
  // error) and never styled like one: inconclusive is not the same as failed.
  insufficient_evidence: "Insufficient evidence",
};

export const EVALUATION_STATUS_TONE = {
  completed: "success",
  inconclusive: "warning",
  failed: "danger",
  insufficient_evidence: "warning",
};

/**
 * The exact explanation text this task's own spec named for the insufficient-
 * evidence state -- defined once so every surface that shows it (AssessmentPanel,
 * ReportDetailPage's Assessment Quality section) uses identical wording.
 */
export const INSUFFICIENT_EVIDENCE_EXPLANATION =
  "VoiceHR did not collect enough usable language evidence to assign a reliable CEFR level.";

/**
 * A concise, human-readable band for a single 0-100 rubric dimension score --
 * display-only sugar on top of the raw number, never used to compute anything
 * (CEFR itself is still computed exclusively by VoiceHR's own weighted_overall,
 * never re-derived here). Deliberately coarser and differently-worded than the
 * CEFR scale itself, so a recruiter never mistakes a per-dimension band for a
 * second, competing language-level verdict.
 */
export function describeDimensionScore(score) {
  if (score == null) return "Not assessed";
  if (score >= 90) return "Excellent";
  if (score >= 75) return "Strong";
  if (score >= 60) return "Good";
  if (score >= 40) return "Fair";
  return "Limited";
}
