"""
interview_round_mailer.py
--------------------------
RWS-owned, thin Resend-based mailer for interview-round scheduling notifications --
phase 1 of automating human interview scheduling (see handoff.md). Mirrors
services/voicehr/backend/services/mailer.py's own shape closely (one small httpx
POST to Resend, no retry/circuit-breaker machinery) rather than sharing code with
it: VoiceHR's mailer is scoped to its own secure-interview-link email; this module
is scoped to interview_rounds' own scheduling/interviewer content -- two different
concerns owned by two different services. Duplicating ~30 lines here is the
CLAUDE.md-sanctioned choice over a shared abstraction with exactly one caller each.

Never persists a candidate's email/name -- the caller (main.py) fetches it fresh
from Resume Intelligence per send and passes it straight through as plain function
arguments; this module only ever holds it in memory for the duration of the two
HTTP calls below, never writes it anywhere.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import httpx

_RESEND_URL = "https://api.resend.com/emails"
_DEFAULT_TIMEOUT_SECONDS = 10.0


class NotificationDeliveryError(Exception):
    """Raised on any interview-round notification failure -- not configured, a
    network/timeout error, or a non-2xx response from Resend. Never returns a bool,
    so main.py's caller can never mistake a failure for success; it always catches
    this and records notification_status='failed' rather than letting it fail (or
    roll back) the round-creation/resend request itself -- except for the explicit
    resend-notification endpoint, which deliberately re-raises it as the request's
    own failure (see main.py: resend_interview_round_notification_endpoint)."""


def _format_when(scheduled_at: datetime) -> str:
    return scheduled_at.strftime("%A, %B %d, %Y at %H:%M UTC")


def _candidate_email_content(
    candidate_name: str, interviewer_name: str, job_title: str,
    round_type: str, scheduled_at: datetime, location: Optional[str],
) -> tuple[str, str]:
    subject = f"Your {round_type} interview for {job_title} has been scheduled"
    location_line = f"Location/link: {location}\n" if location else ""
    body = (
        f"Hi {candidate_name},\n\n"
        f"Your {round_type} interview for the {job_title} role has been scheduled.\n\n"
        f"When: {_format_when(scheduled_at)}\n"
        f"Interviewer: {interviewer_name}\n"
        f"{location_line}"
        "\nGood luck!\n"
    )
    return subject, body


def _interviewer_email_content(
    interviewer_name: str, candidate_name: str, job_title: str,
    round_type: str, scheduled_at: datetime, location: Optional[str],
) -> tuple[str, str]:
    subject = f"Interview assigned: {round_type} round for {job_title}"
    location_line = f"Location/link: {location}\n" if location else ""
    body = (
        f"Hi {interviewer_name},\n\n"
        f"You have been assigned to conduct a {round_type} interview for the "
        f"{job_title} role.\n\n"
        f"Candidate: {candidate_name}\n"
        f"When: {_format_when(scheduled_at)}\n"
        f"{location_line}"
    )
    return subject, body


async def _send_email(api_key: str, from_address: str, to: str, subject: str, body: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _RESEND_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"from": from_address, "to": [to], "subject": subject, "text": body},
            )
    except httpx.HTTPError as exc:
        raise NotificationDeliveryError(
            f"Email send to {to} failed: {type(exc).__name__}"
        ) from exc
    if response.status_code not in (200, 201):
        raise NotificationDeliveryError(f"Email send to {to} failed: {response.text}")


async def send_interview_round_notification(
    *,
    api_key: Optional[str],
    from_address: str,
    candidate_email: str,
    candidate_name: str,
    interviewer_email: str,
    interviewer_name: str,
    job_title: str,
    round_type: str,
    scheduled_at: datetime,
    location: Optional[str],
) -> None:
    """
    Sends two independent notification emails -- one to the candidate, one to the
    interviewer -- for a newly (re)scheduled interview round. Attempts both even if
    one fails, since each recipient's delivery is independent and both are worth
    getting through where possible; raises NotificationDeliveryError describing
    whichever failed (or both) if either did, so the caller can record a single
    notification_status/notification_error pair without losing which side failed.

    Raises immediately, without attempting either send, if api_key is falsy -- the
    "missing RESEND_API_KEY" case main.py's Settings.resend_api_key defaults to.
    """
    if not api_key:
        raise NotificationDeliveryError(
            "Email delivery not configured. Set RESEND_API_KEY in .env."
        )

    candidate_subject, candidate_body = _candidate_email_content(
        candidate_name, interviewer_name, job_title, round_type, scheduled_at, location
    )
    interviewer_subject, interviewer_body = _interviewer_email_content(
        interviewer_name, candidate_name, job_title, round_type, scheduled_at, location
    )

    errors: list[str] = []
    try:
        await _send_email(api_key, from_address, candidate_email, candidate_subject, candidate_body)
    except NotificationDeliveryError as exc:
        errors.append(f"candidate: {exc}")
    try:
        await _send_email(api_key, from_address, interviewer_email, interviewer_subject, interviewer_body)
    except NotificationDeliveryError as exc:
        errors.append(f"interviewer: {exc}")

    if errors:
        raise NotificationDeliveryError("; ".join(errors))
