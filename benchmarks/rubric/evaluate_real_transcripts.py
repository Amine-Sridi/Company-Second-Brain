"""
benchmarks/rubric/evaluate_real_transcripts.py
-----------------------------------------------
Runs VoiceHR's real evaluator over real interview transcripts.

Why this is possible at all: the evaluator is **text-only**. It never receives audio --
which is why `pronunciation_score` is permanently null -- so a stored transcript is the
complete input. Calibrating the rubric therefore does not require conducting a live
interview, only a real one that already happened.

**PII handling.** The transcripts are real people speaking. They live in
`services/voicehr/storage/transcripts/`, which is gitignored, and this script never
prints or writes a single word of their content: output is scores, counts and derived
metrics keyed by the transcript's numeric id. Results are written next to the
transcripts, i.e. also outside git. Nothing verbatim reaches the repository, the handoff
or the report.

What it can and cannot establish depends entirely on how much the candidate actually
said, which is why `--survey` exists and is worth running first.

Run:
    python benchmarks/rubric/evaluate_real_transcripts.py --survey
    python benchmarks/rubric/evaluate_real_transcripts.py --run
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

CONTAINER = "csb-voicehr"
# Inside the container these are on the voicehr_data volume; on the host they are under
# services/voicehr/storage. The evaluator runs in the container, so the transcripts are
# passed in rather than read from a path.
HOST_TRANSCRIPTS = Path("services/voicehr/storage/transcripts")

# The host directory is NOT the live data: VoiceHR's storage is a Docker volume, and the
# host copy drifted behind it (10 files vs 16 in the container -- and the single richest
# transcript, 2713 characters of candidate speech, existed only in the container). A
# survey run against the host therefore understated the usable set. Reading from the
# container is the default for that reason; --host-transcripts keeps the old behaviour
# for when the stack is down.
CONTAINER_TRANSCRIPTS = "storage/transcripts"

# Below roughly this much candidate speech, a CEFR verdict is not defensible and the
# evaluator is expected to withhold one. Used only to label the survey, never to filter:
# the thin transcripts are themselves a real test of the withholding logic.
SUBSTANTIAL_USER_CHARS = 800


def load_transcripts_from_container() -> dict[str, list]:
    """Reads the transcripts the evaluator itself would see.

    A triple-quoted program rather than concatenated escaped lines: the escaping is the
    part that breaks, and this version is also readable.
    """
    code = f'''
import json, os
d = {CONTAINER_TRANSCRIPTS!r}
out = {{}}
for f in sorted(os.listdir(d)):
    if not f.endswith(".json"):
        continue
    try:
        t = json.load(open(os.path.join(d, f), encoding="utf-8"))
    except Exception:
        continue
    if isinstance(t, list):
        out[f[:-5]] = t
print("T:" + json.dumps(out))
'''
    out = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "python", "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    if out.returncode != 0:
        raise SystemExit(f"could not read transcripts from {CONTAINER}:\n{out.stderr[-400:]}")
    for line in out.stdout.splitlines():
        if line.startswith("T:"):
            return json.loads(line[2:])
    raise SystemExit("no transcripts returned from the container")


def load_transcripts() -> dict[str, list]:
    out: dict[str, list] = {}
    for path in sorted(HOST_TRANSCRIPTS.glob("*.json"),
                       key=lambda p: int(p.stem) if p.stem.isdigit() else 0):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(data, list):
            out[path.stem] = data
    return out


def survey(transcripts: dict[str, list]) -> None:
    print(f"\n{len(transcripts)} real transcripts\n")
    print(f"  {'id':<6}{'turns':>7}{'user turns':>12}{'user chars':>12}   expectation")
    substantial = 0
    for tid, turns in transcripts.items():
        user = [t for t in turns if isinstance(t, dict) and t.get("role") == "user"]
        chars = sum(len(str(t.get("text", ""))) for t in user)
        if not user:
            note = "no candidate speech -> abandoned"
        elif chars >= SUBSTANTIAL_USER_CHARS:
            note = "enough to score"
            substantial += 1
        else:
            note = "thin -> expect withheld verdict"
        print(f"  {tid:<6}{len(turns):>7}{len(user):>12}{chars:>12}   {note}")
    print(f"\n  scoreable: {substantial}/{len(transcripts)}")
    if substantial < 3:
        print("\n  NOTE: too few substantial transcripts to calibrate CEFR accuracy.")
        print("  What this set CAN validate is the insufficient-evidence path against")
        print("  real speech, which until now was only covered by hand-written cases.")


def evaluate_in_container(transcripts: dict[str, list]) -> list[dict]:
    """Runs the real evaluator inside the VoiceHR container, one transcript at a time."""
    payload = json.dumps(transcripts)
    code = '''
import asyncio, json, sys
from backend.services.evaluator import evaluate

transcripts = json.loads(sys.stdin.read())

async def main():
    results = []
    for tid, turns in transcripts.items():
        try:
            r = await evaluate(turns, language="en", inconclusive=False,
                               duration_seconds=max(60.0, len(turns) * 20.0))
            results.append({
                "id": tid,
                "overall_score": r.get("overall_score"),
                "cefr_level": r.get("cefr_level"),
                "confidence": r.get("confidence"),
                "evaluation_status": r.get("evaluation_status"),
                # evaluate() returns normally with status "failed" and the reason in
                # "error"; without capturing it a failed run leaves no evidence of why.
                "evaluation_error": r.get("error"),
                "inconclusive": r.get("inconclusive"),
                "fluency": r.get("fluency_score"),
                "vocabulary": r.get("vocabulary_score"),
                "grammar": r.get("grammar_score"),
                "comprehension": r.get("comprehension_score"),
                "interaction": r.get("interaction_score"),
                "pronunciation": r.get("pronunciation_score"),
                "word_count": r.get("word_count"),
            })
        except Exception as exc:
            results.append({"id": tid, "error": type(exc).__name__})
    print("RESULTS_JSON:" + json.dumps(results))

asyncio.run(main())
'''
    out = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "python", "-c", code],
        input=payload, capture_output=True, text=True, timeout=3600,
    )
    if out.returncode != 0:
        raise SystemExit(f"evaluator run failed:\n{out.stderr[-1000:]}")
    for line in out.stdout.splitlines():
        if line.startswith("RESULTS_JSON:"):
            return json.loads(line[len("RESULTS_JSON:"):])
    raise SystemExit(f"no results returned:\n{out.stdout[-800:]}")


def report(results: list[dict], transcripts: dict[str, list]) -> None:
    print(f"\n{'id':<6}{'status':<24}{'CEFR':<7}{'overall':>8}{'flu':>5}{'voc':>5}"
          f"{'gra':>5}{'com':>5}{'int':>5}")
    print("-" * 74)
    for r in sorted(results, key=lambda x: int(x["id"]) if x["id"].isdigit() else 0):
        if "error" in r:
            print(f"  {r['id']:<6}ERROR: {r['error']}")
            continue
        print(f"  {r['id']:<6}{str(r['evaluation_status']):<24}"
              f"{str(r['cefr_level'] or '-'):<7}{str(r['overall_score'] or '-'):>8}"
              f"{str(r['fluency'] or '-'):>5}{str(r['vocabulary'] or '-'):>5}"
              f"{str(r['grammar'] or '-'):>5}{str(r['comprehension'] or '-'):>5}"
              f"{str(r['interaction'] or '-'):>5}")

    scored = [r for r in results if r.get("cefr_level")]
    withheld = [r for r in results if not r.get("cefr_level") and "error" not in r]
    print(f"\n  scored (CEFR issued) : {len(scored)}")
    print(f"  verdict withheld     : {len(withheld)}")
    print(f"  errors               : {sum(1 for r in results if 'error' in r)}")

    reasons = {}
    for r in results:
        why = r.get("evaluation_error") or (r.get("error") if "error" in r else None)
        if why:
            key = str(why)[:90]
            reasons[key] = reasons.get(key, 0) + 1
    if reasons:
        print("\n  failure reasons (this is what the previous run did not record):")
        for why, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>3}x  {why}")

    bad_pron = [r for r in results if r.get("pronunciation") is not None]
    print(f"  pronunciation non-null (must be 0): {len(bad_pron)}")

    out = HOST_TRANSCRIPTS.parent / "rubric_evaluation_results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n  wrote {out}  (outside git -- storage/ is ignored)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--survey", action="store_true", help="describe the set, call nothing")
    ap.add_argument("--run", action="store_true", help="run the real evaluator (uses Gemini quota)")
    ap.add_argument("--host-transcripts", action="store_true",
                    help="read the host copy instead of the container's (stack down)")
    args = ap.parse_args()

    transcripts = (load_transcripts() if args.host_transcripts
                   else load_transcripts_from_container())
    if not transcripts:
        raise SystemExit(f"no transcripts found in {HOST_TRANSCRIPTS}")

    if args.survey or not args.run:
        survey(transcripts)
        if not args.run:
            print("\n  Re-run with --run to evaluate (one Gemini call per transcript).")
            return 0

    report(evaluate_in_container(transcripts), transcripts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
