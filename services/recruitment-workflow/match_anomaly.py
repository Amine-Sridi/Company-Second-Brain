"""
Post-match JD anomaly detection.

Pure functions only -- no DB, no HTTP, no LLM calls, the same discipline
`jd_validation.py` and `scoring.py` both follow. Runs once, synchronously, in
`main.py: submit_job`, immediately *after* every retrieved candidate for a job
has already been scored and its `MatchResult` already persisted -- never
before, never instead of, and this module never receives or returns anything
resembling a score. It only reads the already-computed outcome
(`semantic_score`/`hard_gate_passed`/`qualified`) of each `MatchResult` and,
if that outcome looks wrong, returns an advisory warning in the exact same
shape `jd_validation.ExtractionWarning` already uses -- it never rescales,
recomputes, or writes to any `MatchResult` row itself.

Why this can't live in jd_validation.py: that module only ever sees the
structured JD, before any candidate exists. This module's one real
motivating case -- observed live in this project's own data, not
hypothetical -- is a `required_skills` extraction that is individually
skill-name-shaped enough to never trip jd_validation.py's phrase-like check
(each entry short, no obvious responsibility-phrase wording), but is still a
compound/hallucinated phrase Gemini invented that no real candidate's Second
Brain skill list will ever contain verbatim under the exact-match hard gate.
`jd_validation.py` cannot see that; it has no candidates to check against.
Only after `scoring.score_candidate` has actually run against a real
retrieved pool does the pattern become visible: every candidate fails the
hard gate specifically, despite genuinely relevant retrieval (a reasonably
high semantic_score across the board rules out "the retrieved candidates
just aren't relevant" as the innocent explanation).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from jd_validation import ExtractionWarning

# A pool this small can legitimately have 0 qualified candidates for reasons
# that have nothing to do with the JD (a narrow top_k, or Second Brain simply
# not holding many relevant profiles yet) -- too small a sample to conclude
# anything about the JD itself from.
_MIN_CANDIDATES_FOR_ANOMALY_CHECK = 5

# "Reasonably strong" semantic relevance, grounded in this project's own real
# retrieval data rather than an invented round number: live second-brain
# cosine similarities for genuinely relevant candidate/JD pairs in this
# project's own data range roughly 0.54-0.84 (the real, already-documented
# `Backend Engineer (Demo)` anomaly case averages ~0.72; a second real case
# found live while building this feature averaged ~0.66), while the one real
# *irrelevant* retrieval observed averaged ~0.44. 0.5 sits between those --
# below every real relevant observation, above the one real irrelevant one --
# so a truly weak/irrelevant retrieval (which legitimately explains 0
# qualified on its own, no JD problem implicated) doesn't trigger this.
_MIN_AVG_SEMANTIC_SCORE = 0.5


@dataclass
class MatchOutcome:
    """The one slice of an already-persisted MatchResult this module needs --
    never the ORM row itself, so this module stays decoupled from db.py's
    schema and can never be tempted to write to one."""

    semantic_score: float
    hard_gate_passed: bool
    qualified: bool


def detect_match_anomaly(outcomes: list[MatchOutcome]) -> Optional[ExtractionWarning]:
    """
    Returns an advisory warning if this job's own match outcomes look
    anomalous, else None. Fires only when ALL of:
      - at least _MIN_CANDIDATES_FOR_ANOMALY_CHECK candidates were retrieved
        and scored (see threshold's own reasoning above)
      - zero candidates qualified
      - every single one failed the required-skills hard gate specifically --
        not merely a low final_score with a passing gate, which is a
        threshold/weighting question this check has nothing useful to say
        about
      - average semantic_score across all of them is still
        >= _MIN_AVG_SEMANTIC_SCORE (retrieval was genuinely relevant, so a
        weak/irrelevant candidate pool isn't the innocent explanation)
    """
    n = len(outcomes)
    if n < _MIN_CANDIDATES_FOR_ANOMALY_CHECK:
        return None
    if any(o.qualified for o in outcomes):
        return None
    if any(o.hard_gate_passed for o in outcomes):
        return None
    avg_semantic = sum(o.semantic_score for o in outcomes) / n
    if avg_semantic < _MIN_AVG_SEMANTIC_SCORE:
        return None

    return ExtractionWarning(
        field="match_outcomes",
        message=(
            f"0 of {n} candidates passed the required-skills gate despite "
            f"relevant profiles (average semantic relevance {avg_semantic:.2f}). "
            "Verify required_skills."
        ),
    )


def merge_anomaly_warning(
    current_status: str,
    current_warnings: list[ExtractionWarning],
    anomaly: Optional[ExtractionWarning],
) -> tuple[str, list[ExtractionWarning]]:
    """
    Folds a post-match anomaly warning (or none, if detect_match_anomaly found
    nothing) into a JobDescription's existing extraction_status/
    extraction_warnings -- idempotently: calling this again with the same
    anomaly and the result of a previous call returns an equal result, never a
    duplicate warning appended twice.

    Status transition, per this feature's own "preserve existing
    extraction_status" rule: "flagged" never changes (it already carries
    exactly the warning-banner treatment this anomaly needs -- see
    JobDetailPage); "clean"/"repaired" become "flagged" the first time a real
    anomaly is found, since "flagged" is the one status this app's frontend
    actually renders a warning for. There is deliberately no separate
    "warning-bearing but not flagged" status -- reusing "flagged" is what lets
    this warning surface through the existing Job Detail warning UI with zero
    new frontend surface.
    """
    if anomaly is None:
        return current_status, current_warnings

    already_present = any(
        w.field == anomaly.field and w.message == anomaly.message for w in current_warnings
    )
    new_warnings = current_warnings if already_present else [*current_warnings, anomaly]
    return "flagged", new_warnings
