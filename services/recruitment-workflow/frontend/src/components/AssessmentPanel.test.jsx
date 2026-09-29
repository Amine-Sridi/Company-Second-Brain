/**
 * AssessmentPanel — the component that renders an interview result to a recruiter.
 *
 * Worth testing over the other 28 components because it is the one that makes
 * *decisions* about what a recruiter sees, and each branch corresponds to a real
 * backend state that is easy to conflate:
 *
 *   failed                -> no scores exist at all
 *   insufficient_evidence -> scores exist but the CEFR verdict was withheld
 *   completed             -> the full grid
 *
 * Conflating the middle case with a failure would be a genuine misrepresentation: the
 * evaluator withholding a verdict is not the same as the evaluation breaking, and
 * milestone 48's real interview produced exactly that state.
 *
 * It also pins one deliberate omission (pronunciation) that a well-meaning future
 * change would otherwise "fix" back into existence.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import AssessmentPanel from "./AssessmentPanel.jsx";
import { INSUFFICIENT_EVIDENCE_EXPLANATION } from "../constants/language.js";

const complete = {
  overall_score: 78,
  cefr_level: "B2",
  fluency_score: 80,
  vocabulary_score: 75,
  grammar_score: 72,
  comprehension_score: 85,
  interaction_score: 70,
  inconclusive: false,
  evaluation_status: "completed",
};

describe("a completed evaluation", () => {
  it("shows every dimension the backend sends", () => {
    render(<AssessmentPanel assessment={complete} />);
    for (const label of ["Overall score", "Fluency", "Vocabulary", "Grammar", "Comprehension", "Interaction"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("78/100")).toBeInTheDocument();
    expect(screen.getByText("B2")).toBeInTheDocument();
  });

  it("never renders pronunciation", () => {
    // Deliberate and load-bearing: the evaluator only ever sees a text transcript, so
    // pronunciation_score is always null. A permanently blank row would invite "why is
    // this always empty?" instead of answering it. This test exists so the omission
    // survives someone "completing" the grid.
    render(<AssessmentPanel assessment={{ ...complete, pronunciation_score: 91 }} />);
    expect(screen.queryByText(/pronunciation/i)).not.toBeInTheDocument();
    expect(screen.queryByText("91/100")).not.toBeInTheDocument();
  });

  it("renders a missing score as an em dash, not null", () => {
    render(<AssessmentPanel assessment={{ ...complete, vocabulary_score: null }} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("null/100")).not.toBeInTheDocument();
  });

  it("renders a zero score rather than treating it as missing", () => {
    // 0 is falsy in JS; a `value || "—"` check would silently hide a genuine zero.
    render(<AssessmentPanel assessment={{ ...complete, grammar_score: 0 }} />);
    expect(screen.getByText("0/100")).toBeInTheDocument();
  });
});

describe("a failed evaluation", () => {
  const failed = { ...complete, evaluation_status: "failed", overall_score: null, cefr_level: null };

  it("says no scores are available instead of showing an empty grid", () => {
    render(<AssessmentPanel assessment={failed} />);
    expect(screen.getByText(/no scores are available/i)).toBeInTheDocument();
    expect(screen.queryByText("Fluency")).not.toBeInTheDocument();
  });
});

describe("an insufficient-evidence evaluation", () => {
  const insufficient = {
    ...complete,
    evaluation_status: "insufficient_evidence",
    overall_score: null,
    cefr_level: null,
    inconclusive: true,
  };

  it("explains the withheld verdict rather than showing a failure", () => {
    render(<AssessmentPanel assessment={insufficient} />);
    expect(screen.getByText(INSUFFICIENT_EVIDENCE_EXPLANATION)).toBeInTheDocument();
    expect(screen.queryByText(/no scores are available/i)).not.toBeInTheDocument();
  });

  it("does not also show the low-confidence note", () => {
    // Would be redundant: the dedicated explanation above already says it, and the badge
    // carries a warning tone. Two notices for one state reads as two problems.
    render(<AssessmentPanel assessment={insufficient} />);
    expect(screen.queryByText(/low-confidence/i)).not.toBeInTheDocument();
  });

  it("still shows the grid when a CEFR level did come through", () => {
    // insufficient_evidence WITH a level is a different state from without one, and the
    // component only suppresses the grid in the latter.
    render(<AssessmentPanel assessment={{ ...insufficient, cefr_level: "B1" }} />);
    expect(screen.getByText("Fluency")).toBeInTheDocument();
    expect(screen.getByText("B1")).toBeInTheDocument();
  });
});

describe("an inconclusive but otherwise normal evaluation", () => {
  it("flags low confidence alongside the scores", () => {
    render(<AssessmentPanel assessment={{ ...complete, inconclusive: true }} />);
    expect(screen.getByText(/low-confidence/i)).toBeInTheDocument();
    expect(screen.getByText("Fluency")).toBeInTheDocument();
  });
});
