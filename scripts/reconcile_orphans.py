"""
scripts/reconcile_orphans.py
-----------------------------
Collects candidate voice/transcript files that no `recordings` row points at.

This is the complement to `scripts/retention_sweep.py`, not a replacement for it:

  retention_sweep.py    walks *rows* and removes the files they reference, then
                        redacts identity. Age-based, keeps the decision record.
  reconcile_orphans.py  walks *files* and removes the ones no row references at all.

The gap it closes is structural. `session.py` opens `storage/recordings/{id}.webm` and
writes to it *during* the interview, while `process_interview` only INSERTs the
`recordings` row at the very end -- so any interview that does not reach that INSERT
(a disconnect, a crash, an abandoned invite) leaves a voice recording that the retention
sweep can never see, because it has no row to find it from.

**Why this is not just `find -delete`.** Because the file exists before the row does,
"unreferenced" is the normal state of an interview that is happening right now. A file is
only collected when it is unreferenced *and* its interview is not `active` *and* it is
past the cutoff (or, with no interview row at all, its mtime is). The reasoning is in
`backend/core/reconcile.py`'s docstring; the invariants have tests in
`tests/test_reconcile.py`.

Like the retention sweep, it is **explicitly invoked** -- nothing schedules it -- and it
is **dry-run by default**: `--apply` is required to delete anything. It modifies no
database rows whatsoever.

Run:
    python scripts/reconcile_orphans.py --older-than-days 90            # dry run
    python scripts/reconcile_orphans.py --older-than-days 90 --apply
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone

CONTAINER = "csb-voicehr"
# Inside the container this is the voicehr_data volume mount point. The host copy of
# services/voicehr/storage is NOT the live data (see handoff.md), so the work happens in
# there rather than reaching into the volume from the host.
STORAGE_ROOT = "storage"


def _run_in_container(python_code: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "python", "-c", python_code],
        capture_output=True, text=True, timeout=600,
    )
    if out.returncode != 0:
        raise SystemExit(f"container command failed:\n{out.stderr[-800:]}")
    return out.stdout.strip()


def reconcile(cutoff_iso: str, apply: bool) -> dict:
    code = f"""
import json, sqlite3
import backend.database as d
from backend.core.reconcile import reconcile as run

conn = sqlite3.connect(d.DB_PATH)
try:
    print(json.dumps(run(conn, {STORAGE_ROOT!r}, {cutoff_iso!r}, {apply!r})))
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
    ap.add_argument(
        "--list", action="store_true",
        help="print every file that would be (or was) collected, not just the counts",
    )
    args = ap.parse_args()

    # 7 days is the candidate invite link's own lifetime (api/routes/invite.py). Below
    # that a `pending` interview may still legitimately be resumed, and its recording is
    # live data -- reconcile.py keeps those regardless, so a smaller value would collect
    # nothing extra while making the intent unclear.
    if args.older_than_days < 7:
        raise SystemExit("--older-than-days must be >= 7 (the invite link's own lifetime)")

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.older_than_days)
    cutoff_iso = cutoff.replace(tzinfo=None).isoformat()

    mode = "APPLY -- files will be deleted" if args.apply else "DRY RUN -- nothing deleted"
    print(f"\nOrphan reconciliation  [{mode}]")
    print(f"  cutoff: files/interviews older than {cutoff_iso}\n")

    report = reconcile(cutoff_iso, args.apply)

    print(f"  files scanned                : {report['scanned']}")
    print(f"  kept                         : {report['kept']}")
    print(f"  collected                    : {report['collected']}")
    print(f"  deletions that failed        : {report['failed']}")
    print(f"  disk to free                 : {report['bytes_freed'] / 1024:.1f} KB")

    if report["by_reason"]:
        print("\n  breakdown:")
        for reason, count in sorted(report["by_reason"].items(), key=lambda kv: -kv[1]):
            print(f"    {count:>4}  {reason}")

    if args.list and report["collected_names"]:
        print("\n  collected files:")
        for name in report["collected_names"]:
            print(f"    {name}")

    if not args.apply:
        print("\n  Nothing was changed. Re-run with --apply to collect these files.")
    else:
        print("\n  Done. No database rows were modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
