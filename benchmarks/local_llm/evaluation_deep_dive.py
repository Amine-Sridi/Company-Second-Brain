"""
benchmarks/local_llm/evaluation_deep_dive.py
---------------------------------------------
Why local models fail VoiceHR's interview evaluation, and whether that is fixable.

Milestone 46 benchmarked seven local models on this task and none cleared the 0.70
usability floor (best: mistral:7b at 0.600), so the conclusion was "no local fallback for
interview evaluation". But the *failure mode* was specific and is recorded in handoff.md:
the models produced the dimension scores perfectly well and then set
`sufficient_evidence: false`, which makes `normalize_result` withhold the CEFR verdict
entirely. Nearly every case scored 0.15 -- dimensions present, verdict withheld.

That is not obviously a capability problem. Sufficiency in this system already has two
halves (processing.py):

    pre_check_inconclusive   deterministic: terminated, or < 300s, or < 3 candidate turns
    evaluation_status        the model's own read of whether the content supports a verdict

Local models are unreliable on the second half. This script measures whether they are
reliable on the part that actually needs a language model -- the per-dimension scoring --
by grading two ways:

    AS-IS       exactly as milestone 46 did: the model's sufficiency veto is honoured.
    SCORES-ONLY the veto is ignored; the CEFR is derived from the model's dimension
                scores using VoiceHR's OWN weighted_overall + cefr_level_for_score, and
                compared against the golden expectation.

If SCORES-ONLY is markedly better, the fix is architectural rather than a bigger model:
stop asking a small model for a judgment call and keep it for the measurement. That would
also match this project's existing principle that the LLM never decides thresholds or
qualification -- sufficiency is arguably the same kind of decision.

Run:
    python benchmarks/local_llm/evaluation_deep_dive.py --models phi4-mini:3.8b mistral:7b
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import tasks  # noqa: E402
from shared.llm import ollama_compatible_schema  # noqa: E402

OLLAMA = "http://localhost:11434"
RESULTS = HERE / "results"

# Exact-band agreement is the wrong single yardstick for a fallback. The golden set
# names one band per case out of six, and cefr_level_for_score's buckets are 15 points
# wide -- a model that is three points off the boundary is graded identically to one
# that is two whole levels out. Trained human raters do not agree to the band either;
# adjacency is the normal unit of agreement on CEFR. So both are reported: exact, and
# within-one-band. Neither is charity -- a fallback that is reliably +/-1 is a usable
# degraded result, a fallback that is bidirectionally 2+ bands out is noise.
CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]


def band_distance(got: str | None, expected: set[str]) -> int | None:
    """Levels between the derived band and the nearest acceptable one. None if either
    side has no band at all (those cases are graded by the withheld/issued check)."""
    if got is None or not expected:
        return None
    if got not in CEFR_ORDER:
        return None
    gi = CEFR_ORDER.index(got)
    return min(abs(gi - CEFR_ORDER.index(e)) for e in expected if e in CEFR_ORDER)


def _voicehr():
    """VoiceHR's own scoring helpers -- the derived CEFR must use the same maths the
    product uses, not a reimplementation."""
    sys.path.insert(0, str(HERE.parents[1] / "services" / "voicehr"))
    from backend.services.evaluator import (  # noqa: E402
        DIMENSION_WEIGHTS,
        _get_dimension_score,
        cefr_level_for_score,
        weighted_overall,
    )

    return DIMENSION_WEIGHTS, weighted_overall, cefr_level_for_score, _get_dimension_score


def generate(model: str, prompt: str, schema: dict, timeout: int = 300) -> tuple[dict | None, float]:
    started = time.perf_counter()
    resp = requests.post(
        f"{OLLAMA}/api/generate",
        json={
            "model": model, "prompt": prompt, "stream": False, "think": False,
            "format": ollama_compatible_schema(schema),
            "options": {"temperature": 0.2},
        },
        timeout=timeout,
    )
    elapsed = time.perf_counter() - started
    if resp.status_code != 200:
        return None, elapsed
    body = resp.json()
    text = (body.get("response") or "").strip() or (body.get("thinking") or "").strip()
    try:
        return json.loads(text), elapsed
    except (ValueError, TypeError):
        return None, elapsed


def run_model(model: str, cases: list[dict], schema: dict) -> list[dict]:
    DIMENSION_WEIGHTS, weighted_overall, cefr_level_for_score, get_dim = _voicehr()
    rows = []
    for case in cases:
        prompt = tasks.evaluation_prompt(
            case["transcript"], case.get("language", "en"), case["pre_check_inconclusive"]
        )
        raw, elapsed = generate(model, prompt, schema)
        if raw is None:
            rows.append({"model": model, "case": case["id"], "failed": True})
            continue

        # AS-IS: exactly milestone 46's grading, veto honoured.
        as_is = tasks.grade_evaluation(raw, case)

        # SCORES-ONLY: ignore the model's sufficiency veto and derive the CEFR from its
        # dimension scores with VoiceHR's own maths. The deterministic pre-check is still
        # honoured -- that half is not the model's to decide and is genuinely reliable.
        # Use VoiceHR's own accessor, not a reimplementation: the model may return a
        # dimension under any of several aliases (DIMENSION_ALIASES), and hand-rolling
        # the lookup is exactly how the first run of this script reported
        # "dims present 0.00" for every model and produced a meaningless comparison.
        nested = raw.get("dimensions") if isinstance(raw.get("dimensions"), dict) else {}
        dims = {name: get_dim(raw, nested, name) for name in DIMENSION_WEIGHTS}

        derived_overall = weighted_overall(dims)
        derived_cefr = (
            None if case["pre_check_inconclusive"] or derived_overall is None
            else cefr_level_for_score(derived_overall)
        )
        if case["expect_sufficient"]:
            scores_only_ok = derived_cefr in case["expect_cefr_in"]
        else:
            scores_only_ok = derived_cefr is None

        distance = band_distance(derived_cefr, set(case["expect_cefr_in"]))
        # Signed, so a model that is consistently one band generous can be told apart
        # from one that is simply unstable -- the first is correctable by moving a
        # threshold, the second is not usable at all.
        signed = None
        if derived_cefr in CEFR_ORDER and case["expect_cefr_in"]:
            gi = CEFR_ORDER.index(derived_cefr)
            nearest = min((abs(gi - CEFR_ORDER.index(e)), gi - CEFR_ORDER.index(e))
                          for e in case["expect_cefr_in"] if e in CEFR_ORDER)
            signed = nearest[1]

        rows.append({
            "model": model,
            "case": case["id"],
            "failed": False,
            "latency_s": round(elapsed, 2),
            "expect_sufficient": case["expect_sufficient"],
            "expect_cefr_in": sorted(case["expect_cefr_in"]),
            # what the model itself claimed
            "model_said_sufficient": raw.get("sufficient_evidence"),
            "as_is_quality": as_is["quality"],
            "as_is_cefr": as_is["cefr_level"],
            # what its numbers imply
            "dimensions": dims,
            "all_dims_present": all(v is not None for v in dims.values()),
            "derived_overall": derived_overall,
            "derived_cefr": derived_cefr,
            "scores_only_ok": scores_only_ok,
            "band_distance": distance,
            "band_error_signed": signed,
        })
        print(f"  {model:<20}{case['id']:<30} as-is={as_is['quality']:<6} "
              f"derived={str(derived_cefr):<6} want={sorted(case['expect_cefr_in']) or '(none)'} "
              f"{'OK' if scores_only_ok else '--'}", flush=True)
    return rows


def report(rows: list[dict]) -> None:
    models = sorted({r["model"] for r in rows})
    print(f"\n{'model':<22}{'AS-IS':>8}{'EXACT':>8}{'WITHIN-1':>10}{'bias':>8}"
          f"{'spread':>8}{'over-veto':>11}")
    print("-" * 75)
    for m in models:
        sel = [r for r in rows if r["model"] == m and not r.get("failed")]
        if not sel:
            continue
        as_is = statistics.mean(r["as_is_quality"] for r in sel)
        graded = [r for r in sel if r.get("band_distance") is not None]
        exact = statistics.mean(1.0 if r["band_distance"] == 0 else 0.0 for r in graded) if graded else 0.0
        near = statistics.mean(1.0 if r["band_distance"] <= 1 else 0.0 for r in graded) if graded else 0.0
        signed = [r["band_error_signed"] for r in sel if r.get("band_error_signed") is not None]
        bias = statistics.mean(signed) if signed else 0.0
        # Spread of the derived overall scores: a model that hands back 83-90 for six
        # very different transcripts is not assessing, whatever its band accuracy.
        overalls = [r["derived_overall"] for r in sel if r.get("derived_overall") is not None]
        spread = (max(overalls) - min(overalls)) if len(overalls) > 1 else 0
        should = [r for r in sel if r["expect_sufficient"]]
        over = sum(1 for r in should if r["model_said_sufficient"] is False)
        print(f"{m:<22}{as_is:>8.3f}{exact:>8.2f}{near:>10.2f}{bias:>+8.2f}"
              f"{spread:>8}{f'{over}/{len(should)}':>11}")

    print("\n  AS-IS     = milestone 46's grading: the model's own sufficiency veto honoured")
    print("  EXACT     = derived CEFR lands in the golden band")
    print("  WITHIN-1  = derived CEFR is that band or an adjacent one (rater-level agreement)")
    print("  bias      = mean signed band error; consistent sign is correctable, mixed is not")
    print("  spread    = max-min derived overall; near-zero means it is not discriminating")
    print("  over-veto = scoreable cases the model wrongly declared insufficient")

    # Per-case detail, because a mean hides which end of the scale a model fails at.
    for m in models:
        sel = [r for r in rows if r["model"] == m and not r.get("failed")
               and r.get("band_distance") is not None]
        if not sel:
            continue
        print(f"\n  {m} per case:")
        for r in sorted(sel, key=lambda x: x["case"]):
            mark = "ok" if r["band_distance"] == 0 else ("~" if r["band_distance"] == 1 else "XX")
            print(f"    {mark:<3}{r['case']:<32}got {str(r['derived_cefr']):<3}"
                  f"({r['derived_overall']:>3})  want {'/'.join(r['expect_cefr_in'])}")

    out = RESULTS / f"evaluation_deep_dive_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\n  wrote {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    args = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    cases = tasks.evaluation_cases()
    schema = tasks.evaluation_schema()
    print(f"\n{len(cases)} golden cases x {len(args.models)} models\n")

    rows = []
    for model in args.models:
        rows.extend(run_model(model, cases, schema))
    report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
