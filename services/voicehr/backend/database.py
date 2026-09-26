import sqlite3

# "storage/" is the one directory Docker actually persists for this service
# (infra/docker-compose.yml mounts the named volume voicehr_data at /app/storage;
# the Dockerfile creates storage/recordings and storage/transcripts under it at
# build time, so the parent directory always exists before this path is opened).
# The database previously lived at a bare "voicehr.db" -- relative to the
# container's CWD (/app/), which is *outside* that volume -- so every container
# recreate silently reset every interview/report to empty. Moving it here is the
# entire fix: same SQLite file, same schema, same every other line in this
# module; only where it physically lives on disk changes.
DB_PATH = "storage/voicehr.db"

# Candidate gets up to this many automatic reconnects after a recoverable disconnect
# (browser_disconnect / gemini_live_error) before the interview stops offering a fresh
# `/start` attempt -- shared by api/routes/session.py (WebSocket-side disconnect
# handling) and api/deps.py (validate_interview_start's stale-active reactivation), so
# both halves of "reconnect after a drop" enforce the exact same bounded budget instead
# of two independently-unbounded fallbacks (the bug this constant replaces: the old
# code capped session.py's own reset at 1, but deps.py's 30-second staleness check had
# no cap at all, silently undermining it for any reconnect attempted >30s after the
# most recent /start call -- true for almost every real disconnect).
MAX_INTERVIEW_RECONNECTS = 2


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    existing_interviews = cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'interviews'"
    ).fetchone()
    if existing_interviews and "'terminated'" not in (existing_interviews[0] or ""):
        cursor.execute("ALTER TABLE interviews RENAME TO interviews_legacy")
        cursor.execute("""
            CREATE TABLE interviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                candidate_name TEXT NOT NULL,
                candidate_email TEXT NOT NULL,
                language TEXT NOT NULL CHECK(language IN ('en', 'fr')),
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'active', 'completed', 'inconclusive', 'terminated')),
                retry_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                invite_sent INTEGER DEFAULT 0
            )
        """)
        cursor.execute(
            """INSERT INTO interviews
               SELECT id, token, candidate_name, candidate_email, language, status,
                      retry_count, created_at, expires_at, started_at, completed_at
               FROM interviews_legacy"""
        )
        cursor.execute("DROP TABLE interviews_legacy")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            candidate_name TEXT NOT NULL,
            candidate_email TEXT NOT NULL,
            language TEXT NOT NULL CHECK(language IN ('en', 'fr')),
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'active', 'completed', 'inconclusive', 'terminated')),
            retry_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            invite_sent INTEGER DEFAULT 0
        )
    """)

    interview_columns = {row[1] for row in cursor.execute("PRAGMA table_info(interviews)").fetchall()}
    if "invite_sent" not in interview_columns:
        try:
            cursor.execute("ALTER TABLE interviews ADD COLUMN invite_sent INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

    # RWS integration columns. external_reference_id stores/echoes RWS's application_id;
    # NULL means this interview was created outside the RWS flow (e.g. the admin UI), and
    # such interviews never trigger the outbound webhook. idempotency_key is RWS's
    # "{application_id}:{attempt_number}" value, used to make repeated invite calls with
    # the same key return the existing interview instead of creating a duplicate.
    for column, ddl in (
        ("external_reference_id", "TEXT"),
        ("idempotency_key", "TEXT"),
        ("webhook_delivery_id", "TEXT"),
        ("webhook_delivered_at", "TEXT"),
        # RWS's Job.title, for the candidate email only (see api/routes/invite.py) --
        # not read by anything scoring/matching-related, which VoiceHR has no concept of.
        ("job_title", "TEXT"),
        # Automatic candidate invitation email delivery (see api/routes/invite.py:
        # create_invite / deliver_invitation_email). NULL means "not yet delivered" --
        # this is exactly what makes a duplicate POST /api/invite with the same
        # Idempotency-Key safe to retry (delivery failed last time) without resending
        # once it has actually succeeded.
        ("invite_email_sent_at", "TEXT"),
        ("invite_email_error", "TEXT"),
        # Session-robustness columns (candidate reconnect/completion-idempotency).
        # pending_transcript/pending_duration_seconds hold whatever transcript/elapsed
        # time a recoverable disconnect (browser_disconnect/gemini_live_error) captured
        # before the drop -- api/routes/session.py loads them on the next WebSocket
        # connect and hands them to the Gemini Live pipeline as resume_transcript/
        # resume_duration_seconds, so a reconnect's final evaluated transcript/duration
        # covers the whole conversation, not just the last leg. Cleared once the
        # interview actually finalizes (see finalized_at below).
        ("pending_transcript", "TEXT"),
        ("pending_duration_seconds", "REAL"),
        # Set exactly once, atomically, by processing.process_interview's own claim
        # UPDATE ("WHERE finalized_at IS NULL") -- the actual exactly-once completion
        # guard. A concurrent/duplicate finalization attempt (e.g. a WebSocket close
        # racing a stale-session reconnect) sees rowcount == 0 on that UPDATE and
        # returns immediately, before any evaluation work or webhook send happens.
        # Independent of `status`, which still records the human-facing outcome.
        ("finalized_at", "TEXT"),
    ):
        if column not in interview_columns:
            try:
                cursor.execute(f"ALTER TABLE interviews ADD COLUMN {column} {ddl}")
            except sqlite3.OperationalError:
                pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            interview_id INTEGER NOT NULL,
            recording_path TEXT,
            transcript_path TEXT,
            duration_seconds INTEGER,
            ended_reason TEXT,
            raw_transcript TEXT,
            FOREIGN KEY (interview_id) REFERENCES interviews(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            interview_id INTEGER NOT NULL UNIQUE,
            report_token TEXT UNIQUE NOT NULL,
            overall_score INTEGER,
            cefr_level TEXT,
            confidence TEXT,
            inconclusive INTEGER NOT NULL DEFAULT 0,
            fluency_score INTEGER,
            vocabulary_score INTEGER,
            grammar_score INTEGER,
            pronunciation_score INTEGER,
            comprehension_score INTEGER,
            strengths TEXT,
            concerns TEXT,
             evidence TEXT,
             candidate_speaking_time INTEGER,
             word_count INTEGER,
             evaluation_status TEXT NOT NULL DEFAULT 'completed',
             evaluation_error TEXT,
             created_at TEXT NOT NULL,
             FOREIGN KEY (interview_id) REFERENCES interviews(id)
         )
     """)

    # Add evaluator state to databases created before hosted evaluation was
    # introduced, then classify existing null-score reports for retry.
    report_columns = {row[1] for row in cursor.execute("PRAGMA table_info(reports)").fetchall()}
    if "evaluation_status" not in report_columns:
        cursor.execute("ALTER TABLE reports ADD COLUMN evaluation_status TEXT NOT NULL DEFAULT 'pending'")
    if "evaluation_error" not in report_columns:
        cursor.execute("ALTER TABLE reports ADD COLUMN evaluation_error TEXT")
    # Interaction/spontaneity is now scored as its own rubric dimension (see
    # backend/services/evaluator.py) -- distinct from comprehension, which it used to
    # be silently folded into. VoiceHR-internal only: never sent over the RWS webhook
    # (shared/schemas.InterviewAssessment is unchanged), only surfaced via VoiceHR's
    # own rich report (backend/api/routes/report.py: build_report), which the recruiter
    # frontend's AssessmentScoreRow already renders "--" for gracefully when absent
    # (exactly how a null/missing score already displays elsewhere).
    if "interaction_score" not in report_columns:
        cursor.execute("ALTER TABLE reports ADD COLUMN interaction_score INTEGER")
    cursor.execute(
        """UPDATE reports SET evaluation_status = CASE
               WHEN overall_score IS NULL THEN 'failed' ELSE 'completed' END
           WHERE evaluation_status = 'pending'"""
    )

    conn.commit()
    conn.close()
