"""
Outbound webhook: VoiceHR -> RWS, sent once when an interview reaches a terminal
evaluation outcome (completed / inconclusive / failed).

Best-effort, single attempt only -- no retry queue or reconciliation polling, per this
project's "no message brokers / no later infrastructure" rule. If RWS is unreachable the
failure is logged and swallowed; it must never affect VoiceHR's own interview
processing (report generation, email) which is this service's actual job.
"""
import hashlib
import hmac
import json
import logging
import sqlite3
import uuid
from datetime import datetime

import httpx

from backend.core.config import settings

logger = logging.getLogger(__name__)


def sign_payload(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def build_assessment_payload(scores: dict, inconclusive: bool, language: str | None = None) -> dict:
    """
    Shapes `scores` (the evaluator's full result dict) down to exactly the fields RWS's
    InterviewAssessment schema accepts -- that schema uses extra="forbid", so sending
    evaluator-only fields (confidence, strengths, dimensions, ...) would be rejected.

    `language` is the interview's own conducted/evaluated language (interviews.language,
    e.g. "en"/"fr") -- not part of `scores` itself (the evaluator scores whatever
    transcript it's given; it doesn't know or care which language that was), so it's
    threaded in separately by the caller (processing.py) from the interview row.
    """
    evaluation_status = scores.get(
        "evaluation_status", "failed" if scores.get("overall_score") is None else "completed"
    )
    return {
        "overall_score": scores.get("overall_score"),
        "language": language,
        "cefr_level": scores.get("cefr_level"),
        "fluency_score": scores.get("fluency_score"),
        "vocabulary_score": scores.get("vocabulary_score"),
        "grammar_score": scores.get("grammar_score"),
        "pronunciation_score": scores.get("pronunciation_score"),
        "comprehension_score": scores.get("comprehension_score"),
        "inconclusive": inconclusive,
        "evaluation_status": evaluation_status,
    }


def interview_status_for(assessment: dict) -> str:
    if assessment["evaluation_status"] == "failed":
        return "failed"
    if assessment["inconclusive"]:
        return "inconclusive"
    return "completed"


async def notify_rws_interview_result(
    external_reference_id: str,
    interview_status: str,
    assessment: dict,
    completed_at: datetime,
    delivery_id: str,
) -> bool:
    """Returns True if RWS acknowledged the webhook (2xx). Never raises."""
    if not settings.rws_webhook_url:
        return False

    payload = {
        "delivery_id": delivery_id,
        "external_reference_id": external_reference_id,
        "interview_status": interview_status,
        "assessment": assessment,
        "completed_at": completed_at.isoformat(),
    }
    body = json.dumps(payload).encode("utf-8")
    signature = sign_payload(body, settings.rws_webhook_hmac_secret)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                settings.rws_webhook_url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Signature": signature,
                    "X-Delivery-Id": delivery_id,
                },
            )
        if 200 <= resp.status_code < 300:
            return True
        logger.warning(
            "RWS rejected interview-result webhook (delivery_id=%s): %s %s",
            delivery_id, resp.status_code, resp.text,
        )
        return False
    except httpx.HTTPError:
        logger.exception(
            "Failed to deliver interview-result webhook to RWS (delivery_id=%s)", delivery_id
        )
        return False


async def notify_and_record_interview_result(
    conn: sqlite3.Connection,
    interview_id: int,
    external_reference_id: str | None,
    scores: dict,
    inconclusive: bool,
    language: str | None = None,
) -> None:
    """
    Called by backend/services/processing.py once an interview reaches a terminal
    outcome. Shapes the evaluator's result into RWS's assessment schema, sends the
    signed webhook, and persists the delivery receipt on the interview row.

    No-op if external_reference_id is None -- this interview was created outside the
    RWS flow (e.g. the admin UI) and there is nothing on the RWS side to notify. Never
    raises: notify_rws_interview_result is itself best-effort/non-raising, and a failed
    delivery is still recorded (webhook_delivered_at stays NULL) rather than retried.
    """
    if not external_reference_id:
        return

    assessment_payload = build_assessment_payload(scores, inconclusive, language)
    delivery_id = str(uuid.uuid4())
    delivered = await notify_rws_interview_result(
        external_reference_id=external_reference_id,
        interview_status=interview_status_for(assessment_payload),
        assessment=assessment_payload,
        completed_at=datetime.utcnow(),
        delivery_id=delivery_id,
    )
    conn.execute(
        "UPDATE interviews SET webhook_delivery_id = ?, webhook_delivered_at = ? WHERE id = ?",
        (delivery_id, datetime.utcnow().isoformat() if delivered else None, interview_id),
    )
    conn.commit()
