"""
benchmarks/local_llm/make_graphs.py
------------------------------------
Turns the measured results in results/raw_*.json into the report figures in graphs/.

Run:
    python benchmarks/local_llm/make_graphs.py            # newest run
    python benchmarks/local_llm/make_graphs.py --tag ...  # a specific run

Figures produced:
  1_quality_by_task.png        per-task mean quality, grouped by model
  2_latency_by_task.png        median latency per task, grouped by model
  3_memory_footprint.png       peak VRAM and system RAM per model
  4_structured_output.png      structured-output validity and failure rate
  5_quality_vs_latency.png     the actual selection trade-off
  6_quality_vs_memory.png      quality against VRAM footprint
  7_throughput.png             tokens/sec per model
"""
from __future__ import annotations

import argparse
import pathlib
import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
GRAPHS = HERE / "graphs"

TASKS = ["jd_structuring", "resume_parsing", "match_explanation", "interview_evaluation"]
TASK_LABELS = {
    "jd_structuring": "JD structuring",
    "resume_parsing": "Resume parsing",
    "match_explanation": "Match explanation",
    "interview_evaluation": "Interview evaluation",
}
# Colour-blind-safe qualitative palette.
PALETTE = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00"]


def _style(ax, title, xlabel="", ylabel=""):
    ax.set_title(title, fontsize=12, fontweight="600", pad=12)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=10)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=10)
    ax.grid(axis="y", alpha=0.25, linestyle="--", linewidth=0.7)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)


def load(tag: str | None, explicit: list[str] | None = None) -> tuple[list[dict], str]:
    """Loads result rows from explicitly-named files, or the newest sweep otherwise."""
    if explicit:
        rows, names = [], []
        for name in explicit:
            path = pathlib.Path(name)
            if not path.exists():
                path = RESULTS / name
            rows.extend(json.loads(path.read_text(encoding="utf-8")))
            names.append(path.stem)
        return rows, "+".join(names)

    files = sorted(RESULTS.glob("sweep_*.json")) or sorted(RESULTS.glob("raw_*.json"))
    files = [f for f in files if "status" not in f.name]
    if not files:
        raise SystemExit("No results found -- run sweep.py first.")
    path = next((f for f in files if tag and tag in f.name), files[-1])
    return json.loads(path.read_text(encoding="utf-8")), path.stem


def vram_footprints() -> dict[str, float]:
    """Per-model VRAM from the dedicated measure_vram.py pass.

    The sweep's own inline `vram_model_mb` is deliberately NOT used: `unload()` returns
    before the driver has released the previous model, so the baseline it subtracts is
    sometimes still inflated by the model before it, which yields negative footprints.
    measure_vram.py polls the GPU to a settled idle state first.
    """
    path = RESULTS / "vram_footprint.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {r["model"]: r["model_footprint_mb"] for r in data.get("rows", [])
            if r.get("model_footprint_mb")}


def agg(rows, models):
    """mean quality / median latency / rates per (model, task)."""
    out: dict[tuple[str, str], dict] = {}
    for m in models:
        for t in TASKS:
            sel = [r for r in rows if r["model"] == m and r["task"] == t]
            if not sel:
                continue
            lat = [r["latency_s"] for r in sel if r.get("latency_s") is not None]
            out[(m, t)] = {
                "quality": statistics.mean([r.get("quality") or 0.0 for r in sel]),
                "latency": statistics.median(lat) if lat else 0.0,
                "valid_rate": sum(bool(r.get("structured_valid")) for r in sel) / len(sel),
                "fail_rate": sum(bool(r.get("failed")) for r in sel) / len(sel),
                "n": len(sel),
            }
    return out


def grouped_bars(ax, models, categories, values_fn, ylabel, title, fmt="{:.2f}"):
    width = 0.8 / max(len(models), 1)
    xs = range(len(categories))
    for i, m in enumerate(models):
        vals = [values_fn(m, c) for c in categories]
        pos = [x - 0.4 + width * (i + 0.5) for x in xs]
        bars = ax.bar(pos, vals, width=width, label=m, color=PALETTE[i % len(PALETTE)],
                      edgecolor="white", linewidth=0.6)
        for b, v in zip(bars, vals):
            if v:
                ax.text(b.get_x() + b.get_width() / 2, v, fmt.format(v), ha="center",
                        va="bottom", fontsize=7.5)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([TASK_LABELS.get(c, c) for c in categories], fontsize=9)
    _style(ax, title, ylabel=ylabel)
    ax.legend(frameon=False, fontsize=9)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=None)
    ap.add_argument("results", nargs="*", help="result JSON files (default: newest sweep)")
    args = ap.parse_args()

    rows, tag = load(args.tag, args.results)
    GRAPHS.mkdir(parents=True, exist_ok=True)
    models = sorted({r["model"] for r in rows})
    a = agg(rows, models)
    footprints = vram_footprints()

    # 1 -- quality by task
    fig, ax = plt.subplots(figsize=(10, 5.5))
    grouped_bars(ax, models, TASKS, lambda m, t: a.get((m, t), {}).get("quality", 0.0),
                 "Mean quality (0-1)", "Task quality by model\n(graded by this project's own validators)")
    ax.set_ylim(0, 1.15)
    fig.tight_layout(); fig.savefig(GRAPHS / "1_quality_by_task.png", dpi=160); plt.close(fig)

    # 2 -- latency by task
    fig, ax = plt.subplots(figsize=(10, 5.5))
    grouped_bars(ax, models, TASKS, lambda m, t: a.get((m, t), {}).get("latency", 0.0),
                 "Median latency (s)", "Median latency by task", fmt="{:.1f}")
    fig.tight_layout(); fig.savefig(GRAPHS / "2_latency_by_task.png", dpi=160); plt.close(fig)

    # 3 -- memory footprint
    fig, ax = plt.subplots(figsize=(9, 5.5))
    vram, ram = [], []
    for m in models:
        sel = [r for r in rows if r["model"] == m]
        vram.append(footprints.get(m) or max(
            [r["vram_peak_mb"] for r in sel if r.get("vram_peak_mb")] or [0]))
        ram.append(max([r["ram_used_mb"] for r in sel if r.get("ram_used_mb")] or [0]))
    xs = range(len(models))
    ax.bar([x - 0.2 for x in xs], vram, width=0.4, label="Model VRAM (MB, measured idle-to-loaded)", color=PALETTE[0], edgecolor="white")
    ax.bar([x + 0.2 for x in xs], ram, width=0.4, label="System RAM in use (MB)", color=PALETTE[1], edgecolor="white")
    for x, v in zip(xs, vram):
        ax.text(x - 0.2, v, f"{v:.0f}", ha="center", va="bottom", fontsize=8)
    for x, v in zip(xs, ram):
        ax.text(x + 0.2, v, f"{v:.0f}", ha="center", va="bottom", fontsize=8)
    ax.axhline(6144, color="#D55E00", linestyle="--", linewidth=1.2)
    ax.text(len(models) - 0.5, 6144, " RTX 3060 Laptop limit (6144 MB)", color="#D55E00",
            fontsize=8, va="bottom", ha="right")
    ax.set_xticks(list(xs))
    # Model tags are long; past ~5 of them the labels collide, so angle them.
    ax.set_xticklabels(models, fontsize=9,
                       rotation=20 if len(models) > 5 else 0,
                       ha="right" if len(models) > 5 else "center")
    _style(ax, "Memory footprint per model", ylabel="MB")
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout(); fig.savefig(GRAPHS / "3_memory_footprint.png", dpi=160); plt.close(fig)

    # 4 -- structured output validity + failure rate
    fig, ax = plt.subplots(figsize=(10, 5.5))
    structured_tasks = ["jd_structuring", "resume_parsing", "interview_evaluation"]
    grouped_bars(ax, models, structured_tasks,
                 lambda m, t: a.get((m, t), {}).get("valid_rate", 0.0) * 100,
                 "Schema-valid outputs (%)",
                 "Structured-output validity\n(Ollama constrains decoding to the same JSON Schema Gemini receives)",
                 fmt="{:.0f}")
    ax.set_ylim(0, 115)
    fig.tight_layout(); fig.savefig(GRAPHS / "4_structured_output.png", dpi=160); plt.close(fig)

    # 5 -- quality vs latency
    fig, ax = plt.subplots(figsize=(9, 6))
    markers = ["o", "s", "^", "D", "v", "P"]
    for ti, t in enumerate(TASKS):
        for mi, m in enumerate(models):
            d = a.get((m, t))
            if not d:
                continue
            ax.scatter(d["latency"], d["quality"], s=130, color=PALETTE[mi % len(PALETTE)],
                       marker=markers[ti % len(markers)], edgecolor="white", linewidth=1.1,
                       label=m if ti == 0 else None, zorder=3)
    for ti, t in enumerate(TASKS):
        ax.scatter([], [], marker=markers[ti % len(markers)], color="#555",
                   label=TASK_LABELS[t], s=90)
    _style(ax, "Quality vs latency\n(up and to the left is better)",
           xlabel="Median latency (s)", ylabel="Mean quality (0-1)")
    ax.set_ylim(-0.05, 1.12)
    ax.legend(frameon=False, fontsize=8, ncol=2)
    fig.tight_layout(); fig.savefig(GRAPHS / "5_quality_vs_latency.png", dpi=160); plt.close(fig)

    # 6 -- quality vs memory
    fig, ax = plt.subplots(figsize=(9, 6))
    for mi, m in enumerate(models):
        sel = [r for r in rows if r["model"] == m]
        v = footprints.get(m) or max(
            [r["vram_peak_mb"] for r in sel if r.get("vram_peak_mb")] or [0])
        q = statistics.mean([a[(m, t)]["quality"] for t in TASKS if (m, t) in a] or [0])
        ax.scatter(v, q, s=220, color=PALETTE[mi % len(PALETTE)], edgecolor="white",
                   linewidth=1.3, zorder=3)
        ax.annotate(m, (v, q), textcoords="offset points", xytext=(0, 14),
                    ha="center", fontsize=9)
    ax.axvline(6144, color="#D55E00", linestyle="--", linewidth=1.2)
    ax.text(6144, ax.get_ylim()[0], " 6 GB VRAM limit", color="#D55E00", fontsize=8, va="bottom")
    _style(ax, "Quality vs memory footprint\n(mean quality across all four tasks)",
           xlabel="Model VRAM (MB, measured idle-to-loaded)", ylabel="Mean quality (0-1)")
    fig.tight_layout(); fig.savefig(GRAPHS / "6_quality_vs_memory.png", dpi=160); plt.close(fig)

    # 7 -- throughput
    fig, ax = plt.subplots(figsize=(9, 5))
    tps = []
    for m in models:
        vals = [r["tokens_per_s"] for r in rows if r["model"] == m and r.get("tokens_per_s")]
        tps.append(statistics.mean(vals) if vals else 0.0)
    bars = ax.bar(models, tps, color=[PALETTE[i % len(PALETTE)] for i in range(len(models))],
                  edgecolor="white", width=0.55)
    for b, v in zip(bars, tps):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.0f}", ha="center", va="bottom", fontsize=9)
    _style(ax, "Generation throughput", ylabel="tokens / second")
    fig.tight_layout(); fig.savefig(GRAPHS / "7_throughput.png", dpi=160); plt.close(fig)

    print(f"Wrote 7 figures to {GRAPHS} (run tag: {tag})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
