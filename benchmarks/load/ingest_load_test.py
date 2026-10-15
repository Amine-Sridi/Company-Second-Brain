"""
benchmarks/load/ingest_load_test.py
------------------------------------
Measures end-to-end resume-ingestion throughput through the queue, so the 100,000 CVs/day
target stops being an estimate.

Every scaling threshold written in SCALING_AND_FUTURE_IMPROVEMENTS.md is currently
*reasoned*, not measured -- the document says so in its own caveats section. This script
replaces the most important one with a number, and `docs/ARCHITECTURE_DECISIONS.md` now makes that number the
precondition for adding Prometheus at all ("deferred until there is a throughput number
worth tuning").

What it measures, deliberately end-to-end rather than per-endpoint: a generic HTTP load
tool would report how fast `POST /v1/resumes/jobs` returns 202, which is nearly
meaningless here -- accepting work quickly is the easy half. What matters is how fast
**jobs drain**, so this enqueues N CVs and then polls `resume_intel.ingest_jobs` until
every one reaches a terminal state.

What it does NOT measure: extraction *quality*. The CVs are synthetic and structurally
identical, which is right for throughput (the pipeline does the same work either way) and
wrong for accuracy. Quality is covered by the benchmark suite in benchmarks/local_llm/.

Run (from the repo root, stack up):
    python benchmarks/load/ingest_load_test.py --count 20 --concurrency 5
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

# Terminal states in resume_intel.ingest_jobs.
DONE = ("succeeded", "failed")


def build_cv(index: int, out_dir: Path) -> Path:
    """One synthetic CV with a unique email.

    Unique emails matter: store_candidate dedups on normalized email, so N copies of the
    same CV would short-circuit after the first and measure nothing but the dedup path.
    """
    from docx import Document

    doc = Document()
    for line in [
        f"Load Test Candidate {index}",
        f"Email: load.test.{index}.{uuid.uuid4().hex[:8]}@example.com",
        f"Phone: +216 20 {index:03d} {index:03d}",
        "",
        "Summary: Backend engineer with 5 years building REST APIs in Python and "
        "FastAPI, deploying with Docker and working with PostgreSQL.",
        "",
        "Experience:",
        "Backend Engineer at Example Corp, 2019-01 to 2024-06.",
        "Built and maintained REST APIs, deployed services with Docker, worked with "
        "PostgreSQL databases.",
        "",
        "Junior Developer at Startup SARL, 2017-09 to 2018-12.",
        "Maintained internal tooling in Python and Django.",
        "",
        "Skills: Python, FastAPI, PostgreSQL, Docker, REST APIs, Git, Linux",
    ]:
        doc.add_paragraph(line)
    path = out_dir / f"load_cv_{index}.docx"
    doc.save(str(path))
    return path


def _psql(sql: str) -> str:
    """Query Postgres through the container -- host psycopg cannot reach it through the
    WSL2 port proxy (see handoff.md's Known Limitations)."""
    out = subprocess.run(
        ["docker", "exec", "csb-postgres", "psql", "-U", "csb_admin",
         "-d", "company_second_brain", "-tAc", sql],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr[:300]}")
    return out.stdout.strip()


def enqueue_all(cv_paths: list[Path], api_key: str, concurrency: int) -> tuple[list[str], float]:
    """Uploads every CV, returning (job_ids, elapsed_seconds).

    Runs inside the resume-intelligence container: the service deliberately publishes no
    host port (milestone 45, L7), so this is how a client reaches it.
    """
    # Copy the files in, then drive the uploads from one in-container script so the
    # measurement is not dominated by `docker cp` round-trips.
    for p in cv_paths:
        subprocess.run(["docker", "cp", str(p), f"csb-resume-intelligence:/tmp/{p.name}"],
                       check=True, capture_output=True, timeout=120)

    script = f'''
import asyncio, json, httpx, pathlib
names = {json.dumps([p.name for p in cv_paths])}
KEY = {api_key!r}
sem = asyncio.Semaphore({concurrency})

async def one(name):
    async with sem:
        with open("/tmp/" + name, "rb") as f:
            data = f.read()
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post("http://localhost:8000/v1/resumes/jobs",
                files={{"file": (name, data,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}},
                headers={{"X-Api-Key": KEY}})
        if r.status_code != 202:
            return {{"error": r.status_code, "body": r.text[:120]}}
        return r.json()

async def main():
    results = await asyncio.gather(*[one(n) for n in names])
    print(json.dumps(results))

asyncio.run(main())
'''
    started = time.perf_counter()
    out = subprocess.run(
        ["docker", "exec", "-i", "csb-resume-intelligence", "python", "-c", script],
        capture_output=True, text=True, timeout=1800,
    )
    elapsed = time.perf_counter() - started
    if out.returncode != 0:
        raise SystemExit(f"enqueue failed: {out.stderr[-600:]}")

    payload = json.loads(out.stdout.strip().splitlines()[-1])
    job_ids, errors = [], []
    for entry in payload:
        (job_ids if "job_id" in entry else errors).append(entry.get("job_id") or entry)
    if errors:
        print(f"  !! {len(errors)} upload(s) rejected, e.g. {errors[0]}")
    return job_ids, elapsed


def drain(job_ids: list[str], poll_seconds: float, timeout_seconds: float) -> dict:
    """Polls until every job is terminal, or the timeout expires."""
    id_list = ",".join(f"'{j}'" for j in job_ids)
    started = time.perf_counter()
    last_done = -1
    while True:
        rows = _psql(
            f"SELECT status, count(*) FROM resume_intel.ingest_jobs "
            f"WHERE id IN ({id_list}) GROUP BY status;"
        )
        counts = {}
        for line in rows.splitlines():
            if "|" in line:
                st, n = line.split("|")
                counts[st.strip()] = int(n)
        done = sum(counts.get(s, 0) for s in DONE)
        if done != last_done:
            elapsed = time.perf_counter() - started
            print(f"  {elapsed:7.1f}s  {counts}  ({done}/{len(job_ids)} terminal)", flush=True)
            last_done = done
        if done >= len(job_ids):
            return counts
        if time.perf_counter() - started > timeout_seconds:
            print(f"  !! timed out with {done}/{len(job_ids)} terminal")
            return counts
        time.sleep(poll_seconds)


def provider_mix(since_iso: str) -> dict:
    """Counts how many extractions the local fallback served during the run.

    This exists because milestone 54's measurement was ambiguous in exactly the way that
    matters: the stack was quota-exhausted, so 23 of 24 extractions were served by the
    local model on a single GPU, which serialises -- and that had to be reconstructed by
    hand afterwards from the worker log. A throughput number is uninterpretable without
    knowing which provider produced it, so the harness now reports it.

    Read from the worker's structured log lines (shared/llm.py emits
    `llm_primary_unavailable_using_fallback` and `llm_fallback_succeeded`) rather than
    from the database, because no column records the provider -- adding one would mean a
    migration for a benchmark's benefit.

    Caveat kept deliberately visible in the output: these are *call* counts, not job
    counts. One job makes one extraction call today, so they coincide, but a future
    pipeline with two LLM calls per CV would make `fallback_calls` exceed the number of
    jobs. `gemini_served_estimate` is therefore an estimate and is labelled as one.
    """
    # encoding/errors are explicit, not decorative: `text=True` alone decodes with the
    # host's preferred encoding, which on this Windows machine is cp1252, and container
    # logs carry bytes that are not valid cp1252. That raised UnicodeDecodeError inside
    # subprocess's reader *thread*, where it does not propagate -- the run completed and
    # silently reported zero fallback calls, which is indistinguishable from a genuinely
    # clean Gemini-path run. Counting on a partially decoded log would have been worse
    # than not counting at all.
    out = subprocess.run(
        ["docker", "logs", "--since", since_iso, "csb-resume-intelligence-worker"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    # Docker writes container logs to stderr as well as stdout depending on the stream
    # the process used, so both are searched.
    text = (out.stdout or "") + (out.stderr or "")
    return {
        "fallback_calls": text.count("llm_primary_unavailable_using_fallback"),
        "fallback_succeeded": text.count("llm_fallback_succeeded"),
        "fallback_also_failed": text.count("llm_fallback_also_failed"),
        "source": "csb-resume-intelligence-worker structured log lines",
    }


def per_job_durations(job_ids: list[str]) -> list[float]:
    id_list = ",".join(f"'{j}'" for j in job_ids)
    rows = _psql(
        f"SELECT EXTRACT(EPOCH FROM (updated_at - created_at)) FROM resume_intel.ingest_jobs "
        f"WHERE id IN ({id_list}) AND status IN ('succeeded','failed');"
    )
    out = []
    for line in rows.splitlines():
        line = line.strip()
        if line:
            try:
                out.append(float(line))
            except ValueError:
                pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--concurrency", type=int, default=5, help="parallel uploads")
    ap.add_argument("--poll", type=float, default=5.0)
    ap.add_argument("--timeout", type=float, default=1800.0)
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()

    api_key = args.api_key
    if not api_key:
        env = Path("services/resume-intelligence/.env")
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("SERVICE_API_KEY="):
                api_key = line.split("=", 1)[1].strip()
                break
    if not api_key:
        raise SystemExit("SERVICE_API_KEY not found; pass --api-key")

    RESULTS.mkdir(parents=True, exist_ok=True)
    tmp = RESULTS / "cvs"
    tmp.mkdir(exist_ok=True)

    print(f"\nBuilding {args.count} synthetic CVs ...", flush=True)
    cvs = [build_cv(i, tmp) for i in range(args.count)]

    # Docker's --since wants a timestamp it can parse; take it just before the first
    # upload so the window covers exactly this run and no earlier one.
    run_since = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    print(f"Enqueueing {args.count} (concurrency {args.concurrency}) ...", flush=True)
    job_ids, enqueue_s = enqueue_all(cvs, api_key, args.concurrency)
    print(f"  accepted {len(job_ids)} in {enqueue_s:.1f}s "
          f"({len(job_ids)/enqueue_s if enqueue_s else 0:.2f}/s accept rate)", flush=True)

    if not job_ids:
        raise SystemExit("nothing accepted; aborting")

    print("Draining ...", flush=True)
    drain_started = time.perf_counter()
    counts = drain(job_ids, args.poll, args.timeout)
    drain_s = time.perf_counter() - drain_started

    durations = per_job_durations(job_ids)
    mix = provider_mix(run_since)
    succeeded = counts.get("succeeded", 0)
    # Labelled an estimate on purpose: see provider_mix's docstring on call-vs-job counts.
    mix["gemini_served_estimate"] = max(0, succeeded - mix["fallback_succeeded"])
    total_s = enqueue_s + drain_s
    per_hour = (succeeded / total_s * 3600) if total_s else 0

    report = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "requested": args.count,
        "accepted": len(job_ids),
        "upload_concurrency": args.concurrency,
        "counts": counts,
        "enqueue_seconds": round(enqueue_s, 2),
        "drain_seconds": round(drain_s, 2),
        "wall_seconds": round(total_s, 2),
        "succeeded_per_hour": round(per_hour, 1),
        "projected_per_day_24h": round(per_hour * 24),
        "provider_mix": mix,
        "per_job_seconds": {
            "n": len(durations),
            "min": round(min(durations), 2) if durations else None,
            "median": round(statistics.median(durations), 2) if durations else None,
            "max": round(max(durations), 2) if durations else None,
        },
    }

    print("\n" + "=" * 62)
    print(f"  accepted        : {report['accepted']}/{report['requested']}")
    print(f"  outcome         : {counts}")
    print(f"  wall clock      : {report['wall_seconds']}s")
    print(f"  throughput      : {report['succeeded_per_hour']} succeeded CVs/hour")
    print(f"  projected /day  : {report['projected_per_day_24h']:,} (24h at this rate)")
    print(f"  per-job seconds : {report['per_job_seconds']}")
    print(f"  provider mix    : {mix['gemini_served_estimate']} Gemini (est.) / "
          f"{mix['fallback_succeeded']} local fallback"
          + (f"  [{mix['fallback_also_failed']} fallback failures]"
             if mix["fallback_also_failed"] else ""))
    print("=" * 62)
    print("  NOTE: one worker container on a developer laptop that also hosts Ollama.")
    # "Clean" is judged on fallback_CALLS, not fallback_succeeded. A run where the
    # primary faltered and the fallback failed too has zero successful fallback work in
    # it, so keying off fallback_succeeded would call that clean -- while the retries
    # those failures caused are sitting in the latency being reported.
    if mix["fallback_succeeded"] and succeeded:
        share = mix["fallback_succeeded"] / succeeded
        print(f"  {share:.0%} of successful extractions were served by the LOCAL model.")
        if share > 0.2:
            print("  This measures the FALLBACK's ceiling, not the system's: Ollama runs on")
            print("  one GPU and serialises. Do not quote this as the Gemini-path number.")
            print("  (This is exactly what made milestone 54's run uninterpretable.)")
    elif mix["fallback_calls"]:
        print(f"  NOT a clean run: the primary was unavailable {mix['fallback_calls']}x and")
        print(f"  the fallback then failed {mix['fallback_also_failed']}x (is Ollama up?).")
        print("  Jobs that recovered did so by RETRYING the primary, so the latency and")
        print("  throughput here include that retry cost. Re-run once both providers are")
        print("  healthy before quoting either number.")
    elif succeeded:
        print("  Clean Gemini-path measurement: the fallback was never invoked.")

    # A throughput figure divides successes by wall clock, so a run that mostly failed
    # produces a small, tidy, meaningless number -- and the provider-mix line above will
    # happily call it clean, because no fallback was involved in the failing either. The
    # 8-CV run in milestone 63 failed 7/8 on Gemini 429s and still reported "329.5 CVs/
    # hour"; without this warning that figure is quotable-looking nonsense.
    failed = counts.get("failed", 0)
    if failed and (failed / max(1, len(job_ids))) > 0.1:
        print(f"  !! {failed}/{len(job_ids)} jobs FAILED. Throughput counts only successes,")
        print("     so the figure above understates capacity while looking precise. Check")
        print("     the worker log for 429/503 before quoting anything from this run.")

    out = RESULTS / f"ingest_load_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  wrote {out}")

    for p in cvs:
        p.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
