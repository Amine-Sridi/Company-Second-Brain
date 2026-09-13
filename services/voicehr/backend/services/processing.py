import json
import os
import sqlite3
from datetime import datetime

from backend.core.config import settings
from backend.core.security import generate_report_token
import backend.database as database
from backend.services.evaluator import evaluate
from backend.services.mailer import send_report_email
from backend.services.rws_client import notify_and_record_interview_result


async def process_interview(token: str, transcript: list[dict], audio_path: str | None,
                            duration_seconds: float, ended_reason: str = "completed"):
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
        if not row or row["status"] not in ("active", "pending"):
            return

        interview_id = row["id"]
        terminated = ended_reason in ("candidate_refused", "abusive_language")
        inconclusive = terminated or duration_seconds < 300 or sum(
            1 for turn in transcript if turn.get("role") in ("user", "candidate")
        ) < 3
        os.makedirs("storage/transcripts", exist_ok=True)
        transcript_path = f"storage/transcripts/{interview_id}.json"
        with open(transcript_path, "w", encoding="utf-8") as transcript_file:
            json.dump(transcript, transcript_file, ensure_ascii=False)

        scores = await evaluate(
            transcript,
            language=row["language"],
            inconclusive=inconclusive,
            duration_seconds=duration_seconds,
        )
        report_token = generate_report_token()
        now = datetime.utcnow().isoformat()
        status = "terminated" if terminated else ("inconclusive" if inconclusive else "completed")

        conn.execute(
            """INSERT INTO reports (interview_id, report_token, overall_score, cefr_level, confidence,
               inconclusive, fluency_score, vocabulary_score, grammar_score, pronunciation_score,
               comprehension_score, strengths, concerns, evidence, candidate_speaking_time,
               word_count, evaluation_status, evaluation_error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (interview_id, report_token, scores.get("overall_score"), scores.get("cefr_level"),
             scores.get("confidence"), int(inconclusive), scores.get("fluency_score"),
             scores.get("vocabulary_score"), scores.get("grammar_score"), scores.get("pronunciation_score"),
             scores.get("comprehension_score"), json.dumps(scores.get("strengths", [])),
             json.dumps(scores.get("concerns", [])), json.dumps(scores.get("evidence", [])),
             scores.get("candidate_speaking_time"), scores.get("word_count"),
             scores.get("evaluation_status", "failed" if scores.get("overall_score") is None else "completed"),
             scores.get("error"), now),
        )
        conn.execute(
            """INSERT INTO recordings (interview_id, recording_path, transcript_path, duration_seconds,
               ended_reason, raw_transcript) VALUES (?, ?, ?, ?, ?, ?)""",
            (interview_id, audio_path, transcript_path, int(duration_seconds), ended_reason, json.dumps(transcript)),
        )
        conn.execute("UPDATE interviews SET status = ?, completed_at = ? WHERE id = ?", (status, now, interview_id))
        conn.commit()

        # This is the completion path session.py drives after a WebSocket interview
        # ends -- notify RWS via the shared helper (see rws_client.py) once the
        # local state is already durably committed above.
        await notify_and_record_interview_result(
            conn, interview_id, row["external_reference_id"], scores, inconclusive,
            language=row["language"],
        )

        if scores.get("evaluation_status") == "completed" and settings.resend_api_key and settings.admin_email:
            await send_report_email(
                candidate_name=row["candidate_name"],
                admin_email=settings.admin_email,
                report_link=f"{settings.base_url}/report?token={report_token}",
                scores=scores,
            )
    finally:
        conn.close()


async def retry_report(report_token: str) -> dict:
    """Re-evaluate an existing report without creating a new report token."""
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """SELECT r.*, i.token, i.language, i.candidate_name, i.status as interview_status,
                      rec.transcript_path, rec.raw_transcript, rec.duration_seconds
               FROM reports r
               JOIN interviews i ON r.interview_id = i.id
               LEFT JOIN recordings rec ON rec.interview_id = r.interview_id
               WHERE r.report_token = ?""",
            (report_token,),
        ).fetchone()
        if not row:
            raise LookupError("Report not found")

        transcript = []
        if row["raw_transcript"]:
            try:
                transcript = json.loads(row["raw_transcript"])
            except json.JSONDecodeError:
                transcript = []
        if not transcript and row["transcript_path"]:
            try:
                with open(row["transcript_path"], "r", encoding="utf-8") as transcript_file:
                    transcript = json.load(transcript_file)
            except (OSError, json.JSONDecodeError):
                transcript = []
        if not transcript:
            error = "No transcript is available for evaluation"
            conn.execute(
                "UPDATE reports SET evaluation_status = 'failed', evaluation_error = ? WHERE report_token = ?",
                (error, report_token),
            )
            conn.commit()
            raise ValueError("No transcript is available for evaluation")

        duration_seconds = row["duration_seconds"] or 0
        inconclusive = duration_seconds < 300 or sum(
            1 for turn in transcript if turn.get("role") in ("user", "candidate")
        ) < 3
        scores = await evaluate(
            transcript,
            language=row["language"],
            inconclusive=inconclusive,
            duration_seconds=duration_seconds,
        )
        evaluation_status = scores.get("evaluation_status", "failed" if scores.get("overall_score") is None else "completed")
        if evaluation_status == "failed":
            error = scores.get("error", "Evaluation failed")
            conn.execute(
                "UPDATE reports SET evaluation_status = 'failed', evaluation_error = ? WHERE report_token = ?",
                (error, report_token),
            )
            conn.commit()
            raise RuntimeError(error)

        now = datetime.utcnow().isoformat()
        conn.execute(
            """UPDATE reports SET overall_score = ?, cefr_level = ?, confidence = ?, inconclusive = ?,
               fluency_score = ?, vocabulary_score = ?, grammar_score = ?, pronunciation_score = ?,
               comprehension_score = ?, strengths = ?, concerns = ?, evidence = ?,
               candidate_speaking_time = ?, word_count = ?, evaluation_status = ?, evaluation_error = ?
               WHERE report_token = ?""",
            (scores.get("overall_score"), scores.get("cefr_level"), scores.get("confidence"), int(inconclusive),
             scores.get("fluency_score"), scores.get("vocabulary_score"), scores.get("grammar_score"),
             scores.get("pronunciation_score"), scores.get("comprehension_score"),
             json.dumps(scores.get("strengths", [])), json.dumps(scores.get("concerns", [])),
             json.dumps(scores.get("evidence", [])), scores.get("candidate_speaking_time"),
             scores.get("word_count"), evaluation_status, None, report_token),
        )
        conn.execute(
            "UPDATE interviews SET status = ?, completed_at = ? WHERE id = ?",
            ("inconclusive" if inconclusive else "completed", now, row["interview_id"]),
        )
        conn.commit()
        return scores
    finally:
        conn.close()
