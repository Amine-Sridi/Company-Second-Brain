"""
Deterministic, job-specific VoiceHR language eligibility gate.

RWS owns this decision end to end -- Gemini/VoiceHR only ever produce the *assessed*
language and CEFR level (see shared/schemas.InterviewAssessment); nothing here calls an
LLM, and nothing here reads or writes MatchResult/final_score. This is a second-stage
screening signal, evaluated after resume matching already qualified the candidate, per
docs/ARCHITECTURE_DECISIONS.md's "the LLM never determines... threshold comparisons" rule extended to this
second gate the same way it already applies to the first one (scoring.py).

compute_language_gate is a pure function: same five inputs always produce the same
status, with no I/O, no clock, no randomness -- both main.py's response-shaping
(_application_response) and its accept-time enforcement call this same function on the
same underlying data, so "what the recruiter is shown" and "what accept enforces" can
never drift apart into two different answers.
"""
from __future__ import annotations

from typing import Literal, Optional

CEFR_RANK: dict[str, int] = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}

LanguageGateStatus = Literal["not_required", "pending", "passed", "failed", "needs_review"]

# Statuses under which the language gate blocks an accept/advance decision --
# main.py's accept_application is the one and only enforcement point (see its own
# docstring for why reject_application is deliberately never gated the same way).
BLOCKING_STATUSES: frozenset[str] = frozenset({"pending", "failed", "needs_review"})


def compute_language_gate(
    required_language: Optional[str],
    required_cefr: Optional[str],
    has_interview_result: bool,
    assessed_language: Optional[str],
    assessed_cefr: Optional[str],
) -> LanguageGateStatus:
    """
    - not_required: the job (and therefore this application, via its selection-time
      snapshot) has no language requirement configured. Preserves current behavior
      for every job/application that predates this feature.
    - pending: a requirement exists but no terminal VoiceHR result has been recorded
      yet for this application (interview_assessment is still None).
    - needs_review: a requirement exists, an interview result exists, but the
      assessment data can't be compared cleanly -- missing/null assessed
      language/CEFR (e.g. VoiceHR's own evaluation itself failed), an assessed
      language that doesn't match the required one (a data/process error, not a
      genuine ability shortfall), or a CEFR value outside the known A1-C2 scale on
      either side.
    - passed / failed: a clean, comparable result -- CEFR_RANK[assessed] compared
      against CEFR_RANK[required] once the language itself is confirmed to match.
    """
    if not required_language or not required_cefr:
        return "not_required"
    if not has_interview_result:
        return "pending"
    if not assessed_language or not assessed_cefr:
        return "needs_review"
    if assessed_cefr not in CEFR_RANK or required_cefr not in CEFR_RANK:
        return "needs_review"
    if assessed_language != required_language:
        return "needs_review"
    return "passed" if CEFR_RANK[assessed_cefr] >= CEFR_RANK[required_cefr] else "failed"
