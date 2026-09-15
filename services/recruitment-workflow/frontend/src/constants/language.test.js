// Framework-free unit tests for constants/language.js, run via Node's own built-in
// test runner (zero new dependencies/config -- this project has never introduced a
// frontend test framework like Jest/Vitest, relying on real-browser Playwright
// verification instead; these are the pure-logic pieces that are worth covering
// deterministically in between those live passes).
//
// Run from services/recruitment-workflow/frontend:
//   node --test src/constants/language.test.js
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CEFR_LEVELS,
  EVALUATION_STATUS_LABELS,
  EVALUATION_STATUS_TONE,
  INSUFFICIENT_EVIDENCE_EXPLANATION,
  LANGUAGE_GATE_LABELS,
  LANGUAGE_GATE_TONE,
  LANGUAGE_LABELS,
  describeDimensionScore,
  describeLanguageGate,
  formatLanguageRequirement,
} from "./language.js";

test("LANGUAGE_GATE_LABELS and LANGUAGE_GATE_TONE cover the exact same set of gate statuses", () => {
  const labelKeys = Object.keys(LANGUAGE_GATE_LABELS).sort();
  const toneKeys = Object.keys(LANGUAGE_GATE_TONE).sort();
  assert.deepEqual(labelKeys, toneKeys);
  assert.deepEqual(labelKeys, ["failed", "needs_review", "not_required", "passed", "pending"]);
});

test("LANGUAGE_GATE_LABELS uses the canonical 'Meets requirement' wording, not a second name for the same state", () => {
  assert.equal(LANGUAGE_GATE_LABELS.passed, "Meets requirement");
  assert.notEqual(LANGUAGE_GATE_LABELS.passed, "Passed");
});

test("EVALUATION_STATUS_LABELS and EVALUATION_STATUS_TONE cover the exact same set of statuses", () => {
  const labelKeys = Object.keys(EVALUATION_STATUS_LABELS).sort();
  const toneKeys = Object.keys(EVALUATION_STATUS_TONE).sort();
  assert.deepEqual(labelKeys, toneKeys);
  assert.deepEqual(labelKeys, ["completed", "failed", "inconclusive", "insufficient_evidence"]);
});

test("insufficient_evidence is never toned like a failure", () => {
  assert.equal(EVALUATION_STATUS_TONE.insufficient_evidence, "warning");
  assert.notEqual(EVALUATION_STATUS_TONE.insufficient_evidence, EVALUATION_STATUS_TONE.failed);
});

test("INSUFFICIENT_EVIDENCE_EXPLANATION matches this task's own named wording exactly", () => {
  assert.equal(
    INSUFFICIENT_EVIDENCE_EXPLANATION,
    "VoiceHR did not collect enough usable language evidence to assign a reliable CEFR level.",
  );
});

test("describeDimensionScore: missing optional dimension data renders as 'Not assessed', not a crash or a fake 0", () => {
  assert.equal(describeDimensionScore(null), "Not assessed");
  assert.equal(describeDimensionScore(undefined), "Not assessed");
});

test("describeDimensionScore bands span the full 0-100 range without gaps or overlaps", () => {
  assert.equal(describeDimensionScore(0), "Limited");
  assert.equal(describeDimensionScore(39), "Limited");
  assert.equal(describeDimensionScore(40), "Fair");
  assert.equal(describeDimensionScore(59), "Fair");
  assert.equal(describeDimensionScore(60), "Good");
  assert.equal(describeDimensionScore(74), "Good");
  assert.equal(describeDimensionScore(75), "Strong");
  assert.equal(describeDimensionScore(89), "Strong");
  assert.equal(describeDimensionScore(90), "Excellent");
  assert.equal(describeDimensionScore(100), "Excellent");
});

test("formatLanguageRequirement: no requirement configured returns null, never a broken partial string", () => {
  assert.equal(formatLanguageRequirement(null, null), null);
  assert.equal(formatLanguageRequirement("en", null), null);
  assert.equal(formatLanguageRequirement(null, "B2"), null);
  assert.equal(formatLanguageRequirement("en", "B2"), "English · B2");
});

test("describeLanguageGate: not_required and unset gate both describe as nothing extra to show", () => {
  assert.equal(describeLanguageGate(null), null);
  assert.equal(describeLanguageGate({ status: "not_required" }), null);
});

test("describeLanguageGate: pending never claims an assessed result exists yet", () => {
  const text = describeLanguageGate({
    status: "pending", required_language: "en", required_cefr: "B2",
    assessed_language: null, assessed_cefr: null,
  });
  assert.match(text, /pending/i);
  assert.match(text, /B2 English/);
});

test("describeLanguageGate: needs_review with a missing assessed CEFR never interpolates a bare null/undefined", () => {
  const text = describeLanguageGate({
    status: "needs_review", required_language: "en", required_cefr: "B2",
    assessed_language: null, assessed_cefr: null,
  });
  assert.doesNotMatch(text, /\bnull\b/);
  assert.doesNotMatch(text, /\bundefined\b/);
  assert.match(text, /no usable result/);
});

test("describeLanguageGate: passed/failed both name required and assessed levels", () => {
  const passed = describeLanguageGate({
    status: "passed", required_language: "en", required_cefr: "B2",
    assessed_language: "en", assessed_cefr: "C1",
  });
  assert.match(passed, /Meets requirement/);
  assert.match(passed, /B2 English/);
  assert.match(passed, /C1 English/);

  const failed = describeLanguageGate({
    status: "failed", required_language: "en", required_cefr: "C1",
    assessed_language: "en", assessed_cefr: "B1",
  });
  assert.match(failed, /Below required level/);
});

test("CEFR_LEVELS and LANGUAGE_LABELS stay internally consistent with the gate/profile vocabulary", () => {
  assert.deepEqual(CEFR_LEVELS, ["A1", "A2", "B1", "B2", "C1", "C2"]);
  assert.deepEqual(Object.keys(LANGUAGE_LABELS).sort(), ["en", "fr"]);
});
