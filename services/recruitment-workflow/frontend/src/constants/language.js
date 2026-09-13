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
  passed: "Passed",
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
