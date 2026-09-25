"""
benchmarks/local_llm/sweep.py
------------------------------
Benchmarks a list of candidate models one at a time, pulling each before it runs and
optionally evicting it afterwards.

Why not just pull everything first: this machine has ~7 GB of free disk and the
candidate set is ~20 GB. Sweeping pull -> benchmark -> evict lets the candidate space
be as wide as the hardware genuinely allows rather than as wide as the disk holds at
one moment. Results are appended after every model, so a network failure part-way
through costs one model, not the whole run.

Run:
    python benchmarks/local_llm/sweep.py --models llama3.2:3b granite4.1:3b --keep qwen3.5:4b
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_benchmark as rb  # noqa: E402

RESULTS = HERE / "results"
OLLAMA = "http://localhost:11434"


def installed() -> set[str]:
    try:
        r = requests.get(f"{OLLAMA}/api/tags", timeout=15)
        r.raise_for_status()
        return {m["name"] for m in r.json().get("models", [])}
    except Exception:
        return set()


def pull(model: str) -> bool:
    print(f"--- pulling {model} ...", flush=True)
    proc = subprocess.run(["ollama", "pull", model], capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        print(f"    PULL FAILED: {(proc.stderr or proc.stdout)[-300:]}", flush=True)
        return False
    return True


def remove(model: str) -> None:
    print(f"--- evicting {model} to free disk", flush=True)
    subprocess.run(["ollama", "rm", model], capture_output=True, text=True, timeout=300)


def free_gb() -> float:
    import shutil

    return shutil.disk_usage("C:/").free / 1e9


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--keep", nargs="*", default=[], help="models to leave installed afterwards")
    ap.add_argument("--latency-reps", type=int, default=3)
    ap.add_argument("--tag", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    args = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS / f"sweep_{args.tag}.json"
    all_rows: list[dict] = []
    status: dict[str, str] = {}

    for model in args.models:
        already = model in installed()
        if not already:
            if free_gb() < 6.0:
                print(f"!!! only {free_gb():.1f} GB free before {model}; evicting non-kept models")
                for m in list(installed()):
                    if m not in args.keep and m not in args.models[args.models.index(model):]:
                        remove(m)
            if not pull(model):
                status[model] = "pull_failed"
                continue

        try:
            rows = rb.benchmark_model(model, args.latency_reps)
            all_rows.extend(rows)
            status[model] = "ok"
        except Exception as exc:  # a model that cannot run is a real result, not a crash
            print(f"!!! {model} failed to benchmark: {type(exc).__name__}: {exc}", flush=True)
            status[model] = f"benchmark_failed: {type(exc).__name__}"

        out_path.write_text(json.dumps(all_rows, indent=2), encoding="utf-8")
        print(f"    (saved {len(all_rows)} rows -> {out_path.name}; {free_gb():.1f} GB free)", flush=True)

        if not already and model not in args.keep:
            remove(model)
            time.sleep(2)

    (RESULTS / f"sweep_status_{args.tag}.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    print("\n=== sweep status ===")
    for m, s in status.items():
        print(f"  {m:<26} {s}")
    print(f"\nRows: {len(all_rows)} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
