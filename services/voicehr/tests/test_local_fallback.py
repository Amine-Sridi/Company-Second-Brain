"""
Tests for the local evaluation fallback (milestone 61).

The fallback exists to keep VoiceHR producing *something* during a Gemini outage. The
invariants worth proving are not "it returns a dict" but the three that stop it from
doing harm:

  1. it is only ever reached on an availability failure, never on an application bug;
  2. it never issues a CEFR level, because the benchmarked model compresses the scale
     (it rated the weak A1/A2 golden sample as B1) and RWS's language gate passes an
     application as soon as assessed_cefr >= required_cefr;
  3. a broken fallback does not mask why Gemini failed.
"""
import pytest

from backend.services import evaluator


TRANSCRIPT = [
    {"role": "assistant", "text": "Tell me about your last role."},
    {"role": "user", "text": "I worked as a developer for three years on backend systems."},
    {"role": "assistant", "text": "What did you find hardest?"},
    {"role": "user", "text": "Coordinating releases across teams was the hardest part for me."},
    {"role": "assistant", "text": "How did you handle it?"},
    {"role": "user", "text": "We introduced a weekly sync and a shared checklist, which helped."},
]

# A plausible local-model response: all five dimensions present, and confident enough
# that a naive implementation would happily turn it into a B2 verdict.
LOCAL_RAW = {
    "comprehension_relevance_score": 72,
    "fluency_coherence_score": 70,
    "grammar_language_control_score": 68,
    "vocabulary_range_score": 71,
    "interaction_spontaneity_score": 69,
    "sufficient_evidence": True,
    "strengths": [{"text": "Clear structure", "timestamp": "00:01"}],
    "concerns": [],
    "evidence": [],
    "candidate_speaking_time": 120,
    "word_count": 40,
}


@pytest.fixture
def fallback_enabled(monkeypatch):
    monkeypatch.setattr(evaluator.settings, "local_llm_enabled", True)
    monkeypatch.setattr(evaluator.settings, "local_llm_evaluation_model", "gemma4:e2b-it-qat")
    return evaluator.settings


def _gemini_raises(exc):
    async def _raise(prompt, budget):
        raise exc
    return _raise


async def _local_returns(_prompt):
    return dict(LOCAL_RAW)


@pytest.mark.asyncio
async def test_quota_exhaustion_falls_back_but_issues_no_cefr(monkeypatch, fallback_enabled):
    """The core safety property: the fallback runs, its dimension scores survive for
    the recruiter to read, and yet no CEFR level or overall score is produced."""
    monkeypatch.setattr(evaluator, "_evaluate_with_gemini",
                        _gemini_raises(RuntimeError("429 RESOURCE_EXHAUSTED: quota")))
    monkeypatch.setattr(evaluator, "_evaluate_with_local_model", _local_returns)

    result = await evaluator.evaluate(TRANSCRIPT, duration_seconds=600.0)

    assert result["evaluation_status"] == evaluator.FALLBACK_ADVISORY_STATUS
    # The whole point: these two are what RWS's language gate reads.
    assert result["cefr_level"] is None
    assert result["overall_score"] is None
    # ...while the advisory signal is preserved.
    assert result["fluency_score"] == 70
    assert result["grammar_score"] == 68
    assert result["evaluation_provider"] == "local:gemma4:e2b-it-qat"


@pytest.mark.asyncio
async def test_fallback_result_drives_language_gate_to_needs_review(monkeypatch, fallback_enabled):
    """End-to-end on the property that matters to a candidate: a fallback assessment
    must not be able to satisfy a job's language requirement. Uses RWS's real gate."""
    import sys
    from pathlib import Path
    rws = Path(__file__).resolve().parents[3] / "services" / "recruitment-workflow"
    sys.path.insert(0, str(rws))
    from language_gate import compute_language_gate

    monkeypatch.setattr(evaluator, "_evaluate_with_gemini",
                        _gemini_raises(RuntimeError("503 Service Unavailable")))
    monkeypatch.setattr(evaluator, "_evaluate_with_local_model", _local_returns)

    result = await evaluator.evaluate(TRANSCRIPT, duration_seconds=600.0)
    status = compute_language_gate("en", "B1", True, "en", result["cefr_level"])

    # Not "passed": the local model's numbers imply ~B2, which would clear a B1
    # requirement if they were allowed through as a verdict.
    assert status == "needs_review"


@pytest.mark.asyncio
async def test_validation_failure_does_not_reach_the_fallback(monkeypatch, fallback_enabled):
    """A Gemini response that parsed but was incomplete is an application/quality
    fault, not an outage. Answering it with a weaker model would hide the fault."""
    called = False

    async def _local(_prompt):
        nonlocal called
        called = True
        return dict(LOCAL_RAW)

    async def _incomplete(prompt, budget):
        return {"sufficient_evidence": True}  # no dimension scores at all

    monkeypatch.setattr(evaluator, "_evaluate_with_gemini", _incomplete)
    monkeypatch.setattr(evaluator, "_evaluate_with_local_model", _local)

    result = await evaluator.evaluate(TRANSCRIPT, duration_seconds=600.0)

    assert called is False
    assert result["evaluation_status"] == "failed"


@pytest.mark.asyncio
async def test_non_availability_error_does_not_reach_the_fallback(monkeypatch, fallback_enabled):
    """A programming error (here: a missing key / TypeError-shaped fault) must stay
    visible rather than being silently downgraded to an advisory result."""
    called = False

    async def _local(_prompt):
        nonlocal called
        called = True
        return dict(LOCAL_RAW)

    monkeypatch.setattr(evaluator, "_evaluate_with_gemini",
                        _gemini_raises(RuntimeError("GEMINI_API_KEY is not configured")))
    monkeypatch.setattr(evaluator, "_evaluate_with_local_model", _local)

    result = await evaluator.evaluate(TRANSCRIPT, duration_seconds=600.0)

    assert called is False
    assert result["evaluation_status"] == "failed"


@pytest.mark.asyncio
async def test_disabled_by_default(monkeypatch):
    """Nothing changes for a deployment that has not opted in."""
    monkeypatch.setattr(evaluator.settings, "local_llm_enabled", False)
    called = False

    async def _local(_prompt):
        nonlocal called
        called = True
        return dict(LOCAL_RAW)

    monkeypatch.setattr(evaluator, "_evaluate_with_gemini",
                        _gemini_raises(RuntimeError("429 quota exceeded")))
    monkeypatch.setattr(evaluator, "_evaluate_with_local_model", _local)

    result = await evaluator.evaluate(TRANSCRIPT, duration_seconds=600.0)

    assert called is False
    assert result["evaluation_status"] == "failed"


@pytest.mark.asyncio
async def test_broken_fallback_reports_the_original_gemini_failure(monkeypatch, fallback_enabled):
    """If the fallback itself fails, the recruiter must still learn that Gemini was
    the problem -- a dead Ollama must not overwrite the real cause."""
    async def _local_dies(_prompt):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(evaluator, "_evaluate_with_gemini",
                        _gemini_raises(RuntimeError("429 RESOURCE_EXHAUSTED: quota")))
    monkeypatch.setattr(evaluator, "_evaluate_with_local_model", _local_dies)

    result = await evaluator.evaluate(TRANSCRIPT, duration_seconds=600.0)

    assert result["evaluation_status"] == "failed"
    assert "quota" in result["error"].lower()


@pytest.mark.asyncio
async def test_budget_exhaustion_counts_as_unavailable(monkeypatch, fallback_enabled):
    """The retry budget can only be exhausted by consecutive retryable provider
    errors, so its RuntimeError is an availability signal, not a bug."""
    monkeypatch.setattr(
        evaluator, "_evaluate_with_gemini",
        _gemini_raises(RuntimeError("Gemini call budget exhausted for this evaluation (3 calls)")),
    )
    monkeypatch.setattr(evaluator, "_evaluate_with_local_model", _local_returns)

    result = await evaluator.evaluate(TRANSCRIPT, duration_seconds=600.0)

    assert result["evaluation_status"] == evaluator.FALLBACK_ADVISORY_STATUS


def test_advisory_status_is_accepted_by_the_shared_schema():
    """The webhook boundary uses extra="forbid" and a Literal evaluation_status, so a
    new value that was not added there would fail validation and RWS would never learn
    the interview completed at all."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from shared.schemas import InterviewAssessment

    assessment = InterviewAssessment(
        overall_score=None, language="en", cefr_level=None,
        fluency_score=70, vocabulary_score=71, grammar_score=68,
        pronunciation_score=None, comprehension_score=72,
        inconclusive=True, evaluation_status=evaluator.FALLBACK_ADVISORY_STATUS,
    )
    assert assessment.evaluation_status == "fallback_advisory"
    assert assessment.cefr_level is None
