"""
backend/services/sufficiency.py
--------------------------------
Is there enough candidate language here to defend a CEFR verdict?

This replaces a duration threshold with a measurement of the thing that actually
matters. The previous deterministic gate was:

    terminated or duration_seconds < 300 or candidate_turns < 3

`duration_seconds` measures the **session**, not the candidate. It counts the
interviewer's speech, the silences, and reconnect gaps, so a ten-minute interview in
which someone said "yes" five times passed it, while a dense 284-second interview was
rejected -- that is a real case, not a hypothetical: a stored interview with **485
candidate words across 15 substantive turns** was withheld for being 16 seconds short.
The turn count had the matching flaw: it counted *any* candidate turn, so three
one-word answers satisfied it.

**Two kinds of insufficiency, and only one of them is countable.** Measuring the golden
cases showed no word-count threshold can separate them, because they are different
problems:

    insufficient_language   too little candidate speech to observe range at all.
                            Countable, deterministic, explainable. This module.
    off_topic               plenty of words, none of them answering the question.
                            Requires judgment -- `off_topic_severely_thin` has 60 words
                            and 12 distinct words, MORE than `prompt_injection_attempt`
                            (35 words) which should score. No counter can tell those
                            apart, so this half stays with the model's own
                            `sufficient_evidence` (evaluator.py).

Set the relevance case aside and the quantity features separate cleanly on the golden
set -- candidate words (should-score min 35 vs should-withhold max 10), substantive
turns (4 vs 2), distinct words (10 vs 5).

**The thresholds below are provisional and deliberately permissive.** They are derived
from golden transcripts that are compact by construction -- the tightest case that must
still score has only 35 candidate words -- so they cannot be raised to what real speech
would warrant without contradicting the committed expectations. Real interviews are
wordier: a 42-word real sample clears this gate and is arguably too thin to defend a
band. That is what the tiering is for rather than a stricter gate: a sample that only
just clears the floor still yields a verdict, but with `confidence: "Low"`. Anchoring
these numbers properly needs roughly five substantial real interviews; there are
currently two (see handoff.md).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- Gate: below any of these there is not enough language to grade at all. ----------
# Provisional (see the module docstring). Each sits inside the clean gap the golden set
# leaves between cases that must score and cases that must be withheld, chosen toward
# the permissive end so the gate never withholds something the golden set says is
# gradeable; marginal-but-gradeable is expressed as low confidence instead.
MIN_CANDIDATE_WORDS = 30        # golden gap: (10, 35]
MIN_SUBSTANTIVE_TURNS = 3       # golden gap: (2, 4]
MIN_DISTINCT_WORDS = 8          # golden gap: (5, 10]

# A turn has to be more than an acknowledgement to count as engaging with a question.
# The old gate counted any turn at all, so "yes" / "ok" / "I don't know" satisfied it.
SUBSTANTIVE_TURN_MIN_WORDS = 5

# --- Tier: above the gate but below this is gradeable-yet-thin, hence low confidence.
COMFORTABLE_CANDIDATE_WORDS = 150

# A sanity floor only, NOT the primary gate. A session this short cannot have covered
# enough distinct prompts to be an interview, however fast the candidate spoke. Kept
# deliberately far below the old 300 s so it can never again be the operative reason a
# substantial interview is discarded.
PATHOLOGICAL_DURATION_SECONDS = 60

CANDIDATE_ROLES = ("user", "candidate")

# Reason codes. Recorded so "why was this withheld?" is answerable from the data --
# previously `insufficient_evidence` carried no reason at all, and a recruiter (or a
# jury) asking had nothing to read.
SUFFICIENT = "sufficient"
TERMINATED = "terminated"
INSUFFICIENT_LANGUAGE = "insufficient_language"
TOO_SHORT = "pathologically_short"
OFF_TOPIC = "off_topic"  # set by the evaluator from the model's judgment, not here

_WORD = re.compile(r"\b[\w']+\b")


def _words(text) -> list[str]:
    return _WORD.findall(str(text).lower())


@dataclass
class LanguageSample:
    """What the candidate actually produced. All counts, no judgment."""

    turns: int = 0
    substantive_turns: int = 0
    words: int = 0
    distinct_words: int = 0
    interviewer_words: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def candidate_share(self) -> float:
        total = self.words + self.interviewer_words
        return round(self.words / total, 3) if total else 0.0

    def as_dict(self) -> dict:
        return {
            "turns": self.turns,
            "substantive_turns": self.substantive_turns,
            "words": self.words,
            "distinct_words": self.distinct_words,
            "candidate_share": self.candidate_share,
        }


def measure(transcript: list[dict]) -> LanguageSample:
    """Counts candidate language in a transcript. Never raises on malformed input --
    a transcript is untrusted data that has already survived a live interview."""
    sample = LanguageSample()
    if not isinstance(transcript, list):
        return sample

    seen: set[str] = set()
    for turn in transcript:
        if not isinstance(turn, dict):
            continue
        words = _words(turn.get("text", ""))
        if turn.get("role") in CANDIDATE_ROLES:
            sample.turns += 1
            sample.words += len(words)
            seen.update(words)
            if len(words) >= SUBSTANTIVE_TURN_MIN_WORDS:
                sample.substantive_turns += 1
        else:
            sample.interviewer_words += len(words)
    sample.distinct_words = len(seen)
    return sample


def assess(
    transcript: list[dict],
    duration_seconds: float | None = None,
    terminated: bool = False,
) -> tuple[bool, str, LanguageSample]:
    """
    Returns (is_sufficient, reason, sample).

    `is_sufficient` False means no CEFR verdict may be issued. This decides only the
    *countable* half; the model's own relevance judgment is applied separately by the
    evaluator, and either half is enough to withhold.
    """
    sample = measure(transcript)

    # An interview the candidate refused or that was cut short for abuse is not a
    # language sample regardless of how much was said before it ended.
    if terminated:
        sample.reasons.append(TERMINATED)
        return False, TERMINATED, sample

    if duration_seconds is not None and duration_seconds < PATHOLOGICAL_DURATION_SECONDS:
        sample.reasons.append(TOO_SHORT)
        return False, TOO_SHORT, sample

    if (
        sample.words < MIN_CANDIDATE_WORDS
        or sample.substantive_turns < MIN_SUBSTANTIVE_TURNS
        or sample.distinct_words < MIN_DISTINCT_WORDS
    ):
        sample.reasons.append(INSUFFICIENT_LANGUAGE)
        return False, INSUFFICIENT_LANGUAGE, sample

    return True, SUFFICIENT, sample


def explain(reason: str, sample: LanguageSample) -> str:
    """A sentence a recruiter can read, with the numbers that produced the decision."""
    counts = (
        f"{sample.words} candidate words, {sample.substantive_turns} substantive turns "
        f"(>= {SUBSTANTIVE_TURN_MIN_WORDS} words), {sample.distinct_words} distinct words"
    )
    if reason == TERMINATED:
        return f"Interview ended early (refused or terminated). Recorded: {counts}."
    if reason == TOO_SHORT:
        return (
            f"Interview was shorter than {PATHOLOGICAL_DURATION_SECONDS}s, too short to "
            f"have covered enough prompts. Recorded: {counts}."
        )
    if reason == INSUFFICIENT_LANGUAGE:
        return (
            f"Not enough candidate speech to support a defensible CEFR level: {counts}. "
            f"Minimums are {MIN_CANDIDATE_WORDS} words, {MIN_SUBSTANTIVE_TURNS} "
            f"substantive turns, {MIN_DISTINCT_WORDS} distinct words."
        )
    if reason == OFF_TOPIC:
        return (
            f"Enough speech was recorded ({counts}) but too little of it addressed the "
            f"questions to support a defensible CEFR level."
        )
    return f"Sufficient language sample: {counts}."


def confidence_for_sample(
    sample: LanguageSample,
    duration_seconds: float | None,
    inconclusive: bool = False,
) -> str:
    """
    Low / Medium / High, driven by how much language there is rather than by clock time.

    Duration is kept as a secondary signal -- a long interview does carry information
    about how many distinct prompts were covered -- but it can no longer be the reason a
    dense sample is rated Low.
    """
    if inconclusive:
        return "Low"
    if sample.words < COMFORTABLE_CANDIDATE_WORDS:
        # Above the gate, so a verdict is issued, but it rests on a thin sample and says so.
        return "Low"
    if duration_seconds is not None and duration_seconds >= 480:
        return "High"
    return "Medium"
