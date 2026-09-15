"""
Golden-case benchmark tests for the VoiceHR interview evaluator
(backend/services/evaluator.py). Case definitions live in golden_cases.py; the
shared analysis logic (dimension coverage, contradiction detection, missing-field
checks) lives in benchmark_runner.py -- this file just wires both into pytest so the
benchmark runs in CI alongside everything else, with no live Gemini call anywhere
(every case mocks backend.services.evaluator._evaluate_with_gemini directly).

This is a regression/quality benchmark, not a claim of certified CEFR accuracy.
The same benchmark can also be run standalone, without pytest:
    cd services/voicehr && python -m tests.benchmark_runner
"""
import pytest

from tests.benchmark_runner import REQUIRED_PASS_RATE, analyze_case, evaluate_case, run_benchmark
from tests.golden_cases import GOLDEN_CASES


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["id"] for c in GOLDEN_CASES])
@pytest.mark.asyncio
async def test_golden_case(case):
    result = await evaluate_case(case)
    analysis = analyze_case(case, result)
    assert analysis["passed"], analysis


@pytest.mark.asyncio
async def test_benchmark_runner_produces_a_deterministic_summary():
    """The runner itself (not just individual cases) is what section 1/9 of this
    task asked for: a single deterministic pass that reports expected-vs-observed,
    dimension coverage, and an explicit regression threshold."""
    summary = await run_benchmark()

    assert summary["total"] == len(GOLDEN_CASES)
    assert summary["passed"] + summary["failed"] == summary["total"]
    assert summary["pass_rate"] >= REQUIRED_PASS_RATE, summary

    # Every case's analysis must at least report on all five real rubric dimensions,
    # regardless of pass/fail -- proves dimension coverage is actually being checked,
    # not just asserted for the "happy path" cases.
    for case_result in summary["cases"]:
        assert set(case_result["dimensions_present"].keys()) == {
            "comprehension_relevance", "fluency_coherence", "grammar_language_control",
            "vocabulary_range", "interaction_spontaneity",
        }


@pytest.mark.asyncio
async def test_benchmark_is_deterministic_across_repeated_runs():
    """Same mocked inputs must always produce the same summary -- no hidden
    randomness/state leaking between cases (e.g. a module-level cache)."""
    first = await run_benchmark()
    second = await run_benchmark()

    assert first["pass_rate"] == second["pass_rate"]
    assert [c["observed_cefr"] for c in first["cases"]] == [c["observed_cefr"] for c in second["cases"]]
