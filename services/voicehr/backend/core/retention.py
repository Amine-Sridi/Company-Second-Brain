"""
backend/core/retention.py
--------------------------
Age-based removal of candidate PII, keeping the recruiter's decision record.

This is **retention / data minimisation, not GDPR erasure-on-request**, which
`CLAUDE.md` explicitly defers. It acts on age, never on a named candidate, so it needs
none of the cross-service identity resolution a right-to-be-forgotten flow requires.
Nothing schedules it: `scripts/retention_sweep.py` invokes it, dry-run by default.

The split between what goes and what stays is the design:

  REMOVED   the recorded voice, the transcript file, the verbatim transcript text,
            and the candidate's name/email
  KEPT      the reports row (scores, CEFR, evaluation_status), the interviews row,
            its status and external_reference_id

Deleting the decision record to protect privacy would trade one accountability problem
for another -- a rejected candidate asking "why?" deserves an answer. So the decision
survives and the raw material around it does not.

Lives here rather than inside the script so it can be tested against a real temporary
database (see tests/test_retention.py); an embedded SQL string in a CLI cannot be.
"""
from __future__ import annotations

import os
import sqlite3

# candidate_name is NOT NULL in the schema, so identity is overwritten rather than
# nulled. A visible marker also distinguishes "redacted deliberately" from "never
# captured", which matters when someone reads the row a year later.
REDACTED_NAME = "[redacted]"
REDACTED_EMAIL = "redacted@invalid"

# Only finished interviews are eligible. A pending or active one is live work, and one
# that never completed may belong to a candidate who is about to sit it.
ELIGIBLE_STATUSES = ("completed", "inconclusive", "terminated")


def sweep(conn: sqlite3.Connection, cutoff_iso: str, apply: bool) -> dict:
    """
    Returns a report of what was (or would be) removed.

    `apply=False` changes nothing -- the caller can show the report first. This is the
    default everywhere, because an irreversible delete driven by a date argument is
    exactly the kind of command that should refuse to act by accident.
    """
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in ELIGIBLE_STATUSES)
    rows = conn.execute(
        f"""SELECT i.id, i.candidate_name,
                   r.recording_path, r.transcript_path,
                   (r.raw_transcript IS NOT NULL AND r.raw_transcript != '') AS has_text
            FROM interviews i
            LEFT JOIN recordings r ON r.interview_id = i.id
            WHERE i.created_at < ?
              AND i.status IN ({placeholders})""",
        (cutoff_iso, *ELIGIBLE_STATUSES),
    ).fetchall()

    report = {
        "eligible_interviews": len(rows),
        "recording_files": 0,
        "transcript_files": 0,
        "raw_transcripts": 0,
        "identities": 0,
        "missing_files": 0,
        "bytes_freed": 0,
    }

    for row in rows:
        for column, counter in (("recording_path", "recording_files"),
                                ("transcript_path", "transcript_files")):
            path = row[column]
            if not path:
                continue
            if os.path.exists(path):
                report["bytes_freed"] += os.path.getsize(path)
                report[counter] += 1
                if apply:
                    try:
                        os.unlink(path)
                    except OSError:
                        # A file we cannot remove must not abort the sweep: the column is
                        # still cleared below, and leaving the rest of the batch untouched
                        # would be worse than one stranded file.
                        pass
            else:
                # Already gone. The column is still cleared, so the row stops pointing at
                # a file that does not exist.
                report["missing_files"] += 1

        if row["has_text"]:
            report["raw_transcripts"] += 1
        if row["candidate_name"] != REDACTED_NAME:
            report["identities"] += 1

        if apply:
            conn.execute(
                "UPDATE recordings SET recording_path=NULL, transcript_path=NULL, "
                "raw_transcript=NULL WHERE interview_id=?",
                (row["id"],),
            )
            conn.execute(
                "UPDATE interviews SET candidate_name=?, candidate_email=? WHERE id=?",
                (REDACTED_NAME, REDACTED_EMAIL, row["id"]),
            )

    if apply:
        conn.commit()
    return report
