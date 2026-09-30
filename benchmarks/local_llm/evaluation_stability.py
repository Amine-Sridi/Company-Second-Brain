"""
benchmarks/local_llm/evaluation_stability.py
---------------------------------------------
Does the candidate fallback model give the *same* answer twice?

evaluation_deep_dive.py measures accuracy from a single run per case. That is not
enough to qualify a fallback. Two failure modes it cannot see:

  band instability   the same transcript scored B1 / B2 / B1 / C1 across re-runs.
                     A mean of "0.40 exact" built from a model that is really just
                     sampling a distribution is not an accuracy figure at all, and
                     two candidates with identical transcripts could be told
                     different things.
  veto instability   sufficient_evidence flipping between runs, which decides
                     whether a CEFR reaches RWS at all or the application goes to
                     needs_review. This one is visible to a recruiter as pure noise.

Both matter more for a fallback than for the primary: the fallback runs unattended,
during a Gemini outage, with nobody comparing its output to anything.

Note on temperature: the deep dive and the real evaluator both use a low but non-zero
temperature, so this measures the variance the product would actually ship, not an
artificial worst case.

Run:
    python benchmarks/local_llm/evaluation_stability.py --models gemma4:e2b-it-qat --repeats 5
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import tasks  # noqa: E402
import evaluation_deep_dive as dd  # noqa: E402

RESULTS = HERE / "results"

# The five CEFR-ladder cases. The insufficient-evidence cases are excluded on purpose:
# their expected outcome is "no band", so they cannot show band instability, and the
# deep dive already covers them.
LADDER = {
    "weak_language_sample", "b1_functional_sample", "b2_independent_sample",
    "c1_advanced_sample", "c2_exceptional_sample",
}


def run(model: str, cases: list[dict], schema: dict, repeats: int) -> list[dict]:
    DIMENSION_WEIGHTS, weighted_overall, cefr_level_for_score, get_dim = dd._voicehr()
    rows = []
    for case in cases:
        prompt = tasks.evaluation_prompt(
            case["transcript"], case.get("language", "en"), case["pre_check_inconclusive"]
        )
        for run_idx in range(repeats):
            raw, elapsed = dd.generate(model, prompt, schema)
            if raw is None:
                rows.append({"model": model, "case": case["id"], "run": run_idx, "failed": True})
                print(f"  {model:<20}{case['id']:<28}run {run_idx}  FAILED", flush=True)
                continue
            nested = raw.get("dimensions") if isinstance(raw.get("dimensions"), dict) else {}
            dims = {n: get_dim(raw, nested, n) for n in DIMENSION_WEIGHTS}
            overall = weighted_overall(dims)
            cefr = None if overall is None else cefr_level_for_score(overall)
            rows.append({
                "model": model, "case": case["id"], "run": run_idx, "failed": False,
                "latency_s": round(elapsed, 2),
                "said_sufficient": raw.get("sufficient_evidence"),
                "overall": overall, "cefr": cefr,
                "expect_cefr_in": sorted(case["expect_cefr_in"]),
            })
            print(f"  {model:<20}{case['id']:<28}run {run_idx}  {str(cefr):<4}"
                  f"({str(overall):>3})  suff={raw.get('sufficient_evidence')}", flush=True)
    return rows


def report(rows: list[dict], repeats: int) -> None:
    for model in sorted({r["model"] for r in rows}):
        sel = [r for r in rows if r["model"] == model and not r.get("failed")]
        if not sel:
            continue
        print(f"\n{model}")
        print(f"  {'case':<28}{'bands seen':<22}{'overall spread':>15}{'veto flips':>12}")
        print("  " + "-" * 75)
        band_unstable = veto_unstable = 0
        for case in sorted({r["case"] for r in sel}):
            runs = [r for r in sel if r["case"] == case]
            bands = Counter(str(r["cefr"]) for r in runs)
            overalls = [r["overall"] for r in runs if r["overall"] is not None]
            spread = (max(overalls) - min(overalls)) if len(overalls) > 1 else 0
            vetoes = {r["said_sufficient"] for r in runs}
            if len(bands) > 1:
                band_unstable += 1
            if len(vetoes) > 1:
                veto_unstable += 1
            shown = " ".join(f"{b}x{n}" for b, n in bands.most_common())
            print(f"  {case:<28}{shown:<22}{spread:>15}{'YES' if len(vetoes) > 1 else '-':>12}")

        n_cases = len({r["case"] for r in sel})
        spreads = []
        for case in {r["case"] for r in sel}:
            o = [r["overall"] for r in sel if r["case"] == case and r["overall"] is not None]
            if len(o) > 1:
                spreads.append(max(o) - min(o))
        print(f"\n  cases whose CEFR band changed across {repeats} runs : {band_unstable}/{n_cases}")
        print(f"  cases whose sufficiency verdict flipped          : {veto_unstable}/{n_cases}")
        if spreads:
            print(f"  median within-case spread of overall score       : {statistics.median(spreads):.0f} points")
        lat = [r["latency_s"] for r in sel]
        print(f"  median latency                                   : {statistics.median(lat):.1f}s")

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"evaluation_stability_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\n  wrote {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--repeats", type=int, default=5)
    args = ap.parse_args()

    # Puts services/voicehr on sys.path, which is what makes the golden cases
    # importable -- tasks.evaluation_cases() reads them from VoiceHR's own test suite
    # rather than keeping a second copy here.
    dd._voicehr()

    cases = [c for c in tasks.evaluation_cases() if c["id"] in LADDER]
    schema = tasks.evaluation_schema()
    print(f"\n{len(cases)} ladder cases x {args.repeats} repeats x {len(args.models)} models "
          f"= {len(cases) * args.repeats * len(args.models)} calls\n")

    rows = []
    for model in args.models:
        rows.extend(run(model, cases, schema, args.repeats))
    report(rows, args.repeats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
