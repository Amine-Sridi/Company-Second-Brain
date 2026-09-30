"""
Tests for backend/core/reconcile.py -- orphaned recording/transcript collection.

Run against a real temporary SQLite database and real temporary directories, not mocks,
because every interesting property here is about the interaction between what is on disk
and what is in the database.

The tests that matter most are the ones proving what is *not* collected. A recording file
exists before its `recordings` row does, so "unreferenced" is the normal state of a
live interview -- the failure mode of this module is deleting a candidate's voice
mid-interview, not leaving a file behind.
"""
import os
import sqlite3
from datetime import datetime, timedelta

import pytest

from backend.core.reconcile import reconcile


NOW = datetime(2026, 9, 29, 12, 0, 0)
CUTOFF = (NOW - timedelta(days=90)).isoformat()
OLD = (NOW - timedelta(days=200)).isoformat()      # past the cutoff
RECENT = (NOW - timedelta(days=2)).isoformat()     # inside the cutoff


@pytest.fixture
def storage(tmp_path):
    for d in ("recordings", "transcripts"):
        (tmp_path / d).mkdir()
        (tmp_path / d / ".gitkeep").write_text("")
    return tmp_path


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE interviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            candidate_name TEXT NOT NULL,
            candidate_email TEXT NOT NULL,
            language TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            retry_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            interview_id INTEGER NOT NULL,
            recording_path TEXT,
            transcript_path TEXT,
            duration_seconds INTEGER,
            ended_reason TEXT,
            raw_transcript TEXT
        );
        """
    )
    yield connection
    connection.close()


def add_interview(conn, interview_id, status, created_at):
    conn.execute(
        "INSERT INTO interviews (id, token, candidate_name, candidate_email, language,"
        " status, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
        (interview_id, f"tok{interview_id}", "Someone", "s@example.com", "en",
         status, created_at, created_at),
    )
    conn.commit()


def add_recording_row(conn, interview_id, recording=True, transcript=True):
    conn.execute(
        "INSERT INTO recordings (interview_id, recording_path, transcript_path)"
        " VALUES (?,?,?)",
        (interview_id,
         f"storage/recordings/{interview_id}.webm" if recording else None,
         f"storage/transcripts/{interview_id}.json" if transcript else None),
    )
    conn.commit()


def write_file(storage, directory, name, age_days=200):
    path = storage / directory / name
    path.write_bytes(b"audio-bytes")
    stamp = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


# --------------------------------------------------------------------------- safety

def test_active_interview_recording_is_never_collected(conn, storage):
    """The core hazard: a live interview's file exists and has no row yet. Age must not
    be able to override this -- an interview could be conducted from an old invite."""
    add_interview(conn, 1, "active", OLD)
    path = write_file(storage, "recordings", "1.webm")

    report = reconcile(conn, str(storage), CUTOFF, apply=True)

    assert path.exists()
    assert report["collected"] == 0


def test_pending_interview_inside_the_cutoff_is_kept(conn, storage):
    """A pending interview can be resumed through the reconnect-recovery path while its
    invite link is still valid."""
    add_interview(conn, 2, "pending", RECENT)
    path = write_file(storage, "recordings", "2.webm", age_days=2)

    reconcile(conn, str(storage), CUTOFF, apply=True)

    assert path.exists()


def test_referenced_file_is_never_collected_even_when_ancient(conn, storage):
    """retention.py owns referenced files. Collecting one here would delete data a row
    still points at, creating the dangling pointer this module exists to clean up."""
    add_interview(conn, 3, "completed", OLD)
    add_recording_row(conn, 3)
    rec = write_file(storage, "recordings", "3.webm")
    tra = write_file(storage, "transcripts", "3.json")

    report = reconcile(conn, str(storage), CUTOFF, apply=True)

    assert rec.exists() and tra.exists()
    assert report["collected"] == 0


def test_gitkeep_is_never_collected(conn, storage):
    reconcile(conn, str(storage), CUTOFF, apply=True)
    assert (storage / "recordings" / ".gitkeep").exists()
    assert (storage / "transcripts" / ".gitkeep").exists()


def test_unreferenced_but_recent_file_with_no_row_is_kept(conn, storage):
    """No interview row to date it from, so the file's own mtime decides. This is also
    what stops a file still being written from being collected mid-run."""
    path = write_file(storage, "recordings", "999.webm", age_days=1)

    reconcile(conn, str(storage), CUTOFF, apply=True)

    assert path.exists()


def test_dry_run_deletes_nothing_but_still_reports(conn, storage):
    add_interview(conn, 4, "completed", OLD)
    path = write_file(storage, "recordings", "4.webm")

    report = reconcile(conn, str(storage), CUTOFF, apply=False)

    assert path.exists()
    assert report["collected"] == 1
    assert report["bytes_freed"] > 0


# ----------------------------------------------------------------------- collection

def test_completed_interview_past_cutoff_with_no_row_is_collected(conn, storage):
    """The retention gap: the interview finished long ago but no recordings row was ever
    inserted, so retention.sweep can never see this file."""
    add_interview(conn, 5, "completed", OLD)
    path = write_file(storage, "recordings", "5.webm")

    report = reconcile(conn, str(storage), CUTOFF, apply=True)

    assert not path.exists()
    assert report["collected"] == 1


def test_abandoned_pending_interview_past_cutoff_is_collected(conn, storage):
    """Past any cutoff of a week or more the invite link has expired, so this can no
    longer be resumed by anyone -- this is the bulk of real accumulated orphans."""
    add_interview(conn, 6, "pending", OLD)
    path = write_file(storage, "recordings", "6.webm")

    report = reconcile(conn, str(storage), CUTOFF, apply=True)

    assert not path.exists()
    assert report["collected"] == 1


def test_file_with_no_interview_row_at_all_is_collected(conn, storage):
    """The interview row was deleted, or the file was never ours. Either way nothing can
    reference it again."""
    path = write_file(storage, "transcripts", "777.json")

    report = reconcile(conn, str(storage), CUTOFF, apply=True)

    assert not path.exists()
    assert report["collected"] == 1


def test_non_numeric_filename_is_handled_by_mtime(conn, storage):
    """Filenames are interview ids, but a stray file must not crash the sweep."""
    old = write_file(storage, "recordings", "stray-file.webm")
    new = write_file(storage, "recordings", "fresh-file.webm", age_days=1)

    report = reconcile(conn, str(storage), CUTOFF, apply=True)

    assert not old.exists()
    assert new.exists()
    assert report["collected"] == 1


def test_partial_row_protects_only_the_path_it_holds(conn, storage):
    """A row with a recording but no transcript path protects the recording only."""
    add_interview(conn, 7, "completed", OLD)
    add_recording_row(conn, 7, recording=True, transcript=False)
    rec = write_file(storage, "recordings", "7.webm")
    tra = write_file(storage, "transcripts", "7.json")

    reconcile(conn, str(storage), CUTOFF, apply=True)

    assert rec.exists(), "referenced recording must survive"
    assert not tra.exists(), "unreferenced transcript past the cutoff must be collected"


def test_report_totals_are_consistent(conn, storage):
    add_interview(conn, 8, "completed", OLD)
    add_interview(conn, 9, "active", OLD)
    write_file(storage, "recordings", "8.webm")
    write_file(storage, "recordings", "9.webm")

    report = reconcile(conn, str(storage), CUTOFF, apply=False)

    # 2 recordings + 2 .gitkeep files
    assert report["scanned"] == 4
    assert report["collected"] + report["kept"] == report["scanned"]
    assert report["collected"] == 1
    assert report["collected_names"] == ["recordings/8.webm"]


def test_no_database_rows_are_modified(conn, storage):
    """This module deletes files only. Clearing columns is retention.py's job, and doing
    it here too would make the two sweeps' effects hard to reason about."""
    add_interview(conn, 10, "completed", OLD)
    add_recording_row(conn, 10)
    query = "SELECT recording_path, transcript_path FROM recordings"
    before = [tuple(r) for r in conn.execute(query).fetchall()]

    reconcile(conn, str(storage), CUTOFF, apply=True)

    # tuple() on both sides because reconcile sets conn.row_factory = sqlite3.Row (as
    # retention.sweep already does), so the second fetch returns Row objects; Row does
    # not compare equal to a plain tuple even with identical values.
    after = [tuple(r) for r in conn.execute(query).fetchall()]
    assert before == after
