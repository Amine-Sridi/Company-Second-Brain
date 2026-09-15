// Framework-free unit tests for constants/interviewRounds.js, run via Node's own
// built-in test runner -- same convention as constants/language.test.js (zero new
// dependencies/config; this project relies on real-browser Playwright verification
// for anything involving actual rendering, these cover only the pure display logic).
//
// Run from services/recruitment-workflow/frontend:
//   node --test src/constants/interviewRounds.test.js
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ROUND_TYPE_LABELS,
  ROUND_OUTCOME_LABELS,
  roundStatusLabel,
  roundStatusTone,
  formatScheduledAt,
  toDatetimeLocalInputValue,
  describeRound,
  describeScheduleBlockedReason,
} from "./interviewRounds.js";

test("roundStatusLabel: scheduled and cancelled never claim a verdict exists", () => {
  assert.equal(roundStatusLabel({ status: "scheduled", outcome: null }), "Scheduled");
  assert.equal(roundStatusLabel({ status: "cancelled", outcome: null }), "Cancelled");
});

test("roundStatusLabel: completed renders the recorded outcome, not a generic label", () => {
  assert.equal(roundStatusLabel({ status: "completed", outcome: "pass" }), "Pass");
  assert.equal(roundStatusLabel({ status: "completed", outcome: "fail" }), "Fail");
  assert.equal(roundStatusLabel({ status: "completed", outcome: "needs_review" }), "Needs review");
});

test("roundStatusLabel: completed with an unrecognized outcome falls back to 'Completed', never blank", () => {
  assert.equal(roundStatusLabel({ status: "completed", outcome: null }), "Completed");
  assert.equal(roundStatusLabel({ status: "completed", outcome: "something-unexpected" }), "Completed");
});

test("roundStatusTone: scheduled/cancelled use neutral-family tones, never success/danger", () => {
  assert.equal(roundStatusTone({ status: "scheduled", outcome: null }), "primary");
  assert.equal(roundStatusTone({ status: "cancelled", outcome: null }), "neutral");
});

test("roundStatusTone: completed outcome maps to the same tone vocabulary as the language gate", () => {
  assert.equal(roundStatusTone({ status: "completed", outcome: "pass" }), "success");
  assert.equal(roundStatusTone({ status: "completed", outcome: "fail" }), "danger");
  assert.equal(roundStatusTone({ status: "completed", outcome: "needs_review" }), "warning");
});

test("formatScheduledAt: null/invalid input never throws or renders 'Invalid Date'", () => {
  assert.equal(formatScheduledAt(null), null);
  assert.equal(formatScheduledAt(undefined), null);
  assert.equal(formatScheduledAt("not-a-date"), null);
});

test("formatScheduledAt: a real ISO timestamp renders a non-empty string", () => {
  const result = formatScheduledAt("2026-10-01T10:00:00Z");
  assert.equal(typeof result, "string");
  assert.ok(result.length > 0);
});

test("toDatetimeLocalInputValue: null/invalid input renders an empty string, never 'Invalid Date'", () => {
  assert.equal(toDatetimeLocalInputValue(null), "");
  assert.equal(toDatetimeLocalInputValue(undefined), "");
  assert.equal(toDatetimeLocalInputValue("not-a-date"), "");
});

test("toDatetimeLocalInputValue: matches the exact YYYY-MM-DDTHH:mm shape a datetime-local input expects", () => {
  const result = toDatetimeLocalInputValue("2026-10-01T10:00:00Z");
  assert.match(result, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
});

test("toDatetimeLocalInputValue is the exact inverse of the ISO conversion the schedule forms submit", () => {
  // Round-trips through the same local-time interpretation both the schedule and
  // reschedule forms rely on (new Date(inputValue).toISOString() on submit).
  const original = "2026-10-01T10:00:00.000Z";
  const inputValue = toDatetimeLocalInputValue(original);
  const roundTripped = new Date(inputValue).toISOString();
  assert.equal(roundTripped, original);
});

test("describeRound: names the type and interviewer", () => {
  const text = describeRound({
    round_type: "technical",
    interviewer_name: "Jane Doe",
    status: "completed",
    scheduled_at: "2026-10-01T10:00:00Z",
  });
  assert.match(text, /Technical/);
  assert.match(text, /Jane Doe/);
});

test("describeRound: only mentions the scheduled time while still scheduled, not once completed", () => {
  const scheduled = describeRound({
    round_type: "managerial", interviewer_name: "Amine", status: "scheduled",
    scheduled_at: "2026-10-01T10:00:00Z",
  });
  assert.match(scheduled, /scheduled for/);

  const completed = describeRound({
    round_type: "managerial", interviewer_name: "Amine", status: "completed",
    scheduled_at: "2026-10-01T10:00:00Z",
  });
  assert.doesNotMatch(completed, /scheduled for/);
});

test("describeScheduleBlockedReason: null (schedulable) when not_required and not decided", () => {
  assert.equal(
    describeScheduleBlockedReason({ status: "interviewed", language_gate: { status: "not_required" } }),
    null,
  );
});

test("describeScheduleBlockedReason: null (schedulable) when the gate passed", () => {
  assert.equal(
    describeScheduleBlockedReason({
      status: "interviewed",
      language_gate: { status: "passed", required_language: "en", required_cefr: "B2", assessed_language: "en", assessed_cefr: "C1" },
    }),
    null,
  );
});

test("describeScheduleBlockedReason: blocked once accepted, regardless of gate status", () => {
  const reason = describeScheduleBlockedReason({
    status: "accepted",
    language_gate: { status: "passed" },
  });
  assert.match(reason, /already accepted/);
});

test("describeScheduleBlockedReason: blocked once rejected, regardless of gate status", () => {
  const reason = describeScheduleBlockedReason({
    status: "rejected",
    language_gate: { status: "not_required" },
  });
  assert.match(reason, /already rejected/);
});

test("describeScheduleBlockedReason: blocked while the language gate is pending/failed/needs_review", () => {
  for (const status of ["pending", "failed", "needs_review"]) {
    const reason = describeScheduleBlockedReason({
      status: "interviewed",
      language_gate: { status, required_language: "en", required_cefr: "B2", assessed_language: "en", assessed_cefr: "A2" },
    });
    assert.notEqual(reason, null, `expected a blocking reason for gate status ${status}`);
  }
});

test("ROUND_TYPE_LABELS and ROUND_OUTCOME_LABELS cover exactly the values the backend can send", () => {
  assert.deepEqual(Object.keys(ROUND_TYPE_LABELS).sort(), ["managerial", "technical"]);
  assert.deepEqual(Object.keys(ROUND_OUTCOME_LABELS).sort(), ["fail", "needs_review", "pass"]);
});
