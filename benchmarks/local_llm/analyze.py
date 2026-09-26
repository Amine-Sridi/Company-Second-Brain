"""
benchmarks/local_llm/analyze.py
--------------------------------
Turns raw sweep rows into the per-task summary table and the per-task winner.

Selection rule, fixed here rather than argued after the fact: highest mean quality
wins; ties within QUALITY_TIE (measurement noise, not a real difference) go to the
lower mean latency. A task whose best model scores below MIN_USABLE_QUALITY gets no
winner at all -- "nothing here is good enough" is a legitimate, reportable outcome and
is what keeps this from rubber-stamping a bad fallback.

Run:
    python benchmarks/local_llm/analyze.py results/sweep_final.json
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

# A local fallback only has to beat "the request fails outright", but it still has to
# be worth trusting unsupervised. Below this, the honest answer is no fallback.
MIN_USABLE_QUALITY = 0.70
QUALITY_TIE = 0.05

TASKS = ["jd_structuring", "resume_parsing", "match_explanation", "interview_evaluation"]
VRAM_BUDGET_MB = 6144


def load(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        rows.extend(json.loads(p.read_text(encoding="utf-8")))
    return rows


def per_task(rows: list[dict]) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    models = sorted({r["model"] for r in rows})
    for task in TASKS:
        out[task] = {}
        for m in models:
            sel = [r for r in rows if r["model"] == m and r["task"] == task and not r.get("failed")]
            if not sel:
                continue
            out[task][m] = {
                "quality": round(st.mean(r["quality"] for r in sel), 4),
                "latency_s": round(st.mean(r["latency_s"] for r in sel), 2),
                "valid_rate": round(st.mean(1.0 if r.get("structured_valid") else 0.0 for r in sel), 3),
                "cases": len(sel),
            }
    return out


def pick_winner(scores: dict[str, dict]) -> tuple[str | None, str]:
    if not scores:
        return None, "no data"
    best_q = max(s["quality"] for s in scores.values())
    if best_q < MIN_USABLE_QUALITY:
        leader = max(scores, key=lambda m: scores[m]["quality"])
        return None, (
            f"no model reached the {MIN_USABLE_QUALITY:.2f} usability floor "
            f"(best: {leader} at {best_q:.3f}) -- no fallback for this task"
        )
    contenders = [m for m, s in scores.items() if best_q - s["quality"] <= QUALITY_TIE]
    winner = min(contenders, key=lambda m: scores[m]["latency_s"])
    if len(contenders) > 1:
        return winner, (
            f"quality {scores[winner]['quality']:.3f}; within {QUALITY_TIE} of the best "
            f"({best_q:.3f}), so chosen on latency ({scores[winner]['latency_s']}s) "
            f"among {', '.join(sorted(contenders))}"
        )
    return winner, f"highest quality {scores[winner]['quality']:.3f}, {scores[winner]['latency_s']}s"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+", type=Path)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    paths = [p if p.exists() else here / p for p in args.results]
    rows = load(paths)
    summary = per_task(rows)
    models = sorted({r["model"] for r in rows})

    print(f"\n{len(rows)} rows | {len(models)} models: {', '.join(models)}\n")
    print(f"{'task':<24}" + "".join(f"{m:>20}" for m in models))
    print("-" * (24 + 20 * len(models)))
    for task in TASKS:
        line = f"{task:<24}"
        for m in models:
            s = summary[task].get(m)
            line += f"{s['quality']:>12.3f}/{s['latency_s']:>5.1f}s" if s else f"{'--':>20}"
        print(line)

    print(f"\n{'model':<20}{'VRAM peak':>12}{'cold load':>12}{'tok/s':>10}{'valid':>9}{'fits 6GB':>10}")
    print("-" * 73)
    for m in models:
        mrows = [r for r in rows if r["model"] == m]
        vram = max((r.get("vram_peak_mb") or 0) for r in mrows)
        cold = next((r["load_time_s"] for r in mrows if r["task"] == "cold_load"), None)
        tps = [r["tokens_per_s"] for r in mrows if r.get("tokens_per_s")]
        valid = st.mean(1.0 if r.get("structured_valid") else 0.0
                        for r in mrows if "structured_valid" in r)
        print(f"{m:<20}{vram:>10.0f}MB{(f'{cold:.2f}s' if cold else '?'):>12}"
              f"{st.mean(tps):>10.1f}{valid:>9.2f}{('yes' if vram <= VRAM_BUDGET_MB else 'NO'):>10}")

    print("\n=== winner per task ===")
    winners = {}
    for task in TASKS:
        w, why = pick_winner(summary[task])
        winners[task] = w
        print(f"  {task:<24} {(w or 'NONE'):<18} {why}")

    out = Path(__file__).resolve().parent / "results" / "summary.json"
    out.write_text(json.dumps({"summary": summary, "winners": winners}, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
