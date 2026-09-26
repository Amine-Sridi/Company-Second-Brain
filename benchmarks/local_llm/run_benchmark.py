"""
benchmarks/local_llm/run_benchmark.py
--------------------------------------
Measures candidate local models against this project's four Gemini-dependent *text*
tasks, using the real prompts, the real schemas, and the project's own validators as
graders (see tasks.py).

Run:
    python benchmarks/local_llm/run_benchmark.py --models qwen2.5:3b qwen3.5:4b phi4-mini:3.8b

What is measured, per (model, task, case):
  quality            task-specific score in [0,1] from the project's own graders
  structured_valid   did the output validate against the real Pydantic model/schema
  latency_s          wall-clock for the generation
  tokens_per_s       from Ollama's own eval_count / eval_duration
  load_time_s        Ollama's load_duration (0 once the model is already resident)
  vram_peak_mb       nvidia-smi sample while the model is resident
  ram_used_mb        system RAM in use during the call
  failed             transport error, timeout, or unparseable output

Structured tasks pass the JSON Schema to Ollama's `format`, which constrains decoding
(XGrammar) -- the same contract GeminiProvider gets from response_json_schema. So
`structured_valid` is expected to be high for every model; it is measured rather than
assumed, and the real differentiator is `quality`.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tasks  # noqa: E402

OLLAMA = "http://localhost:11434"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
GEN_TIMEOUT_S = 300


# ---------------------------------------------------------------------------
# Resource sampling
# ---------------------------------------------------------------------------

# nvidia-smi reports whole-GPU memory, and on Windows (WDDM) per-process VRAM comes
# back as [N/A], so a raw reading includes the desktop compositor, the browser and any
# other still-resident model -- qwen2.5:3b measured 2807 MB on a quiet GPU and 5188 MB
# with another model loaded, which is not a property of qwen2.5. The model's own
# footprint is therefore reported as a delta against a baseline sampled while no model
# is loaded. `vram_peak_mb` keeps the absolute reading; `vram_model_mb` is the delta.
_VRAM_BASELINE_MB: float | None = None


def vram_used_mb() -> float | None:
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


def ram_used_mb() -> float | None:
    try:
        import psutil

        return psutil.virtual_memory().used / (1024 * 1024)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def generate(model: str, prompt: str, schema: dict | None, temperature: float = 0.2) -> dict:
    """One generation. Returns a dict with text + Ollama's own timing counters."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        # Reasoning models (qwen3.5 here) otherwise route their whole answer into a
        # separate `thinking` field and leave `response` empty, which reads as a total
        # failure while the model in fact answered correctly. These are extraction
        # tasks with no reasoning to do, so the chain of thought is pure latency:
        # disabling it made qwen3.5's JD case 13.1s -> 10.3s. Non-thinking models
        # accept and ignore the flag (verified on qwen2.5:3b), so it is always sent.
        "think": False,
        "options": {"temperature": temperature},
    }
    if schema is not None:
        # The exact transformation the production provider applies, imported rather
        # than reimplemented -- otherwise the benchmark would be measuring a schema
        # the real fallback never sends. See shared/llm.py for why it is needed.
        from shared.llm import ollama_compatible_schema

        payload["format"] = ollama_compatible_schema(schema)

    started = time.perf_counter()
    resp = requests.post(f"{OLLAMA}/api/generate", json=payload, timeout=GEN_TIMEOUT_S)
    latency = time.perf_counter() - started
    resp.raise_for_status()
    body = resp.json()

    eval_count = body.get("eval_count") or 0
    eval_ns = body.get("eval_duration") or 0
    # Belt and braces: if a model honours `think` only partially, or ignores the flag,
    # take the thinking text rather than scoring a correct answer as an empty one.
    text = body.get("response") or ""
    if not text.strip():
        text = body.get("thinking") or ""

    return {
        "text": text,
        "latency_s": round(latency, 3),
        "tokens_per_s": round(eval_count / (eval_ns / 1e9), 2) if eval_ns else None,
        "eval_tokens": eval_count,
        "prompt_tokens": body.get("prompt_eval_count") or 0,
        "load_time_s": round((body.get("load_duration") or 0) / 1e9, 3),
    }


def unload(model: str) -> None:
    """Ask Ollama to evict the model so the next load time is measured honestly."""
    try:
        requests.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Structured-output validation -- the project's real gates
# ---------------------------------------------------------------------------

def validate_jd(raw: dict):
    return tasks.jd_model().model_validate(raw)


def validate_resume(raw: dict):
    from schemas.candidate_extraction import CandidateExtraction

    return CandidateExtraction.model_validate(raw)


def validate_evaluation(raw: dict):
    required = tasks.evaluation_schema()["required"]
    missing = [k for k in required if k not in raw]
    if missing:
        raise ValueError(f"missing required keys: {missing}")
    return raw


# ---------------------------------------------------------------------------
# Case runners
# ---------------------------------------------------------------------------

def _record(model, task, case_id, gen, quality_info, structured_valid, failed, error, vram, ram):
    row = {
        "model": model,
        "task": task,
        "case": case_id,
        "failed": failed,
        "error": error,
        "structured_valid": structured_valid,
        "latency_s": gen.get("latency_s") if gen else None,
        "tokens_per_s": gen.get("tokens_per_s") if gen else None,
        "eval_tokens": gen.get("eval_tokens") if gen else None,
        "prompt_tokens": gen.get("prompt_tokens") if gen else None,
        "load_time_s": gen.get("load_time_s") if gen else None,
        "vram_peak_mb": vram,
        "vram_baseline_mb": _VRAM_BASELINE_MB,
        "vram_model_mb": (round(vram - _VRAM_BASELINE_MB, 1)
                          if vram is not None and _VRAM_BASELINE_MB is not None else None),
        "ram_used_mb": round(ram, 1) if ram else None,
    }
    row.update(quality_info or {})
    return row


def run_structured_case(model, task_name, case_id, prompt, schema, validator, grader, case):
    gen, err, structured_valid, quality_info = None, None, False, {"quality": 0.0}
    try:
        gen = generate(model, prompt, schema)
        try:
            parsed = json.loads(gen["text"])
        except json.JSONDecodeError as exc:
            raise ValueError(f"output was not JSON: {exc}") from exc
        validated = validator(parsed)
        structured_valid = True
        as_dict = validated.model_dump() if hasattr(validated, "model_dump") else validated
        quality_info = grader(as_dict, case)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"[:300]
    return _record(
        model, task_name, case_id, gen, quality_info, structured_valid,
        failed=err is not None, error=err, vram=vram_used_mb(), ram=ram_used_mb(),
    )


def run_text_case(model, task_name, case_id, prompt, grader, case):
    gen, err, quality_info = None, None, {"quality": 0.0}
    try:
        gen = generate(model, prompt, schema=None)
        quality_info = grader(gen["text"], case)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"[:300]
    return _record(
        model, task_name, case_id, gen, quality_info, structured_valid=True,
        failed=err is not None, error=err, vram=vram_used_mb(), ram=ram_used_mb(),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def benchmark_model(model: str, latency_reps: int) -> list[dict]:
    rows: list[dict] = []
    print(f"\n{'=' * 70}\nMODEL: {model}\n{'=' * 70}", flush=True)

    unload(model)
    time.sleep(2)

    global _VRAM_BASELINE_MB
    _VRAM_BASELINE_MB = vram_used_mb()
    print(f"  vram baseline (no model loaded): {_VRAM_BASELINE_MB} MB", flush=True)

    # --- Task 1: JD structuring -------------------------------------------------
    schema = tasks.jd_schema()
    for case in tasks.JD_CASES:
        prompt = tasks._jd_prompt(case["raw"])
        row = run_structured_case(model, "jd_structuring", case["id"], prompt, schema,
                                  validate_jd, tasks.grade_jd, case)
        rows.append(row)
        print(f"  jd/{case['id']:<28} quality={row.get('quality')} "
              f"valid={row['structured_valid']} {row.get('latency_s')}s", flush=True)

    # --- Task 2: Resume parsing -------------------------------------------------
    schema = tasks.resume_schema()
    for case in tasks.RESUME_CASES:
        prompt = case["prompt_fn"]()
        row = run_structured_case(model, "resume_parsing", case["id"], prompt, schema,
                                  validate_resume, tasks.grade_resume, case)
        rows.append(row)
        print(f"  resume/{case['id']:<24} quality={row.get('quality')} "
              f"valid={row['structured_valid']} {row.get('latency_s')}s", flush=True)

    # --- Task 3: Match explanation ----------------------------------------------
    for case in tasks.EXPLANATION_CASES:
        prompt = case["prompt_fn"]()
        row = run_text_case(model, "match_explanation", case["id"], prompt,
                            tasks.grade_explanation, case)
        rows.append(row)
        print(f"  expl/{case['id']:<26} quality={row.get('quality')} "
              f"{row.get('latency_s')}s", flush=True)

    # --- Task 4: Interview evaluation -------------------------------------------
    schema = tasks.evaluation_schema()
    for case in tasks.evaluation_cases():
        prompt = tasks.evaluation_prompt(case["transcript"], inconclusive=case["pre_check_inconclusive"])
        row = run_structured_case(model, "interview_evaluation", case["id"], prompt, schema,
                                  validate_evaluation, tasks.grade_evaluation, case)
        rows.append(row)
        print(f"  eval/{case['id']:<26} quality={row.get('quality')} "
              f"cefr={row.get('cefr_level')} want={row.get('expected_cefr')} "
              f"{row.get('latency_s')}s", flush=True)

    # --- Latency/throughput stability on one representative case ----------------
    rep_prompt = tasks._jd_prompt(tasks.JD_CASES[0]["raw"])
    rep_schema = tasks.jd_schema()
    for i in range(latency_reps):
        row = run_structured_case(model, "latency_probe", f"rep_{i+1}", rep_prompt, rep_schema,
                                  validate_jd, tasks.grade_jd, tasks.JD_CASES[0])
        rows.append(row)

    # --- Cold-start load time (honest: model evicted first) ---------------------
    unload(model)
    time.sleep(3)
    cold = run_structured_case(model, "cold_load", "cold_start", rep_prompt, rep_schema,
                               validate_jd, tasks.grade_jd, tasks.JD_CASES[0])
    rows.append(cold)
    print(f"  cold-start load: {cold.get('load_time_s')}s  vram={cold.get('vram_peak_mb')}MB", flush=True)

    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--latency-reps", type=int, default=3)
    ap.add_argument("--tag", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    args = ap.parse_args()

    try:
        requests.get(f"{OLLAMA}/api/tags", timeout=10).raise_for_status()
    except Exception as exc:
        print(f"Ollama is not reachable at {OLLAMA}: {exc}", file=sys.stderr)
        return 2

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []
    for model in args.models:
        all_rows.extend(benchmark_model(model, args.latency_reps))

    raw_path = RESULTS_DIR / f"raw_{args.tag}.json"
    raw_path.write_text(json.dumps(all_rows, indent=2), encoding="utf-8")

    fields: list[str] = []
    for r in all_rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    csv_path = RESULTS_DIR / f"raw_{args.tag}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(all_rows)

    # Per (model, task) summary
    summary: list[dict] = []
    models = sorted({r["model"] for r in all_rows})
    task_names = ["jd_structuring", "resume_parsing", "match_explanation", "interview_evaluation"]
    for model in models:
        for task in task_names:
            sel = [r for r in all_rows if r["model"] == model and r["task"] == task]
            if not sel:
                continue
            ok = [r for r in sel if not r["failed"]]
            lat = [r["latency_s"] for r in ok if r["latency_s"] is not None]
            tps = [r["tokens_per_s"] for r in ok if r["tokens_per_s"]]
            summary.append({
                "model": model,
                "task": task,
                "cases": len(sel),
                "mean_quality": round(statistics.mean([r.get("quality", 0.0) for r in sel]), 4),
                "structured_valid_rate": round(sum(bool(r["structured_valid"]) for r in sel) / len(sel), 4),
                "failure_rate": round(sum(bool(r["failed"]) for r in sel) / len(sel), 4),
                "median_latency_s": round(statistics.median(lat), 3) if lat else None,
                "mean_tokens_per_s": round(statistics.mean(tps), 2) if tps else None,
            })
        probe = [r for r in all_rows if r["model"] == model and r["task"] in ("latency_probe", "cold_load")]
        cold = [r for r in all_rows if r["model"] == model and r["task"] == "cold_load"]
        vram = [r["vram_peak_mb"] for r in probe if r["vram_peak_mb"]]
        summary.append({
            "model": model,
            "task": "_resources",
            "cases": len(probe),
            "vram_peak_mb": max(vram) if vram else None,
            "cold_load_time_s": cold[0]["load_time_s"] if cold else None,
            "ram_used_mb": max([r["ram_used_mb"] for r in probe if r["ram_used_mb"]] or [0]) or None,
        })

    sum_path = RESULTS_DIR / f"summary_{args.tag}.json"
    sum_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote:\n  {raw_path}\n  {csv_path}\n  {sum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
