import json
import os
import sqlite3
from datetime import datetime

from backend.core.config import settings
from backend.core.security import generate_report_token
import backend.database as database
from backend.services.evaluator import FALLBACK_ADVISORY_STATUS, evaluate
from backend.services.sufficiency import assess as assess_sufficiency
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

        # Atomically claim this interview for finalization -- exactly one caller can
        # ever win this UPDATE. Without it, two callers racing for the same interview
        # (a WebSocket close finalizing normally while a stale-session path also
        # reaches this function, or any other double-invocation) would both pass the
        # plain status check above, both call evaluate()/send the webhook, and both
        # commit their own report/recording rows -- and since the webhook's
        # delivery_id is freshly generated per call (rws_client.py), RWS's own
        # delivery-id dedup cannot catch this: from RWS's side these would look like
        # two independent, legitimate completions, not a retried duplicate. This
        # UPDATE has no `await` before it, so under this project's single-process,
        # single-event-loop deployment it always runs to completion before the event
        # loop can switch to a second concurrent call -- the second call's own claim
        # attempt then sees rowcount == 0 and returns immediately, before any
        # evaluation work or webhook send.
        now_claim = datetime.utcnow().isoformat()
        claimed = conn.execute(
            "UPDATE interviews SET finalized_at = ? "
            "WHERE id = ? AND finalized_at IS NULL AND status IN ('active', 'pending')",
            (now_claim, interview_id),
        )
        if claimed.rowcount == 0:
            return
        conn.commit()

        terminated = ended_reason in ("candidate_refused", "abusive_language")
        # This is the structural, deterministic pre-check (too short/too few turns/
        # forcibly ended) -- told to the evaluator up front (below) so its own prompt
        # note is accurate, distinct from evaluation_status below, which can *also*
        # come back "insufficient_evidence" from the model's own read of the
        # transcript's content even when this pre-check found nothing structurally
        # wrong (e.g. long enough, enough turns, but almost entirely off-topic or
        # non-answers).
        # Measures the candidate's language sample rather than the clock. The old gate
        # was `duration_seconds < 300 or candidate_turns < 3`, which rejected a stored
        # interview carrying 485 candidate words across 15 substantive turns for being
        # 16 seconds short, and accepted three one-word answers because it counted any
        # turn at all. See sufficiency.py for the measurements behind the thresholds and
        # why the relevance half stays with the model.
        sufficient, sufficiency_reason, _sample = assess_sufficiency(
            transcript, duration_seconds=duration_seconds, terminated=terminated,
        )
        pre_check_inconclusive = not sufficient
        os.makedirs("storage/transcripts", exist_ok=True)
        transcript_path = f"storage/transcripts/{interview_id}.json"
        with open(transcript_path, "w", encoding="utf-8") as transcript_file:
            json.dump(transcript, transcript_file, ensure_ascii=False)

        scores = await evaluate(
            transcript,
            language=row["language"],
            inconclusive=pre_check_inconclusive,
            duration_seconds=duration_seconds,
            sufficiency_reason=None if sufficient else sufficiency_reason,
        )
        # The single source of truth from here on: either half (the deterministic
        # pre-check, or the model's own sufficient_evidence: false) is enough to
        # treat this interview as not having produced a confident, usable result --
        # drives VoiceHR's own interview/report status *and* what RWS is told, so the
        # two can never disagree about whether a CEFR verdict actually exists.
        # fallback_advisory joins these because a locally-evaluated interview also has
        # no CEFR verdict (evaluator._fallback_advisory_result withholds it on
        # purpose). Without it this would be filed as a completed interview carrying a
        # null cefr_level, and the recruiter would see a finished assessment with a
        # blank level and no indication that Gemini never ran.
        inconclusive = pre_check_inconclusive or scores.get("evaluation_status") in (
            "insufficient_evidence", FALLBACK_ADVISORY_STATUS,
        )
        report_token = generate_report_token()
        now = datetime.utcnow().isoformat()
        status = "terminated" if terminated else ("inconclusive" if inconclusive else "completed")

        conn.execute(
            """INSERT INTO reports (interview_id, report_token, overall_score, cefr_level, confidence,
               inconclusive, fluency_score, vocabulary_score, grammar_score, pronunciation_score,
               comprehension_score, interaction_score, strengths, concerns, evidence, candidate_speaking_time,
               word_count, evaluation_status, evaluation_error, sufficiency_reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (interview_id, report_token, scores.get("overall_score"), scores.get("cefr_level"),
             scores.get("confidence"), int(inconclusive), scores.get("fluency_score"),
             scores.get("vocabulary_score"), scores.get("grammar_score"), scores.get("pronunciation_score"),
             scores.get("comprehension_score"), scores.get("interaction_score"), json.dumps(scores.get("strengths", [])),
             json.dumps(scores.get("concerns", [])), json.dumps(scores.get("evidence", [])),
             scores.get("candidate_speaking_time"), scores.get("word_count"),
             scores.get("evaluation_status", "failed" if scores.get("overall_score") is None else "completed"),
             scores.get("error"), scores.get("sufficiency_reason"), now),
        )
        conn.execute(
            """INSERT INTO recordings (interview_id, recording_path, transcript_path, duration_seconds,
               ended_reason, raw_transcript) VALUES (?, ?, ?, ?, ?, ?)""",
            (interview_id, audio_path, transcript_path, int(duration_seconds), ended_reason, json.dumps(transcript)),
        )
        # Clear the reconnect-recovery scratch columns now that this interview has
        # genuinely finalized -- nothing will ever read them again for this row (a
        # completed/inconclusive/terminated interview never re-enters the connect/
        # start flow), so leaving stale data there would only be confusing on
        # inspection.
        conn.execute(
            "UPDATE interviews SET status = ?, completed_at = ?, "
            "pending_transcript = NULL, pending_duration_seconds = NULL WHERE id = ?",
            (status, now, interview_id),
        )
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
                      i.external_reference_id,
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
        # Same measured gate as process_interview -- if these two disagreed, a retry
        # could issue a CEFR for an interview the original run judged ungradeable (or
        # the reverse), and the two records would contradict each other.
        sufficient, sufficiency_reason, _sample = assess_sufficiency(
            transcript, duration_seconds=duration_seconds, terminated=False,
        )
        pre_check_inconclusive = not sufficient
        scores = await evaluate(
            transcript,
            language=row["language"],
            inconclusive=pre_check_inconclusive,
            duration_seconds=duration_seconds,
            sufficiency_reason=None if sufficient else sufficiency_reason,
        )
        evaluation_status = scores.get("evaluation_status", "failed" if scores.get("overall_score") is None else "completed")
        inconclusive = pre_check_inconclusive or evaluation_status in (
            "insufficient_evidence", FALLBACK_ADVISORY_STATUS,
        )
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
               comprehension_score = ?, interaction_score = ?, strengths = ?, concerns = ?, evidence = ?,
               candidate_speaking_time = ?, word_count = ?, evaluation_status = ?, evaluation_error = ?,
               sufficiency_reason = ?
               WHERE report_token = ?""",
            (scores.get("overall_score"), scores.get("cefr_level"), scores.get("confidence"), int(inconclusive),
             scores.get("fluency_score"), scores.get("vocabulary_score"), scores.get("grammar_score"),
             scores.get("pronunciation_score"), scores.get("comprehension_score"), scores.get("interaction_score"),
             json.dumps(scores.get("strengths", [])), json.dumps(scores.get("concerns", [])),
             json.dumps(scores.get("evidence", [])), scores.get("candidate_speaking_time"),
             scores.get("word_count"), evaluation_status, scores.get("error"),
             scores.get("sufficiency_reason"), report_token),
        )
        conn.execute(
            "UPDATE interviews SET status = ?, completed_at = ? WHERE id = ?",
            ("inconclusive" if inconclusive else "completed", now, row["interview_id"]),
        )
        conn.commit()

        # Re-notify RWS with the corrected result, exactly as process_interview does
        # for the first one. Without this, a retry silently diverged the two systems:
        # VoiceHR showed the new, good report while RWS kept the old failed/
        # inconclusive assessment -- and RWS's language gate decides on *its* copy, so
        # a recruiter could be blocked from accepting a candidate whose report visibly
        # passes. Best-effort and non-raising, like the original send: the
        # re-evaluation itself is already durably committed above and must not be
        # undone by a delivery problem. (RWS accepts this as a correction because
        # record_interview_result also updates an application already 'interviewed' --
        # see its own docstring; a terminal accepted/rejected decision is still never
        # overwritten.)
        await notify_and_record_interview_result(
            conn, row["interview_id"], row["external_reference_id"], scores, inconclusive,
            language=row["language"],
        )
        return scores
    finally:
        conn.close()
