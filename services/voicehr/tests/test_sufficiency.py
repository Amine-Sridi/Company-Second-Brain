"""
Tests for backend/services/sufficiency.py -- the deterministic half of "is there enough
here to grade?".

The important assertions are the two that pin down *why* this module exists:

  * every golden case that must be gradeable still is, and every case that must be
    withheld for lack of language still is (test_golden_cases_agree);
  * the relevance case is NOT expected to be caught here, and the test says so, because
    a counter cannot detect that words were off-topic (test_off_topic_passes_the_counter).

Plus the regression that motivated the change: a dense interview just under the old
300-second threshold.
"""
import pytest

from backend.services import sufficiency
from backend.services.sufficiency import assess, confidence_for_sample, measure

from tests.golden_cases import GOLDEN_CASES


def turns(*pairs):
    """(role, text) pairs -> transcript."""
    return [{"role": r, "text": t} for r, t in pairs]


def candidate_saying(text, times=1):
    out = []
    for _ in range(times):
        out.append({"role": "assistant", "text": "Tell me more about that please."})
        out.append({"role": "user", "text": text})
    return out


# ------------------------------------------------------------------ the counting itself

def test_measure_counts_only_the_candidate():
    t = turns(("assistant", "What did you do in your last role exactly?"),
              ("user", "I built and maintained backend services in Python"))
    sample = measure(t)
    assert sample.words == 8                 # candidate: 8 words
    assert sample.turns == 1
    assert sample.substantive_turns == 1
    assert sample.interviewer_words == 9     # interviewer: 9 words
    assert sample.candidate_share == round(8 / 17, 3)


def test_short_acknowledgements_are_not_substantive_turns():
    """The old gate counted any candidate turn, so three "yes"es satisfied it."""
    t = turns(("assistant", "Ready?"), ("user", "yes"),
              ("assistant", "Shall we start?"), ("user", "ok"),
              ("assistant", "Understood?"), ("user", "I think so"))
    sample = measure(t)
    assert sample.turns == 3
    assert sample.substantive_turns == 0

    sufficient, reason, _ = assess(t, duration_seconds=900.0)
    assert sufficient is False
    assert reason == sufficiency.INSUFFICIENT_LANGUAGE


def test_measure_survives_malformed_transcripts():
    for bad in (None, "not a list", [None, 3, "x"], [{"role": "user"}], [{}]):
        sample = measure(bad)
        assert sample.words == 0


# --------------------------------------------------------------------- the golden cases

@pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c["id"])
def test_golden_cases_agree(case):
    """Every golden case must land the way the committed expectations say -- except the
    relevance-only case, which this module is not responsible for (see below)."""
    if case["id"] == "off_topic_severely_thin":
        pytest.skip("relevance case: covered by test_off_topic_passes_the_counter")

    sufficient, reason, _ = assess(
        case["transcript"],
        # Long enough that the pathological-duration floor is never the operative
        # reason: this test is about the language sample, not the clock.
        duration_seconds=900.0,
        terminated=case.get("ended_reason") in ("candidate_refused", "abusive_language"),
    )
    assert sufficient is case["expect_sufficient"], (
        f"{case['id']}: expected sufficient={case['expect_sufficient']}, got {reason} "
        f"({measure(case['transcript']).as_dict()})"
    )


def test_off_topic_passes_the_counter():
    """Deliberate, documented limitation. `off_topic_severely_thin` has MORE words and
    more distinct words than `prompt_injection_attempt`, which must score -- so no
    quantity threshold can separate them. It has to pass here and be caught by the
    model's own relevance judgment in evaluator.evaluate."""
    off_topic = next(c for c in GOLDEN_CASES if c["id"] == "off_topic_severely_thin")
    must_score = next(c for c in GOLDEN_CASES if c["id"] == "prompt_injection_attempt")

    off = measure(off_topic["transcript"])
    ok = measure(must_score["transcript"])
    assert off.words > ok.words, "premise of this test no longer holds"

    sufficient, _, _ = assess(off_topic["transcript"], duration_seconds=900.0)
    assert sufficient is True
    assert off_topic["expect_sufficient"] is False  # caught elsewhere, by judgment


# ------------------------------------------------------------------------- the vetoes

def test_terminated_overrides_any_amount_of_language():
    t = candidate_saying("I have worked on distributed payment systems for several years", 12)
    assert measure(t).words > sufficiency.MIN_CANDIDATE_WORDS

    sufficient, reason, _ = assess(t, duration_seconds=900.0, terminated=True)
    assert sufficient is False
    assert reason == sufficiency.TERMINATED


def test_pathologically_short_session_is_rejected():
    t = candidate_saying("I have worked on distributed payment systems for several years", 12)
    sufficient, reason, _ = assess(t, duration_seconds=30.0)
    assert sufficient is False
    assert reason == sufficiency.TOO_SHORT


def test_duration_is_no_longer_the_operative_gate():
    """The regression this change exists for. A real stored interview ran 284 s with 485
    candidate words across 15 substantive turns and was withheld by the old
    `duration_seconds < 300` rule -- 16 seconds short of a threshold that was never
    measured against anything."""
    t = candidate_saying(
        "We coordinated releases across three separate teams and introduced a shared "
        "checklist which reduced the confusion considerably", 15,
    )
    sample = measure(t)
    assert sample.substantive_turns == 15
    assert sample.words > 200

    sufficient, reason, _ = assess(t, duration_seconds=284.0)
    assert sufficient is True, f"284s dense interview still withheld: {reason}"


# ---------------------------------------------------------------------- the confidence tier

def test_thin_but_gradeable_sample_is_low_confidence():
    """The reason the gate can stay permissive: a sample that only just clears it still
    produces a verdict, but one that says it rests on little evidence."""
    t = candidate_saying("I worked mostly on backend services and internal tooling", 4)
    sample = measure(t)
    assert sample.words >= sufficiency.MIN_CANDIDATE_WORDS
    assert sample.words < sufficiency.COMFORTABLE_CANDIDATE_WORDS
    assert confidence_for_sample(sample, duration_seconds=900.0) == "Low"


def test_substantial_sample_earns_more_than_low():
    t = candidate_saying(
        "We coordinated releases across three separate teams and introduced a shared "
        "checklist which reduced the confusion considerably", 15,
    )
    sample = measure(t)
    assert sample.words >= sufficiency.COMFORTABLE_CANDIDATE_WORDS
    assert confidence_for_sample(sample, duration_seconds=900.0) == "High"
    assert confidence_for_sample(sample, duration_seconds=300.0) == "Medium"


def test_inconclusive_always_forces_low_confidence():
    t = candidate_saying("A thoroughly detailed and articulate answer about my work", 20)
    sample = measure(t)
    assert confidence_for_sample(sample, duration_seconds=900.0, inconclusive=True) == "Low"


# ------------------------------------------------------------------------- explanations

@pytest.mark.parametrize("reason", [
    sufficiency.TERMINATED, sufficiency.TOO_SHORT,
    sufficiency.INSUFFICIENT_LANGUAGE, sufficiency.OFF_TOPIC, sufficiency.SUFFICIENT,
])
def test_every_reason_explains_itself_with_numbers(reason):
    """The point of the reason codes: a recruiter reading a withheld report must be able
    to see what produced the decision, not just that one was made."""
    sample = measure(candidate_saying("I worked on backend services for three years", 2))
    text = sufficiency.explain(reason, sample)
    assert str(sample.words) in text
    assert len(text) > 40
