import asyncio
import json
import sqlite3
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import backend.database as database
from backend.database import MAX_INTERVIEW_RECONNECTS
from backend.services.agent import run_sarah_pipeline
from backend.services.processing import process_interview

router = APIRouter()
logger = logging.getLogger(__name__)

# One asyncio.Lock per interview token, held for the *entire* handler body below
# (connect through process_interview) -- the single-process guard that makes "only
# one active Gemini/session bridge per interview" and "two simultaneous sockets
# cannot independently own one interview" true. VoiceHR runs as one uvicorn worker,
# one process (see Dockerfile) -- an in-process lock is a real, sufficient guard here,
# not a stand-in for a distributed one; this project does not run multiple workers.
# The lock's scope deliberately extends past run_sarah_pipeline and through
# process_interview: releasing it right after the pipeline returns would leave a
# window (transcription/evaluation can take several seconds) where a reconnect
# attempt could see the DB row still 'active' and start a second pipeline concurrently
# with the first's own finalization -- exactly the "WebSocket close + completion
# racing" case this is meant to prevent. process_interview's own finalized_at claim
# (processing.py) is the defense-in-depth backstop if this lock were ever bypassed.
#
# Grows by one entry per distinct token ever connected, for the life of the process --
# fine at this project's scale (a handful of interviews, not a public-internet queue);
# not worth adding eviction logic for.
_active_interview_locks: dict[str, asyncio.Lock] = {}


def _get_interview_lock(token: str) -> asyncio.Lock:
    lock = _active_interview_locks.get(token)
    if lock is None:
        lock = asyncio.Lock()
        _active_interview_locks[token] = lock
    return lock


@router.websocket("/ws/interview/{token}")
async def interview_session(websocket: WebSocket, token: str):
    lock = _get_interview_lock(token)
    if lock.locked():
        # Another connection is already bridging this exact interview. Accept just
        # long enough to explain why, rather than a bare, unexplained 1008 the
        # candidate's browser has no way to distinguish from "this link is invalid."
        await websocket.accept()
        try:
            await websocket.send_text(json.dumps({
                "type": "already_active",
                "message": "This interview is already connected from another tab or window.",
            }))
        except Exception:
            pass
        await websocket.close(code=1008)
        return

    async with lock:
        await websocket.accept()
        conn = sqlite3.connect(database.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
            if not row or row["status"] != "active":
                await websocket.close(code=1008)
                return

            # A prior, recoverably-dropped attempt on this same interview may have
            # left a partial transcript/elapsed-duration behind (see the recoverable-
            # failure branch below) -- carry it forward so the eventual evaluated
            # transcript covers the whole conversation, not just this leg.
            resume_transcript = []
            if row["pending_transcript"]:
                try:
                    resume_transcript = json.loads(row["pending_transcript"])
                except (TypeError, ValueError):
                    resume_transcript = []
            resume_duration_seconds = row["pending_duration_seconds"] or 0.0

            # MediaRecorder emits WebM in supported browsers.
            audio_path = f"storage/recordings/{row['id']}.webm"
            pipeline_result = await run_sarah_pipeline(
                websocket,
                token,
                row["language"],
                audio_path,
                candidate_name=row["candidate_name"],
                resume_transcript=resume_transcript,
                resume_duration_seconds=resume_duration_seconds,
            )
            if len(pipeline_result) == 3:
                transcript, duration, ended_reason = pipeline_result
            else:
                transcript, duration = pipeline_result
                ended_reason = "completed"
            if ended_reason in ("gemini_live_error", "browser_disconnect"):
                # Keep the recording, but do not create a misleading completed report.
                # Bounded by the same MAX_INTERVIEW_RECONNECTS budget deps.py's own
                # stale-active reactivation respects (see database.py) -- once
                # exhausted this UPDATE's WHERE clause simply stops matching, leaving
                # the interview 'active' and unreachable rather than looping forever.
                conn.execute(
                    "UPDATE interviews SET status = 'pending', retry_count = retry_count + 1, "
                    "pending_transcript = ?, pending_duration_seconds = ? "
                    "WHERE token = ? AND retry_count < ? AND status = 'active'",
                    (json.dumps(transcript), duration, token, MAX_INTERVIEW_RECONNECTS),
                )
                conn.commit()
                await websocket.close(code=1011)
                return
            # Let the browser leave the interview immediately. Transcription and
            # evaluation can take several seconds and do not belong on the socket
            # close path.
            await websocket.close(code=1000)
            await process_interview(token, transcript, audio_path, duration, ended_reason)
        except WebSocketDisconnect:
            conn.execute(
                "UPDATE interviews SET status = 'pending', retry_count = retry_count + 1 "
                "WHERE token = ? AND retry_count < ? AND status = 'active'",
                (token, MAX_INTERVIEW_RECONNECTS),
            )
            conn.commit()
        except ConnectionError:
            conn.execute(
                "UPDATE interviews SET status = 'pending', retry_count = retry_count + 1 "
                "WHERE token = ? AND retry_count < ? AND status = 'active'",
                (token, MAX_INTERVIEW_RECONNECTS),
            )
            conn.commit()
        except Exception:
            logger.exception("Interview session failed for token %s", token)
            try:
                await websocket.send_text(
                    '{"type":"server_error","stage":"session","message":"The interview session encountered an error. Please try again."}'
                )
            except Exception:
                logger.exception("Could not send session error to browser for token %s", token)
            conn.execute(
                "UPDATE interviews SET status = 'pending', retry_count = retry_count + 1 "
                "WHERE token = ? AND retry_count < ? AND status = 'active'",
                (token, MAX_INTERVIEW_RECONNECTS),
            )
            conn.commit()
        finally:
            conn.close()
