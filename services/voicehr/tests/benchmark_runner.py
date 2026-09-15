"""
Deterministic benchmark runner for VoiceHR's interview evaluator
(backend/services/evaluator.py). Runs every golden case (golden_cases.py) through
the real evaluate()/normalize_result() pipeline with only the Gemini call itself
mocked out (each case supplies its own hand-written "as if this were a real model
response" JSON), and produces a structured pass/fail summary per case plus a set of
pipeline-level sanity checks: dimension coverage, missing/invalid fields, and
obvious contradictions (e.g. a "completed" status with no CEFR level).

This is a regression/quality benchmark for VoiceHR's own scoring pipeline, not a
claim that the system is a certified CEFR examination, and not a claim that these
hand-written mocked scores are what a real Gemini call would return for a similar
transcript -- see handoff.md.

Usable two ways:
  - As a pytest suite (test_interview_quality.py imports run_benchmark/analyze_case
    directly), so it runs in CI with everything else, no live Gemini involved.
  - Standalone, without pytest at all:
        cd services/voicehr && python -m tests.benchmark_runner
    Writes tests/benchmark_results/latest.{json,md} and exits non-zero if any case
    fails or the pass rate drops below REQUIRED_PASS_RATE.
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Makes `backend.*` importable regardless of how this file is invoked (as a pytest
# module, as `python -m tests.benchmark_runner`, or as a bare script) -- always
# relative to this file's own location, never the caller's working directory.
_SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from backend.services.evaluator import DIMENSION_WEIGHTS, evaluate  # noqa: E402

try:
    from tests.golden_cases import GOLDEN_CASES
except ImportError:
    from golden_cases import GOLDEN_CASES  # running as a bare script with tests/ itself on sys.path

# Every field a recruiter-facing consumer (VoiceHR's own report, RWS's webhook
# payload) may legitimately read off an evaluate() result. Anything else showing up
# would mean either a schema drift this benchmark should catch, or -- see
# DISALLOWED_FIELDS below -- a private-reasoning leak.
REQUIRED_TOP_LEVEL_FIELDS = (
    "overall_score", "cefr_level", "confidence", "evaluation_status",
    "dimensions", "strengths", "concerns", "evidence", "pronunciation_score",
    "fluency_score", "vocabulary_score", "grammar_score", "comprehension_score",
    "interaction_score", "candidate_speaking_time", "word_count",
)

# A model could, in principle, add extra keys to its JSON that nothing asked for --
# the schema constrains the *required* fields, not additional ones. If any of these
# ever showed up on the normalized result, that would mean private model reasoning
# is leaking into recruiter-facing output (see evaluator.py: normalize_result, which
# only ever copies a fixed allowlist of keys -- this list exists to catch a
# regression in that allowlist, not because any of these are expected today).
DISALLOWED_FIELDS = ("reasoning", "chain_of_thought", "internal_notes", "raw_response", "thinking")


def _find_contradictions(result: dict) -> list[str]:
    problems = []
    status = result.get("evaluation_status")
    cefr = result.get("cefr_level")
    overall = result.get("overall_score")

    if status == "completed" and (cefr is None or overall is None):
        problems.append("status is 'completed' but cefr_level/overall_score is null")
    if status == "insufficient_evidence" and (cefr is not None or overall is not None):
        problems.append("status is 'insufficient_evidence' but a real CEFR/overall_score was still reported")
    if status == "failed" and (cefr is not None or overall is not None):
        problems.append("status is 'failed' but a real CEFR/overall_score was still reported")
    if (cefr is None) != (overall is None):
        problems.append("cefr_level and overall_score disagree about whether a verdict exists")
    if result.get("pronunciation_score") is not None:
        problems.append("pronunciation_score is non-null -- must always be null (no audio evidence)")
    for field in DISALLOWED_FIELDS:
        if field in result:
            problems.append(f"result exposes a disallowed field: {field!r}")
    return problems


def analyze_case(case: dict, result: dict) -> dict:
    """Pure function: case definition + the real evaluate() output it produced -> a
    structured analysis dict. No I/O, no Gemini call -- safe to call directly from a
    test with an already-computed result."""
    dims_present = {
        name: result["dimensions"].get(name) is not None
        for name in DIMENSION_WEIGHTS
    }
    missing_fields = [field for field in REQUIRED_TOP_LEVEL_FIELDS if field not in result]
    contradictions = _find_contradictions(result)

    if not case["expect_sufficient"]:
        outcome_ok = result["evaluation_status"] == "insufficient_evidence" and result["cefr_level"] is None
    else:
        outcome_ok = (
            result["evaluation_status"] == "completed"
            and result["cefr_level"] in case["expect_cefr_in"]
        )

    has_notes = bool(result.get("strengths")) or bool(result.get("concerns"))
    passed = outcome_ok and not missing_fields and not contradictions

    return {
        "id": case["id"],
        "description": case.get("description", ""),
        "expected": (
            "insufficient_evidence" if not case["expect_sufficient"]
            else sorted(case["expect_cefr_in"])
        ),
        "observed_status": result["evaluation_status"],
        "observed_cefr": result["cefr_level"],
        "observed_overall_score": result["overall_score"],
        "dimensions_present": dims_present,
        "missing_fields": missing_fields,
        "contradictions": contradictions,
        "has_strengths_or_concerns": has_notes,
        "outcome_ok": outcome_ok,
        "passed": passed,
    }


async def evaluate_case(case: dict) -> dict:
    """Runs one golden case through the real evaluate() pipeline with Gemini itself
    mocked to return that case's hand-written response. Never touches the network."""
    with patch(
        "backend.services.evaluator._evaluate_with_gemini",
        new=AsyncMock(return_value=case["mocked_response"]),
    ):
        return await evaluate(
            case["transcript"],
            language=case.get("language", "en"),
            inconclusive=case["pre_check_inconclusive"],
            duration_seconds=case["duration_seconds"],
        )


async def run_benchmark(cases=GOLDEN_CASES) -> dict:
    analyses = []
    for case in cases:
        result = await evaluate_case(case)
        analyses.append(analyze_case(case, result))
    total = len(analyses)
    passed = sum(1 for a in analyses if a["passed"])
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "cases": analyses,
    }


def to_markdown(summary: dict) -> str:
    lines = [
        "# VoiceHR evaluator golden-case benchmark",
        "",
        "Regression/quality benchmark for the scoring pipeline -- not a certified CEFR",
        "examination. See services/voicehr/tests/golden_cases.py for case definitions.",
        "",
        f"**{summary['passed']}/{summary['total']} passed** ({summary['pass_rate']:.0%})",
        "",
        "| Case | Expected | Observed status | Observed CEFR | Passed |",
        "|---|---|---|---|---|",
    ]
    for case in summary["cases"]:
        expected = case["expected"] if isinstance(case["expected"], str) else "/".join(case["expected"])
        lines.append(
            f"| {case['id']} | {expected} | {case['observed_status']} | "
            f"{case['observed_cefr'] or '—'} | {'PASS' if case['passed'] else 'FAIL'} |"
        )
    failing = [c for c in summary["cases"] if not c["passed"]]
    if failing:
        lines.append("")
        lines.append("## Failures")
        for case in failing:
            lines.append(
                f"- **{case['id']}**: missing_fields={case['missing_fields']}, "
                f"contradictions={case['contradictions']}, outcome_ok={case['outcome_ok']}"
            )
    return "\n".join(lines) + "\n"


# Regression threshold: every golden case is fully deterministic (the mocked Gemini
# output is fixed, hand-written per case, never a real model call), so a healthy
# pipeline must pass all of them, always -- there is no meaningful "acceptable"
# partial pass rate for a fixed, known input the way there might be for a live,
# non-deterministic evaluation. A drop below 100% here means either the pipeline
# regressed or a case's own fixture needs updating -- never noise to tolerate.
REQUIRED_PASS_RATE = 1.0


def main() -> None:
    summary = asyncio.run(run_benchmark())
    out_dir = Path(__file__).resolve().parent / "benchmark_results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "latest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "latest.md").write_text(to_markdown(summary), encoding="utf-8")
    print(to_markdown(summary))
    if summary["pass_rate"] < REQUIRED_PASS_RATE:
        print(f"FAILED: pass rate {summary['pass_rate']:.0%} is below the required {REQUIRED_PASS_RATE:.0%}")
        sys.exit(1)
    print("All golden cases passed.")


if __name__ == "__main__":
    main()
