"""
benchmarks/load/matching_latency_test.py
-----------------------------------------
Confirms milestone 53's N+1 removal with numbers, and measures `submit_job` end to end.

`SCALING_AND_FUTURE_IMPROVEMENTS.md` §4 called the N+1 in `submit_job` "the sharpest
bottleneck" and predicted its cost: *"20 retrieved candidates = 20 sequential
cross-service calls. At ~50 ms each that is a second of pure round-trip; at top_k=100 it
is five."* Milestone 53 replaced the loop with one batch call and proved the behaviour
(one request instead of N, tolerances preserved) but never measured it, so §4's own
caveats list still owes this number.

Two measurements, deliberately separate:

  PART A  the controlled comparison. Both endpoints still exist -- the batch one and the
          single-candidate `GET /v1/knowledge/candidates/{id}` -- so the old and new
          fetch strategies can be timed against the *same* candidate ids on the *same*
          live stack, with no code reverted and nothing mocked. This is what actually
          confirms or refutes §4's prediction, and it touches no LLM at all, so it is
          unaffected by Gemini quota.

  PART B  `submit_job` end to end at several `top_k` values. The headline number a
          recruiter actually waits for. If the N+1 were still present, total latency
          would climb roughly linearly with `top_k`; if batching worked, the fetch
          component should be near-flat and any remaining growth belongs to scoring and
          persistence.

**Provider attribution, per milestone 63's lesson.** Part B's first step is Gemini
structuring the JD, so its latency depends on which provider served it. A throughput or
latency number without that attribution is uninterpretable -- that is exactly what made
milestone 54's ingestion run useless. Part B reports the split and refuses to present a
clean-Gemini reading when the fallback was involved.

Everything runs inside the containers: neither Second Brain nor RWS publishes a host
port (milestone 45, L7).

Run (from the repo root, stack up):
    python benchmarks/load/matching_latency_test.py --part a
    python benchmarks/load/matching_latency_test.py --part b
    python benchmarks/load/matching_latency_test.py            # both
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

RWS = "csb-recruitment-workflow"
WORKER_LOG = "csb-recruitment-workflow"

# Sized against the live pool (74 candidate_knowledge rows at the time of writing) and
# §4's own example values, so the table can be read directly against its prediction.
FETCH_SIZES = (1, 5, 10, 20, 40)
TOP_K_VALUES = (5, 10, 20, 40)
REPEATS = 3


def _run_in(container: str, code: str, timeout: int = 900) -> str:
    out = subprocess.run(
        ["docker", "exec", "-i", container, "python", "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if out.returncode != 0:
        raise SystemExit(f"{container} command failed:\n{out.stderr[-900:]}")
    return out.stdout.strip()


def _psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "csb-postgres", "psql", "-U", "csb_admin",
         "-d", "company_second_brain", "-tAc", sql],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr[:300]}")
    return out.stdout.strip()


def _env(path: str, key: str) -> str:
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{key} not found in {path}")


def candidate_ids(limit: int) -> list[str]:
    rows = _psql(
        f"SELECT candidate_id FROM second_brain.candidate_knowledge "
        f"ORDER BY candidate_id LIMIT {limit};"
    )
    ids = [r.strip() for r in rows.splitlines() if r.strip()]
    if not ids:
        raise SystemExit("no candidates in second_brain.candidate_knowledge")
    return ids


def part_a(ids: list[str], sb_url: str, sb_key: str) -> list[dict]:
    """Times N sequential GETs against one batch POST, for the same ids."""
    code = f'''
import asyncio, json, time, httpx
IDS = {json.dumps(ids)}
URL = {sb_url!r}
HEAD = {{"X-Api-Key": {sb_key!r}}}
SIZES = {json.dumps(list(FETCH_SIZES))}
REPEATS = {REPEATS}

async def sequential(client, ids):
    """The pre-milestone-53 shape: one round-trip per candidate, in order."""
    t = time.perf_counter()
    got = 0
    for cid in ids:
        r = await client.get(f"{{URL}}/v1/knowledge/candidates/{{cid}}", headers=HEAD)
        if r.status_code == 200:
            got += 1
    return (time.perf_counter() - t) * 1000, got

async def batched(client, ids):
    """The current shape: one round-trip for all of them."""
    t = time.perf_counter()
    r = await client.post(f"{{URL}}/v1/knowledge/candidates/batch",
                          headers=HEAD, json={{"candidate_ids": ids}})
    ms = (time.perf_counter() - t) * 1000
    n = 0
    if r.status_code == 200:
        body = r.json()
        n = len(body.get("candidates", body if isinstance(body, list) else []))
    else:
        return ms, -r.status_code
    return ms, n

async def main():
    rows = []
    async with httpx.AsyncClient(timeout=300) as client:
        # One warm-up so connection setup and any lazy import is not charged to the
        # first measured size, which would otherwise look like a size effect.
        await batched(client, IDS[:1])
        for n in SIZES:
            if n > len(IDS):
                continue
            subset = IDS[:n]
            seq = [await sequential(client, subset) for _ in range(REPEATS)]
            bat = [await batched(client, subset) for _ in range(REPEATS)]
            rows.append({{
                "n": n,
                "sequential_ms": [round(m, 1) for m, _ in seq],
                "batch_ms": [round(m, 1) for m, _ in bat],
                "sequential_got": seq[0][1],
                "batch_got": bat[0][1],
            }})
    print("RESULT:" + json.dumps(rows))

asyncio.run(main())
'''
    out = _run_in(RWS, code)
    for line in out.splitlines():
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    raise SystemExit(f"part A returned nothing usable:\n{out[-600:]}")


def provider_mix(since_iso: str) -> dict:
    """Same approach as ingest_load_test: read the service's own structured log lines.
    Explicit encoding for the reason documented there (cp1252 vs container log bytes)."""
    out = subprocess.run(
        ["docker", "logs", "--since", since_iso, WORKER_LOG],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    text = (out.stdout or "") + (out.stderr or "")
    return {
        "fallback_calls": text.count("llm_primary_unavailable_using_fallback"),
        "fallback_succeeded": text.count("llm_fallback_succeeded"),
        "fallback_also_failed": text.count("llm_fallback_also_failed"),
    }


def part_b(email: str, password: str) -> list[dict]:
    code = f'''
import asyncio, json, time, httpx
TOP_K = {json.dumps(list(TOP_K_VALUES))}
REPEATS = {REPEATS}
DESC = (
    "We are hiring a backend engineer to build and maintain REST APIs in Python. "
    "You will work with FastAPI, PostgreSQL and Docker, deploy services, and "
    "collaborate with a small team. Five years of professional experience expected."
)

async def main():
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=600) as c:
        r = await c.post("/v1/auth/login", json={{"email": {email!r}, "password": {password!r}}})
        r.raise_for_status()
        token = r.json()["access_token"]
        head = {{"Authorization": f"Bearer {{token}}"}}

        rows = []
        for k in TOP_K:
            samples, matches = [], None
            for i in range(REPEATS):
                t = time.perf_counter()
                resp = await c.post("/v1/jobs", headers=head, json={{
                    "title": f"Backend Engineer k{{k}} r{{i}}",
                    "raw_description": DESC,
                    "threshold": 0.6,
                    "top_k": k,
                }})
                ms = (time.perf_counter() - t) * 1000
                if resp.status_code != 200:
                    samples.append(-resp.status_code)
                    continue
                samples.append(round(ms, 1))
                body = resp.json()
                matches = body.get("match_count", body.get("matches_created"))
                if matches is None and isinstance(body.get("matches"), list):
                    matches = len(body["matches"])
            rows.append({{"top_k": k, "ms": samples, "matches": matches}})
        print("RESULT:" + json.dumps(rows))

asyncio.run(main())
'''
    out = _run_in(RWS, code)
    for line in out.splitlines():
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    raise SystemExit(f"part B returned nothing usable:\n{out[-600:]}")


def _median_ok(values: list[float]) -> float | None:
    ok = [v for v in values if v is not None and v > 0]
    return round(statistics.median(ok), 1) if ok else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=["a", "b", "both"], default="both")
    args = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    report: dict = {"measured_at": datetime.now(timezone.utc).isoformat(), "repeats": REPEATS}

    if args.part in ("a", "both"):
        sb_url = _env("services/recruitment-workflow/.env", "SECOND_BRAIN_URL")
        sb_key = _env("services/recruitment-workflow/.env", "SECOND_BRAIN_API_KEY")
        ids = candidate_ids(max(FETCH_SIZES))
        print(f"\nPART A  sequential GETs vs one batch POST  ({len(ids)} candidates available)\n")
        rows = part_a(ids, sb_url, sb_key)
        report["part_a"] = rows

        print(f"  {'n':>4}{'sequential (ms)':>18}{'batch (ms)':>13}{'speedup':>10}{'per-candidate':>15}")
        print("  " + "-" * 60)
        for row in rows:
            seq, bat = _median_ok(row["sequential_ms"]), _median_ok(row["batch_ms"])
            if seq is None or bat is None:
                print(f"  {row['n']:>4}   (failed: {row})")
                continue
            print(f"  {row['n']:>4}{seq:>18.1f}{bat:>13.1f}{seq/bat:>9.1f}x{seq/row['n']:>14.1f}")
        print("\n  per-candidate = sequential cost / n, i.e. the round-trip §4 estimated at ~50 ms")

    if args.part in ("b", "both"):
        email = _env("services/recruitment-workflow/.env", "SEED_RECRUITER_EMAIL")
        password = _env("services/recruitment-workflow/.env", "SEED_RECRUITER_PASSWORD")
        since = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        print(f"\nPART B  submit_job end to end\n")
        rows = part_b(email, password)
        mix = provider_mix(since)
        report["part_b"] = rows
        report["part_b_provider_mix"] = mix

        print(f"  {'top_k':>6}{'median ms':>12}{'all samples':>34}{'matches':>9}")
        print("  " + "-" * 62)
        for row in rows:
            med = _median_ok(row["ms"])
            shown = ", ".join(str(v) for v in row["ms"])
            print(f"  {row['top_k']:>6}{(med if med else -1):>12.1f}{shown:>34}{str(row['matches']):>9}")

        print(f"\n  provider: {mix['fallback_succeeded']} JD structurings served by the local "
              f"fallback, {mix['fallback_calls']} primary failures")
        if mix["fallback_calls"]:
            print("  NOT a clean Gemini reading: JD structuring fell back, so the fixed")
            print("  per-request cost below reflects the LOCAL model's latency, not Gemini's.")
        else:
            print("  Clean Gemini reading: the fallback was never invoked.")

        medians = [(_median_ok(r["ms"]), r["top_k"]) for r in rows]
        usable = [(m, k) for m, k in medians if m]
        if len(usable) >= 2:
            (m_lo, k_lo), (m_hi, k_hi) = usable[0], usable[-1]
            growth = (m_hi - m_lo) / max(1, (k_hi - k_lo))
            print(f"\n  growth from top_k={k_lo} to {k_hi}: {m_hi - m_lo:+.0f} ms total, "
                  f"{growth:+.1f} ms per extra candidate")
            print("  Section 4 predicted ~50 ms per extra candidate while the N+1 stood.")

    out = RESULTS / f"matching_latency_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
