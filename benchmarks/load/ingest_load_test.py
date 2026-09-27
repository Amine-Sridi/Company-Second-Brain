"""
benchmarks/load/ingest_load_test.py
------------------------------------
Measures end-to-end resume-ingestion throughput through the queue, so the 100,000 CVs/day
target stops being an estimate.

Every scaling threshold written in SCALING_AND_FUTURE_IMPROVEMENTS.md is currently
*reasoned*, not measured -- the document says so in its own caveats section. This script
replaces the most important one with a number, and `CLAUDE.md` now makes that number the
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
    succeeded = counts.get("succeeded", 0)
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
    print("=" * 62)
    print("  NOTE: one worker container on a developer laptop that also hosts Ollama.")
    print("  Check the worker log's provider mix before reading anything into this: if")
    print("  Gemini was quota-exhausted the extractions were served by the local model on")
    print("  a single GPU, which serialises -- that measures the fallback's ceiling, not")
    print("  the system's. See handoff.md milestone 54.")

    out = RESULTS / f"ingest_load_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  wrote {out}")

    for p in cvs:
        p.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
