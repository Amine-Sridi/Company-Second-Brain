"""
tests/test_match_anomaly.py
------------------------------
Pure unit tests for match_anomaly.py -- no DB, no HTTP, no LLM, no mocking: every
case is plain data in, plain data out. Mirrors test_jd_validation.py's own style
for the same reason: this is the one function that decides whether a job's match
*outcome* gets flagged post-scoring, so every branch is covered directly rather
than only through a route-level test.
"""
from __future__ import annotations

from jd_validation import ExtractionWarning
from match_anomaly import MatchOutcome, detect_match_anomaly, merge_anomaly_warning


def _outcomes(n: int, semantic_score: float, hard_gate_passed: bool, qualified: bool) -> list[MatchOutcome]:
    return [
        MatchOutcome(semantic_score=semantic_score, hard_gate_passed=hard_gate_passed, qualified=qualified)
        for _ in range(n)
    ]


# ---------------------------------------------------------------------------
# detect_match_anomaly
# ---------------------------------------------------------------------------

def test_fires_when_all_conditions_are_met():
    # 5 candidates, all reasonably relevant (0.7), all failed the hard gate, none
    # qualified -- the real anomaly this feature exists to catch.
    outcomes = _outcomes(5, semantic_score=0.7, hard_gate_passed=False, qualified=False)
    warning = detect_match_anomaly(outcomes)
    assert warning is not None
    assert warning.field == "match_outcomes"
    assert "0 of 5" in warning.message
    assert "0.70" in warning.message
    assert "required_skills" in warning.message


def test_does_not_fire_for_low_semantic_relevance():
    # Same shape (all failed gate, none qualified) but the retrieved pool just
    # isn't relevant to begin with -- that alone explains 0 qualified, no JD
    # problem implicated.
    outcomes = _outcomes(5, semantic_score=0.3, hard_gate_passed=False, qualified=False)
    assert detect_match_anomaly(outcomes) is None


def test_does_not_fire_when_some_candidates_pass_the_hard_gate():
    outcomes = _outcomes(4, semantic_score=0.7, hard_gate_passed=False, qualified=False)
    outcomes.append(MatchOutcome(semantic_score=0.7, hard_gate_passed=True, qualified=False))
    assert detect_match_anomaly(outcomes) is None


def test_does_not_fire_when_any_candidate_qualified():
    outcomes = _outcomes(4, semantic_score=0.7, hard_gate_passed=False, qualified=False)
    outcomes.append(MatchOutcome(semantic_score=0.7, hard_gate_passed=True, qualified=True))
    assert detect_match_anomaly(outcomes) is None


def test_does_not_fire_with_too_small_a_pool():
    # Only 4 candidates -- below the minimum sample size, even though every other
    # condition is met.
    outcomes = _outcomes(4, semantic_score=0.9, hard_gate_passed=False, qualified=False)
    assert detect_match_anomaly(outcomes) is None


def test_fires_at_exactly_the_minimum_pool_size():
    outcomes = _outcomes(5, semantic_score=0.9, hard_gate_passed=False, qualified=False)
    assert detect_match_anomaly(outcomes) is not None


def test_does_not_fire_with_empty_outcomes():
    assert detect_match_anomaly([]) is None


def test_uses_average_semantic_score_not_a_single_outlier():
    # One candidate with a very high semantic score can't drag a genuinely weak
    # pool over the threshold on its own -- the average across all of them is
    # what's checked.
    outcomes = [
        MatchOutcome(semantic_score=0.95, hard_gate_passed=False, qualified=False),
        MatchOutcome(semantic_score=0.2, hard_gate_passed=False, qualified=False),
        MatchOutcome(semantic_score=0.2, hard_gate_passed=False, qualified=False),
        MatchOutcome(semantic_score=0.2, hard_gate_passed=False, qualified=False),
        MatchOutcome(semantic_score=0.2, hard_gate_passed=False, qualified=False),
    ]
    assert detect_match_anomaly(outcomes) is None


def test_average_semantic_score_at_exactly_the_threshold_fires():
    outcomes = _outcomes(5, semantic_score=0.5, hard_gate_passed=False, qualified=False)
    assert detect_match_anomaly(outcomes) is not None


# ---------------------------------------------------------------------------
# merge_anomaly_warning
# ---------------------------------------------------------------------------

def test_merge_with_no_anomaly_returns_status_and_warnings_unchanged():
    warnings = [ExtractionWarning(field="required_skills", message="pre-existing")]
    status, result_warnings = merge_anomaly_warning("clean", warnings, None)
    assert status == "clean"
    assert result_warnings == warnings


def test_merge_promotes_clean_to_flagged():
    anomaly = ExtractionWarning(field="match_outcomes", message="0 of 5 ...")
    status, warnings = merge_anomaly_warning("clean", [], anomaly)
    assert status == "flagged"
    assert warnings == [anomaly]


def test_merge_promotes_repaired_to_flagged():
    anomaly = ExtractionWarning(field="match_outcomes", message="0 of 5 ...")
    status, warnings = merge_anomaly_warning("repaired", [], anomaly)
    assert status == "flagged"
    assert warnings == [anomaly]


def test_merge_leaves_already_flagged_status_as_flagged():
    existing = ExtractionWarning(field="required_skills", message="phrase-like")
    anomaly = ExtractionWarning(field="match_outcomes", message="0 of 5 ...")
    status, warnings = merge_anomaly_warning("flagged", [existing], anomaly)
    assert status == "flagged"
    assert warnings == [existing, anomaly]


def test_merge_is_idempotent_does_not_duplicate_the_same_warning():
    anomaly = ExtractionWarning(field="match_outcomes", message="0 of 5 ...")
    status_1, warnings_1 = merge_anomaly_warning("clean", [], anomaly)
    # Re-applying the exact same anomaly to the result of the first call must not
    # append a second copy -- this is what "avoid duplicate warnings on repeated
    # reads/reprocessing" means at the pure-function level.
    status_2, warnings_2 = merge_anomaly_warning(status_1, warnings_1, anomaly)
    assert status_2 == "flagged"
    assert warnings_2 == [anomaly]
    assert len(warnings_2) == 1


def test_merge_keeps_a_different_pre_existing_warning_when_appending_anomaly():
    existing = ExtractionWarning(field="required_skills", message="phrase-like")
    anomaly = ExtractionWarning(field="match_outcomes", message="0 of 5 ...")
    status, warnings = merge_anomaly_warning("clean", [existing], anomaly)
    assert status == "flagged"
    assert warnings == [existing, anomaly]
