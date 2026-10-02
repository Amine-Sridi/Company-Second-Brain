"""
scripts/retry_advisory_reports.py
----------------------------------
Upgrades reports that have no CEFR verdict by re-evaluating them, once Gemini is
available again. Refuses to touch reports that already have one.

WHY THE REFUSAL IS THE POINT
`retry_report` re-runs the evaluator and overwrites the stored report with whatever comes
back. When Gemini is unavailable the local fallback answers, and milestone 61's design
deliberately withholds the CEFR for a local evaluation -- so retrying a report that
already says "C2 / 96" during an outage **replaces a good verdict with no verdict**.

That is not hypothetical. On 2026-10-02 a blanket retry over eleven reports destroyed four
real verdicts (C2, C2, C2, C1) because Gemini's quota had been exhausted minutes earlier.
The work was recoverable only because the transcripts are stored. This script exists so
that cannot happen by accident again: it selects strictly on evaluation_status, and
'completed' is never in the selection.

WHAT IT RETRIES
  fallback_advisory     evaluated by the local model, so no CEFR was issued
  failed                the evaluation errored outright
  insufficient_evidence ONLY with --include-insufficient. By default these are left
                        alone, because a withheld verdict is usually the correct answer
                        about a thin transcript, not a failure to retry away.

BEFORE RUNNING IT
Check that Gemini is actually serving, or every retry will fall back to the local model
and achieve nothing while costing time:

    python scripts/check_llm_fallback.py --generate     # the stack's own provider check

A quicker read: if `docker logs --since 10m csb-resume-intelligence-worker` shows
llm_primary_unavailable_using_fallback, wait longer.

Run:
    python scripts/retry_advisory_reports.py                # dry run, lists candidates
    python scripts/retry_advisory_reports.py --apply
"""
from __future__ import annotations

import argparse
import json
import subprocess

VOICEHR = "csb-voicehr"

# Statuses worth re-evaluating. 'completed' is deliberately absent and must stay absent.
RETRYABLE = ("fallback_advisory", "failed")
OPTIONAL = ("insufficient_evidence",)


def run_in_voicehr(code: str, timeout: int = 7200) -> str:
    out = subprocess.run(
        ["docker", "exec", "-i", VOICEHR, "python", "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if out.returncode != 0:
        raise SystemExit(f"voicehr command failed:\n{(out.stderr or '')[-800:]}")
    return out.stdout


def extract(stdout: str, marker: str = "RESULT:"):
    for line in stdout.splitlines():
        if line.startswith(marker):
            return json.loads(line[len(marker):])
    raise SystemExit(f"no result returned:\n{stdout[-500:]}")


def list_targets(statuses: tuple[str, ...]) -> list[dict]:
    code = f'''
import json, sqlite3
import backend.database as d
STATUSES = {json.dumps(list(statuses))}
conn = sqlite3.connect(d.DB_PATH); conn.row_factory = sqlite3.Row
marks = ",".join("?" for _ in STATUSES)
rows = conn.execute(
    "SELECT rp.report_token, rp.evaluation_status, rp.cefr_level, rp.overall_score,"
    " rp.sufficiency_reason, i.candidate_name"
    " FROM reports rp JOIN interviews i ON i.id = rp.interview_id"
    f" WHERE rp.evaluation_status IN ({{marks}}) ORDER BY i.candidate_name", STATUSES
).fetchall()
conn.close()
print("RESULT:" + json.dumps([dict(r) for r in rows]))
'''
    return extract(run_in_voicehr(code, timeout=300))


def retry(tokens: list[str], attempts: int, pause: float) -> list[dict]:
    code = f'''
import asyncio, json
from backend.services.processing import retry_report
TOKENS = {json.dumps(tokens)}
ATTEMPTS = {attempts}
PAUSE = {pause}

async def main():
    results = []
    for token in TOKENS:
        outcome = None
        for attempt in range(1, ATTEMPTS + 1):
            try:
                scores = await retry_report(token)
                outcome = {{"token": token, "status": scores.get("evaluation_status"),
                            "cefr": scores.get("cefr_level"),
                            "score": scores.get("overall_score"),
                            "attempts": attempt}}
                # A local-fallback answer is not progress; try again in case Gemini
                # recovers, rather than accepting the same non-verdict.
                if scores.get("cefr_level") or attempt == ATTEMPTS:
                    break
            except Exception as exc:
                outcome = {{"token": token, "status": f"ERROR {{type(exc).__name__}}",
                            "cefr": None, "score": None, "attempts": attempt}}
            if attempt < ATTEMPTS:
                await asyncio.sleep(PAUSE)
        results.append(outcome)
    print("RESULT:" + json.dumps(results))

asyncio.run(main())
'''
    return extract(run_in_voicehr(code))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually re-evaluate")
    ap.add_argument("--include-insufficient", action="store_true",
                    help="also retry insufficient_evidence reports (usually correct as-is)")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="retry only the first N reports. Useful when the available quota "
                         "window is small: spending it on a few candidates that end up "
                         "with real verdicts beats spreading it across all of them and "
                         "getting none")
    ap.add_argument("--attempts", type=int, default=2,
                    help="attempts per report before accepting a non-verdict")
    ap.add_argument("--pause", type=float, default=10.0,
                    help="seconds between attempts, to let a transient 503 clear")
    args = ap.parse_args()

    statuses = RETRYABLE + (OPTIONAL if args.include_insufficient else ())
    targets = list_targets(statuses)
    if args.limit:
        targets = targets[:args.limit]

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"\nRetry reports without a CEFR verdict  [{mode}]\n")
    print(f"  selecting status in: {statuses}")
    print(f"  'completed' reports are never selected -- see this script's docstring\n")

    if not targets:
        print("  Nothing to retry: every report already carries a verdict.")
        return 0

    print(f"  {'candidate':<26}{'status':<22}cefr")
    for target in targets:
        print(f"  {str(target['candidate_name'])[:25]:<26}{target['evaluation_status']:<22}"
              f"{target['cefr_level'] or '-'}")

    if not args.apply:
        print(f"\n  {len(targets)} report(s) would be re-evaluated. Re-run with --apply.")
        print("  Check Gemini is serving first, or they will all fall back again:")
        print("    python scripts/check_llm_fallback.py --generate")
        return 0

    print(f"\n  re-evaluating {len(targets)} report(s), up to {args.attempts} "
          f"attempt(s) each ...", flush=True)
    results = retry([t["report_token"] for t in targets], args.attempts, args.pause)

    by_token = {t["report_token"]: t["candidate_name"] for t in targets}
    print(f"\n  {'candidate':<26}{'status':<22}{'cefr':<6}score")
    upgraded = 0
    for result in results:
        if result.get("cefr"):
            upgraded += 1
        print(f"  {str(by_token.get(result['token'], '?'))[:25]:<26}"
              f"{str(result['status']):<22}{str(result.get('cefr') or '-'):<6}"
              f"{result.get('score') or '-'}")

    print(f"\n  now carrying a CEFR verdict: {upgraded}/{len(results)}")
    if upgraded < len(results):
        print("  The rest fell back to the local model again -- Gemini is still "
              "unavailable or out of quota.")
        print("  Nothing was lost: these had no verdict before either. Re-run later.")
    else:
        print("  Re-deliver them to RWS without spending more quota:")
        print("    see scripts/seed_mock_interviews.py's notes, or use "
              "rws_client.scores_from_report_row")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
