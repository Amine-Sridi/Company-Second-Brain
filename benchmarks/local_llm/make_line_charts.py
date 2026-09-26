"""
benchmarks/local_llm/make_line_charts.py
-----------------------------------------
Line-chart views of the benchmark results, complementing the bar/scatter figures in
make_graphs.py.

On form: three of these connect points across *categories* (tasks, cases), which is
parallel-coordinates rather than a time series -- the line encodes "this model's profile
across the suite", and profile comparison is exactly what picking a per-task winner
needs. The fourth (quality vs VRAM) has a genuinely continuous x-axis and is a line in
the ordinary sense.

Colour: the validated categorical order, in fixed slot order, never cycled. It passes
the lightness/chroma/CVD/normal-vision gates for 7 adjacent series; three slots carry a
contrast-vs-surface WARN, which obliges visible labels or a table view -- both are
provided (direct labels here, full tables in LOCAL_LLM_BENCHMARK_AND_FALLBACK.md §6).

Run:
    python benchmarks/local_llm/make_line_charts.py results/sweep_clean.json \
        results/sweep_granite.json results/sweep_last2.json
"""
from __future__ import annotations

import argparse
import json
import statistics as st
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
    "resume_parsing": "Resume extraction",
    "match_explanation": "Match explanation",
    "interview_evaluation": "Interview evaluation",
}

# Validated categorical palette, fixed slot order (light surface). Never cycled.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"]

# Composite encoding: hue x shape. Seven crossing lines is effectively an all-pairs
# comparison, and the validator FAILS this palette under --pairs all (worst: green
# #008300 vs orange #eb6834 at protan deltaE 3.2, and magenta vs orange at normal-vision
# deltaE 12.9). Shape is the secondary encoding that makes those pairs separable
# regardless of colour vision; the legend carries both, and the shipped models are also
# direct-labelled. Without this, two of these lines are genuinely indistinguishable to a
# protanope -- which for a report defended in front of a jury is not acceptable.
MARKERS = ["o", "s", "^", "D", "v", "P", "X"]
SURFACE = "#fcfcfb"
INK = "#1a1a19"
INK_MUTED = "#6b6a63"
GRID = "#e4e3dd"

# The two models actually shipped as the fallback -- emphasised, never a different hue.
SHIPPED = {"granite4.1:3b", "llama3.2:3b"}

USABILITY_FLOOR = 0.70


def style(ax, *, ylabel: str, title: str, subtitle: str | None = None):
    ax.set_facecolor(SURFACE)
    ax.figure.patch.set_facecolor(SURFACE)
    ax.grid(True, axis="y", color=GRID, linewidth=1, alpha=0.9)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, labelsize=9, length=0)
    ax.set_ylabel(ylabel, color=INK_MUTED, fontsize=10)
    full = title if not subtitle else f"{title}\n{subtitle}"
    ax.set_title(full, color=INK, fontsize=13, fontweight="bold", pad=14)


def series_style(model: str, idx: int) -> dict:
    """Colour follows the entity (fixed slot), width carries emphasis."""
    shipped = model in SHIPPED
    return {
        "color": PALETTE[idx],
        "linewidth": 2.8 if shipped else 2.0,
        "marker": MARKERS[idx],
        "markersize": 9 if shipped else 7.5,
        "markeredgecolor": SURFACE,   # 2px surface ring on overlapping marks
        "markeredgewidth": 2,
        "alpha": 1.0 if shipped else 0.9,
        "zorder": 3 if shipped else 2,
    }


def load(paths: list[str]) -> list[dict]:
    rows: list[dict] = []
    for name in paths:
        p = Path(name)
        if not p.exists():
            p = RESULTS / name
        rows.extend(json.loads(p.read_text(encoding="utf-8")))
    return rows


def agg(rows, models):
    out = {}
    for m in models:
        for t in TASKS:
            sel = [r for r in rows if r["model"] == m and r["task"] == t and not r.get("failed")]
            if sel:
                out[(m, t)] = {
                    "quality": st.mean(r["quality"] for r in sel),
                    "latency": st.mean(r["latency_s"] for r in sel),
                }
    return out


def footprints() -> dict[str, float]:
    path = RESULTS / "vram_footprint.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {r["model"]: r["model_footprint_mb"] for r in data.get("rows", [])
            if r.get("model_footprint_mb")}


def profile_chart(models, a, key, ylabel, title, subtitle, filename, floor=None):
    fig, ax = plt.subplots(figsize=(11, 6))
    xs = range(len(TASKS))
    for i, m in enumerate(models):
        ys = [a.get((m, t), {}).get(key) for t in TASKS]
        ax.plot(list(xs), ys, label=m, **series_style(m, i))
        # Selective direct labels: only the shipped models, only at the last point.
        if m in SHIPPED and ys[-1] is not None:
            ax.annotate(m, (len(TASKS) - 1, ys[-1]), textcoords="offset points",
                        xytext=(10, 0), va="center", fontsize=9,
                        color=INK, fontweight="bold")

    if floor is not None:
        ax.axhline(floor, color=INK_MUTED, linestyle="--", linewidth=1.5, alpha=0.8, zorder=1)
        ax.annotate(f"usability floor ({floor:.2f})", (0.02, floor), xycoords=("axes fraction", "data"),
                    textcoords="offset points", xytext=(0, 6), fontsize=9, color=INK_MUTED)

    style(ax, ylabel=ylabel, title=title, subtitle=subtitle)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([TASK_LABELS[t] for t in TASKS], fontsize=10, color=INK)
    ax.set_xlim(-0.25, len(TASKS) - 0.25 + 1.1)
    ax.legend(frameon=False, fontsize=9, labelcolor=INK, ncol=4,
              loc="upper center", bbox_to_anchor=(0.5, -0.09))
    fig.tight_layout()
    fig.savefig(GRAPHS / filename, dpi=160, facecolor=SURFACE)
    plt.close(fig)


def evaluation_case_chart(rows, models):
    """Small multiples, not one 7-line chart.

    Plotted as a single axes this is spaghetti: seven lines zig-zagging between 0.15 and
    1.00, with qwen3.5:4b completely hidden underneath llama3.2:3b wherever both sit at
    0.15 (which is most of the suite). One panel per model keeps every series readable
    and makes the shared shape of the failure obvious.
    """
    cases = [r["case"] for r in rows
             if r["model"] == models[0] and r["task"] == "interview_evaluation"]
    labels = [c.replace("_", " ") for c in cases]

    ncols, nrows = 2, 4
    fig, axes = plt.subplots(nrows, ncols, figsize=(15, 13), sharex=True, sharey=True)
    fig.patch.set_facecolor(SURFACE)
    flat = axes.flatten()

    for i, m in enumerate(models):
        ax = flat[i]
        by_case = {r["case"]: r["quality"] for r in rows
                   if r["model"] == m and r["task"] == "interview_evaluation"}
        ys = [by_case.get(c) for c in cases]
        sty = series_style(m, i)
        ax.plot(range(len(cases)), ys, **sty)
        ax.axhline(USABILITY_FLOOR, color=INK_MUTED, linestyle="--", linewidth=1.3, alpha=0.8, zorder=1)

        mean_q = sum(v for v in ys if v is not None) / len(ys)
        ax.set_title(f"{m}   (mean {mean_q:.3f})", fontsize=11, color=INK,
                     fontweight="bold" if m in SHIPPED else "normal", pad=8)
        ax.set_facecolor(SURFACE)
        ax.grid(True, axis="y", color=GRID, linewidth=1, alpha=0.9)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color(GRID)
        ax.tick_params(colors=INK_MUTED, labelsize=8, length=0)
        ax.set_ylim(0, 1.08)

    for j in range(len(models), len(flat)):
        flat[j].axis("off")

    # Label the lowest *populated* panel in each column. With 7 models in a 4x2 grid the
    # last slot is empty, so the bottom of the right-hand column is not in the final row
    # and would otherwise inherit sharex's hidden ticks.
    for col in range(ncols):
        populated = [i for i in range(len(models)) if i % ncols == col]
        if not populated:
            continue
        ax = flat[max(populated)]
        ax.set_xticks(range(len(cases)))
        ax.set_xticklabels(labels, rotation=40, ha="right", fontsize=8, color=INK)
        ax.tick_params(axis="x", labelbottom=True)

    fig.suptitle(
        "Interview evaluation: quality per golden case, one panel per model\n"
        "Only 2 of 13 cases are scored correctly by every model -- and both are cases where "
        "withholding a verdict is the right answer",
        fontsize=14, fontweight="bold", color=INK, y=0.985,
    )
    fig.supylabel("Quality (0-1)", color=INK_MUTED, fontsize=11)
    fig.tight_layout(rect=(0.015, 0, 1, 0.965))
    fig.savefig(GRAPHS / "10_evaluation_quality_per_case.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


def quality_vs_vram_chart(models, a, fps):
    """Genuine continuous-x line: the quality-per-megabyte frontier."""
    pts = []
    viable = [t for t in TASKS if t != "interview_evaluation"]
    for m in models:
        vals = [a[(m, t)]["quality"] for t in viable if (m, t) in a]
        if vals and m in fps:
            pts.append((fps[m], st.mean(vals), m))
    pts.sort()

    # Connect ONLY the Pareto frontier -- the models no other model beats on *both* axes
    # (smaller and better). Joining all seven in VRAM order, as an earlier draft did,
    # draws a zig-zag that implies quality varies continuously with VRAM; it does not,
    # and the line invented a trend the data has no basis for. The frontier is a line
    # that means something: everything below it is strictly dominated.
    frontier, best = [], -1.0
    for vram, quality, name in pts:            # pts is sorted by VRAM ascending
        if quality > best:
            frontier.append((vram, quality, name))
            best = quality
    dominated = {p[2] for p in pts} - {f[2] for f in frontier}

    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot([f[0] for f in frontier], [f[1] for f in frontier],
            color=PALETTE[0], linewidth=2, alpha=0.55, zorder=1,
            drawstyle="steps-post", label="Pareto frontier (quality per MB)")
    # Single hue on purpose: every point carries its own label, so colour would encode
    # nothing, and scatter is an all-pairs form this 7-hue palette does not clear.
    # The two shipped models are emphasised by weight, not by a second hue.
    for x, y, m in pts:
        shipped = m in SHIPPED
        ax.plot([x], [y], marker="o", markersize=12 if shipped else 9,
                color=PALETTE[0] if shipped else "#9a9990",
                markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
        ax.annotate(m, (x, y), textcoords="offset points", xytext=(0, 14),
                    ha="center", fontsize=9,
                    color=INK if shipped else INK_MUTED,
                    fontweight="bold" if shipped else "normal")

    style(ax, ylabel="Mean quality across the 3 viable tasks",
          title="Quality per megabyte of VRAM",
          subtitle=f"The line is the efficiency frontier -- the {len(dominated)} models below it\n"
                   "are beaten on both size and quality at once")
    ax.legend(frameon=False, fontsize=9, labelcolor=INK, loc="lower right")
    ax.set_xlabel("Model VRAM footprint (MB, measured idle-to-loaded)", color=INK_MUTED, fontsize=10)
    ax.margins(x=0.12, y=0.18)
    fig.tight_layout()
    fig.savefig(GRAPHS / "11_quality_vs_vram_line.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+")
    args = ap.parse_args()

    GRAPHS.mkdir(parents=True, exist_ok=True)
    rows = load(args.results)
    models = sorted({r["model"] for r in rows})
    if len(models) > 7:
        raise SystemExit(
            f"{len(models)} series exceeds the 7-slot categorical ceiling; facet instead "
            "of generating an 8th hue."
        )
    a = agg(rows, models)

    profile_chart(models, a, "quality", "Quality (0-1)",
                  "Model quality profile across the four tasks",
                  "Each line is one model; no single model leads everywhere",
                  "8_quality_profile_by_task.png", floor=USABILITY_FLOOR)
    profile_chart(models, a, "latency", "Mean latency (seconds)",
                  "Model latency profile across the four tasks",
                  "mistral:7b, the over-budget control, separates clearly from the 3-4B field",
                  "9_latency_profile_by_task.png")
    evaluation_case_chart(rows, models)
    quality_vs_vram_chart(models, a, footprints())

    print(f"Wrote 4 line charts to {GRAPHS}")
    for f in ("8_quality_profile_by_task.png", "9_latency_profile_by_task.png",
              "10_evaluation_quality_per_case.png", "11_quality_vs_vram_line.png"):
        print(f"  {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
