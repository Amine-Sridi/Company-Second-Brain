"""
benchmarks/local_llm/measure_vram.py
-------------------------------------
Measures each model's own VRAM footprint, separately from the main sweep.

Why this is its own pass: nvidia-smi reports whole-GPU memory, and on Windows (WDDM)
per-process VRAM is reported as [N/A], so the only honest per-model figure is a delta
against a baseline taken while no model is resident. Sampling that baseline inline in
the sweep proved unreliable -- `unload()` returns before the driver has actually
released the allocation, so consecutive baselines drifted between 2606 MB and 4455 MB
and the deltas computed from them were meaningless.

This pass instead *waits for the GPU to settle* before each measurement: it polls until
the reading stops falling, so the baseline is taken against a genuinely idle GPU.

Run:
    python benchmarks/local_llm/measure_vram.py --models qwen2.5:3b phi4-mini:3.8b
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

OLLAMA = "http://localhost:11434"
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def vram_mb() -> float | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0:
            return float(out.stdout.strip().splitlines()[0])
    except Exception:
        pass
    return None


def unload_all(models: list[str]) -> None:
    for m in models:
        try:
            requests.post(f"{OLLAMA}/api/generate", json={"model": m, "keep_alive": 0}, timeout=60)
        except Exception:
            pass


def settle(max_wait_s: float = 90.0, stable_for: int = 3) -> float | None:
    """Polls until the VRAM reading stops decreasing, so a baseline is taken against a
    GPU that has actually released the previous model rather than one mid-teardown."""
    last, stable, waited = None, 0, 0.0
    while waited < max_wait_s:
        cur = vram_mb()
        if cur is None:
            return None
        if last is not None and cur >= last - 1:
            stable += 1
            if stable >= stable_for:
                return cur
        else:
            stable = 0
        last = cur
        time.sleep(2)
        waited += 2
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    args = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    rows = []

    for model in args.models:
        unload_all(args.models)
        baseline = settle()
        print(f"\n{model}\n  idle baseline: {baseline} MB", flush=True)

        t0 = time.perf_counter()
        r = requests.post(f"{OLLAMA}/api/generate", json={
            "model": model, "prompt": "Reply with the single word: ready.",
            "stream": False, "think": False,
            "options": {"temperature": 0.0, "num_predict": 8},
        }, timeout=600)
        load_s = time.perf_counter() - t0
        ok = r.status_code == 200
        loaded = vram_mb()
        # A second reading after a brief pause catches KV-cache growth after the first token.
        time.sleep(3)
        loaded = max(loaded or 0, vram_mb() or 0)

        footprint = round(loaded - baseline, 1) if (loaded and baseline is not None) else None
        print(f"  loaded total:  {loaded} MB"
              f"\n  model footprint: {footprint} MB"
              f"\n  cold load: {load_s:.2f}s  ok={ok}", flush=True)

        rows.append({
            "model": model,
            "idle_baseline_mb": baseline,
            "loaded_total_mb": loaded,
            "model_footprint_mb": footprint,
            "cold_load_s": round(load_s, 2),
            "ok": ok,
        })
        unload_all([model])

    out = RESULTS / "vram_footprint.json"
    # Merge with any previously-measured models rather than replacing them, so measuring
    # one newly-downloaded model does not discard the rest of the comparison.
    if out.exists():
        previous = json.loads(out.read_text(encoding="utf-8")).get("rows", [])
        measured = {r["model"] for r in rows}
        rows = [r for r in previous if r["model"] not in measured] + rows
        rows.sort(key=lambda r: r["model"])
    out.write_text(json.dumps({
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "note": "footprint = loaded_total - idle_baseline, with the GPU polled to a "
                "settled idle state before each baseline (see module docstring)",
        "rows": rows,
    }, indent=2), encoding="utf-8")

    print(f"\n{'model':<20}{'footprint':>12}{'cold load':>12}")
    print("-" * 44)
    for r in rows:
        print(f"{r['model']:<20}{(str(r['model_footprint_mb']) + ' MB'):>12}{(str(r['cold_load_s']) + 's'):>12}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
