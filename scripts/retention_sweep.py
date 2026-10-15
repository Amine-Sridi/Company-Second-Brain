"""
scripts/retention_sweep.py
---------------------------
Removes aged candidate PII from VoiceHR while keeping the recruiter's decision record.

**Read this before running it.** `docs/ARCHITECTURE_DECISIONS.md` explicitly defers "GDPR erasure automation",
and this deliberately is not that:

* It is **retention / data minimisation**, not erasure-on-request. It acts on *age*, not
  on a named candidate, so it needs none of the cross-service identity resolution that a
  real right-to-be-forgotten flow requires.
* It is **explicitly invoked**. Nothing schedules it -- no APScheduler job, no cron. A
  human runs it, and it is **dry-run by default**: `--apply` is required to delete
  anything.

What it removes, and what it keeps, is the whole design:

  REMOVED   the candidate's recorded voice (storage/recordings/*.webm)
            the transcript file (storage/transcripts/*.json)
            recordings.raw_transcript  (verbatim text of what they said)
            interviews.candidate_name / candidate_email

  KEPT      the reports row -- scores, CEFR level, evaluation_status
            the interviews row, its status and external_reference_id
            everything RWS needs to explain a past decision

That split matters. The heavy, sensitive payload is the voice and the verbatim
transcript; the thing an auditor (or a rejected candidate) may legitimately ask about is
the *decision*. Deleting the decision record to protect privacy would trade one
accountability problem for another, so this keeps it and strips what surrounds it.

Identity fields are overwritten with a marker rather than set NULL: `candidate_name` is
NOT NULL in the schema, and a visible "[redacted]" tells the next reader this was a
deliberate act rather than missing data.

Run:
    python scripts/retention_sweep.py --older-than-days 90            # dry run
    python scripts/retention_sweep.py --older-than-days 90 --apply
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

CONTAINER = "csb-voicehr"
REDACTED_NAME = "[redacted]"
REDACTED_EMAIL = "redacted@invalid"


def _run_in_container(python_code: str) -> str:
    """VoiceHR's SQLite lives on a volume only its container mounts, so all work happens
    in there rather than reaching into the volume from the host."""
    out = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "python", "-c", python_code],
        capture_output=True, text=True, timeout=600,
    )
    if out.returncode != 0:
        raise SystemExit(f"container command failed:\n{out.stderr[-800:]}")
    return out.stdout.strip()


def sweep(cutoff_iso: str, apply: bool) -> dict:
    """Runs the sweep inside the VoiceHR container.

    The logic itself lives in `backend/core/retention.py` so it can be tested against a
    real temporary database -- this only supplies the connection and the arguments.
    VoiceHR's SQLite is on a volume only its own container mounts, which is why the work
    happens in there rather than reaching into the volume from the host.
    """
    code = f"""
import json, sqlite3
import backend.database as d
from backend.core.retention import sweep as run_sweep

conn = sqlite3.connect(d.DB_PATH)
try:
    print(json.dumps(run_sweep(conn, {cutoff_iso!r}, {apply!r})))
finally:
    conn.close()
"""
    return json.loads(_run_in_container(code))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--older-than-days", type=int, default=90)
    ap.add_argument(
        "--apply", action="store_true",
        help="actually delete; without this the script only reports (default)",
    )
    args = ap.parse_args()

    if args.older_than_days < 1:
        raise SystemExit("--older-than-days must be >= 1")

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.older_than_days)
    cutoff_iso = cutoff.replace(tzinfo=None).isoformat()

    mode = "APPLY -- data will be deleted" if args.apply else "DRY RUN -- nothing deleted"
    print(f"\nRetention sweep  [{mode}]")
    print(f"  cutoff: interviews created before {cutoff_iso}\n")

    report = sweep(cutoff_iso, args.apply)

    print(f"  eligible finished interviews : {report['eligible_interviews']}")
    print(f"  recording files              : {report['recording_files']}")
    print(f"  transcript files             : {report['transcript_files']}")
    print(f"  stored transcript texts      : {report['raw_transcripts']}")
    print(f"  identities to redact         : {report['identities']}")
    print(f"  paths already missing        : {report['missing_files']}")
    print(f"  disk to free                 : {report['bytes_freed'] / 1024:.1f} KB")

    if not args.apply:
        print("\n  Nothing was changed. Re-run with --apply to perform the sweep.")
    else:
        print("\n  Done. Scores, CEFR levels and decision records were kept.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
