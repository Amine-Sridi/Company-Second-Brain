import sqlite3
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import backend.database as database
from backend.services.agent import run_sarah_pipeline
from backend.services.processing import process_interview

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws/interview/{token}")
async def interview_session(websocket: WebSocket, token: str):
    await websocket.accept()
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
        if not row or row["status"] != "active":
            await websocket.close(code=1008)
            return

        # MediaRecorder emits WebM in supported browsers.
        audio_path = f"storage/recordings/{row['id']}.webm"
        pipeline_result = await run_sarah_pipeline(
            websocket,
            token,
            row["language"],
            audio_path,
            candidate_name=row["candidate_name"],
        )
        if len(pipeline_result) == 3:
            transcript, duration, ended_reason = pipeline_result
        else:
            transcript, duration = pipeline_result
            ended_reason = "completed"
        if ended_reason in ("gemini_live_error", "browser_disconnect"):
            # Keep the recording, but do not create a misleading completed report.
            conn.execute(
                "UPDATE interviews SET status = 'pending', retry_count = retry_count + 1 "
                "WHERE token = ? AND retry_count < 1 AND status = 'active'",
                (token,),
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
            "WHERE token = ? AND retry_count < 1 AND status = 'active'",
            (token,),
        )
        conn.commit()
    except ConnectionError:
        conn.execute(
            "UPDATE interviews SET status = 'pending', retry_count = retry_count + 1 "
            "WHERE token = ? AND retry_count < 1 AND status = 'active'",
            (token,),
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
            "WHERE token = ? AND retry_count < 1 AND status = 'active'",
            (token,),
        )
        conn.commit()
    finally:
        conn.close()
