"""
backend/core/reconcile.py
--------------------------
Collects candidate voice/transcript files that no `recordings` row points at.

`retention.py` walks *rows* and removes the files they reference. That leaves a gap it
structurally cannot see: a file written for an interview whose `recordings` row was never
inserted at all. Those files are candidate voice data and no retention policy will ever
collect them.

They accumulate for ordinary reasons, not exotic ones:

  * `session.py` opens `storage/recordings/{id}.webm` and writes to it *during* the
    interview, while `process_interview` only INSERTs the `recordings` row at the very
    end. Any interview that does not reach that INSERT leaves the file behind.
  * On `WebSocketDisconnect` the interview goes back to `status='pending'` and
    `process_interview` is never called for that attempt.
  * `retention.sweep` NULLs `recording_path`/`transcript_path` even when `os.unlink`
    raised, so a file it failed to delete becomes unreferenced by design.

**The hazard this module is mostly about.** Because the file exists before the row does,
"unreferenced" and "abandoned" are not the same thing. A naive sweep would delete the
recording of an interview that is *currently being conducted*, or one that is `pending`
and about to be resumed through the reconnect-recovery path. So being unreferenced is
necessary but never sufficient. A file is only collected when all of these hold:

  1. no `recordings` row references its name;
  2. its interview is not `active` -- that is live work, whatever the file's age;
  3. its interview is older than the cutoff, or there is no interview row at all and the
     file's own mtime is older than the cutoff.

Rule 3 is what makes rule 2 safe to relax for `pending`: the candidate's interview link
expires after 7 days (`api/routes/invite.py`), so a `pending` interview past any cutoff
of a week or more cannot be resumed by anyone. Below that, a `pending` interview is
treated as live work and left alone.

The mtime check in rule 3 also covers the case with no row to date from at all, and
guards against a partially written file from a run that is still in progress.

Like `retention.py`, the logic lives here rather than in the CLI so it can be tested
against a real temporary database and real temporary directories.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

# Matches session.py's own layout: two flat directories under storage/.
MANAGED_DIRS = ("recordings", "transcripts")

# Never collected -- the directories are committed with these to exist in a fresh clone.
KEEP_NAMES = {".gitkeep"}

# An interview being conducted right now. Its recording file exists and has no row yet,
# which is exactly the shape of an orphan, so status is checked before age.
LIVE_STATUSES = ("active",)

# Resumable until the invite link expires; see the module docstring's rule 3.
RESUMABLE_STATUSES = ("pending",)


def _referenced_names(conn: sqlite3.Connection) -> set[str]:
    """Every file basename any `recordings` row still points at.

    Compared by basename rather than by resolved path on purpose. The stored paths are
    relative to the container's working directory (`storage/recordings/12.webm`), so
    resolving them against a host-side or test-side root is where an off-by-one in path
    handling would silently turn a referenced file into an orphan. Basenames are what
    both sides agree on, and a broader referenced set can only ever protect an extra
    file -- never expose one.
    """
    names: set[str] = set()
    for column in ("recording_path", "transcript_path"):
        for (path,) in conn.execute(
            f"SELECT {column} FROM recordings WHERE {column} IS NOT NULL AND {column} != ''"
        ):
            names.add(os.path.basename(str(path)))
    return names


def _interview_index(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return {
        row["id"]: row
        for row in conn.execute("SELECT id, status, created_at FROM interviews")
    }


def _interview_id_from(name: str) -> int | None:
    """session.py names both files after the interview id (`12.webm`, `12.json`)."""
    stem = os.path.splitext(name)[0]
    return int(stem) if stem.isdigit() else None


def _classify(
    name: str,
    path: str,
    referenced: set[str],
    interviews: dict[int, sqlite3.Row],
    cutoff_iso: str,
) -> tuple[str, str]:
    """Returns (verdict, reason). Only the verdict "collect" ever deletes anything."""
    if name in KEEP_NAMES:
        return "keep", "directory placeholder"
    if name in referenced:
        return "keep", "referenced by a recordings row"

    interview_id = _interview_id_from(name)
    interview = interviews.get(interview_id) if interview_id is not None else None

    if interview is not None:
        if interview["status"] in LIVE_STATUSES:
            return "keep", f"interview {interview_id} is {interview['status']} (live)"
        if interview["created_at"] >= cutoff_iso:
            status = interview["status"]
            if status in RESUMABLE_STATUSES:
                return "keep", f"interview {interview_id} is pending and still resumable"
            return "keep", f"interview {interview_id} is newer than the cutoff"
        return "collect", f"interview {interview_id} is {interview['status']} and past the cutoff"

    # No interview row to date the file from -- fall back to the file's own mtime, which
    # also stops a file still being written from being collected mid-run.
    try:
        # Naive UTC ISO, matching both interviews.created_at (written with
        # datetime.utcnow().isoformat()) and the cutoff the CLI builds, so the three
        # can be compared as strings.
        mtime = (
            datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
            .replace(tzinfo=None)
            .isoformat()
        )
    except OSError:
        return "keep", "could not stat the file"
    if mtime >= cutoff_iso:
        return "keep", "no interview row, but the file is newer than the cutoff"
    return "collect", "no interview row and the file is past the cutoff"


def reconcile(conn: sqlite3.Connection, storage_root: str, cutoff_iso: str, apply: bool) -> dict:
    """
    Removes unreferenced files under `storage_root` that are safely past the cutoff.

    `apply=False` changes nothing, matching `retention.sweep`: an irreversible delete
    driven by a date argument should refuse to act by accident.

    Touches no database rows at all. Every row that pointed at a file still points at
    it, because a referenced file is never collected -- so this cannot create the
    dangling-pointer problem it exists to clean up after.
    """
    referenced = _referenced_names(conn)
    interviews = _interview_index(conn)

    report = {
        "scanned": 0,
        "collected": 0,
        "kept": 0,
        "bytes_freed": 0,
        "failed": 0,
        "by_reason": {},
        "collected_names": [],
    }

    for directory in MANAGED_DIRS:
        full_dir = os.path.join(storage_root, directory)
        if not os.path.isdir(full_dir):
            continue
        for name in sorted(os.listdir(full_dir)):
            path = os.path.join(full_dir, name)
            if not os.path.isfile(path):
                continue
            report["scanned"] += 1
            verdict, reason = _classify(name, path, referenced, interviews, cutoff_iso)
            report["by_reason"][reason] = report["by_reason"].get(reason, 0) + 1

            if verdict == "keep":
                report["kept"] += 1
                continue

            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            if apply:
                try:
                    os.unlink(path)
                except OSError:
                    # One stranded file must not abort the rest of the batch.
                    report["failed"] += 1
                    continue
            report["collected"] += 1
            report["bytes_freed"] += size
            report["collected_names"].append(f"{directory}/{name}")

    return report
