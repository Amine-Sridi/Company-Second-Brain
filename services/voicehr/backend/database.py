import sqlite3

DB_PATH = "voicehr.db"

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
    cursor.execute(
        """UPDATE reports SET evaluation_status = CASE
               WHEN overall_score IS NULL THEN 'failed' ELSE 'completed' END
           WHERE evaluation_status = 'pending'"""
    )

    conn.commit()
    conn.close()
