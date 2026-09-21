"""
interview_round_mailer.py
--------------------------
RWS-owned, thin Resend-based mailer for interview-round scheduling notifications --
phase 1 (plain-text email) and phase 2 (a real ICS calendar attachment, reschedule/
cancel updates) of automating human interview scheduling (see handoff.md). Mirrors
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

The ICS attachment is a plain text file (RFC 5545), not a calendar-provider API
integration -- no OAuth, no service account, no two-way sync. Google Calendar (or
similar) remains explicitly deferred per this feature's own original design: an
ICS a real client already knows how to add/update/remove from a "Add to calendar"
prompt is sufficient until a real, demonstrated need for two-way sync or
interviewer-availability checking shows up.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import httpx

_RESEND_URL = "https://api.resend.com/emails"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_ICS_DEFAULT_DURATION_MINUTES = 60

NotificationKind = Literal["scheduled", "rescheduled", "cancelled"]


class NotificationDeliveryError(Exception):
    """Raised on any interview-round notification failure -- not configured, a
    network/timeout error, or a non-2xx response from Resend. Never returns a bool,
    so main.py's caller can never mistake a failure for success; it always catches
    this and records notification_status='failed' rather than letting it fail (or
    roll back) the round-creation/reschedule/cancel request itself -- except for
    the explicit resend-notification endpoint, which deliberately re-raises it as
    the request's own failure (see main.py: resend_interview_round_notification_
    endpoint)."""


def _format_when(scheduled_at: datetime) -> str:
    return scheduled_at.strftime("%A, %B %d, %Y at %H:%M UTC")


def _escape_ics_text(value: str) -> str:
    """RFC 5545 §3.3.11 TEXT escaping -- backslash, semicolon, comma, and newline
    are the only characters that need it for the fields this module actually
    writes (SUMMARY/DESCRIPTION/LOCATION/CN, all plain recruiter/Gemini-free
    input)."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _format_ics_datetime(dt: datetime) -> str:
    """UTC `YYYYMMDDTHHMMSSZ` form -- the one DATE-TIME representation RFC 5545
    guarantees every calendar client interprets unambiguously, without needing a
    VTIMEZONE block (that only matters for floating/local times, which this
    module never emits)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(
    *,
    round_id: str,
    sequence: int,
    cancelled: bool,
    candidate_email: str,
    candidate_name: str,
    interviewer_email: str,
    interviewer_name: str,
    job_title: str,
    round_type: str,
    scheduled_at: datetime,
    location: Optional[str],
) -> bytes:
    """
    One VEVENT, RFC 5545, `\\r\\n` line endings (required by the spec; several
    real clients silently mis-render on bare `\\n`). `UID` is the round's own id
    -- stable for that round's entire lifetime, so a reschedule's regenerated ICS
    updates the same calendar entry rather than creating a duplicate. `SEQUENCE`
    is the caller's job to increment on each real change (see
    db.InterviewRound.notification_sequence's own docstring) -- this function
    only renders whatever value it's given, it never decides when to bump it.
    A cancellation is `METHOD:CANCEL`/`STATUS:CANCELLED` on the *same* UID, the
    standard mechanism a real calendar client uses to remove an event it already
    added rather than leaving an orphaned entry behind.
    """
    uid = f"{round_id}@company-second-brain"
    method = "CANCEL" if cancelled else "REQUEST"
    status = "CANCELLED" if cancelled else "CONFIRMED"
    dtstamp = _format_ics_datetime(datetime.now(timezone.utc))
    dtstart = _format_ics_datetime(scheduled_at)
    dtend = _format_ics_datetime(scheduled_at + timedelta(minutes=_ICS_DEFAULT_DURATION_MINUTES))
    summary = f"{round_type.capitalize()} interview: {job_title}"
    description = f"Candidate: {candidate_name}\nInterviewer: {interviewer_name}"

    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//Company Second Brain//Interview Rounds//EN",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        f"METHOD:{method}",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        f"SEQUENCE:{sequence}",
        f"STATUS:{status}",
        f"SUMMARY:{_escape_ics_text(summary)}",
        f"DESCRIPTION:{_escape_ics_text(description)}",
    ]
    if location:
        lines.append(f"LOCATION:{_escape_ics_text(location)}")
    lines += [
        f"ORGANIZER;CN={_escape_ics_text(interviewer_name)}:mailto:{interviewer_email}",
        f"ATTENDEE;CN={_escape_ics_text(candidate_name)};ROLE=REQ-PARTICIPANT:mailto:{candidate_email}",
        f"ATTENDEE;CN={_escape_ics_text(interviewer_name)};ROLE=REQ-PARTICIPANT:mailto:{interviewer_email}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def email_content(
    kind: NotificationKind,
    *,
    candidate_name: str,
    interviewer_name: str,
    job_title: str,
    round_type: str,
    scheduled_at: datetime,
    location: Optional[str],
) -> tuple[tuple[str, str], tuple[str, str]]:
    """Returns ((candidate_subject, candidate_body), (interviewer_subject,
    interviewer_body)) for the given kind. A resend always uses "scheduled"
    wording regardless of any prior reschedule -- it is a re-delivery of the
    round's current confirmed state, not itself a change (see main.py:
    resend_interview_round_notification_endpoint)."""
    location_line = f"Location/link: {location}\n" if location else ""
    when = _format_when(scheduled_at)

    if kind == "cancelled":
        candidate_subject = f"Your {round_type} interview for {job_title} has been cancelled"
        candidate_body = (
            f"Hi {candidate_name},\n\n"
            f"Your {round_type} interview for the {job_title} role, previously "
            f"scheduled for {when}, has been cancelled.\n"
        )
        interviewer_subject = f"Cancelled: {round_type} round for {job_title}"
        interviewer_body = (
            f"Hi {interviewer_name},\n\n"
            f"The {round_type} interview for the {job_title} role with "
            f"{candidate_name}, previously scheduled for {when}, has been cancelled.\n"
        )
        return (candidate_subject, candidate_body), (interviewer_subject, interviewer_body)

    verb = "rescheduled" if kind == "rescheduled" else "scheduled"
    candidate_subject = f"Your {round_type} interview for {job_title} has been {verb}"
    candidate_body = (
        f"Hi {candidate_name},\n\n"
        f"Your {round_type} interview for the {job_title} role has been {verb}.\n\n"
        f"When: {when}\n"
        f"Interviewer: {interviewer_name}\n"
        f"{location_line}"
        "\nGood luck!\n"
    )
    interviewer_subject = f"Interview {verb}: {round_type} round for {job_title}"
    interviewer_body = (
        f"Hi {interviewer_name},\n\n"
        f"The {round_type} interview for the {job_title} role has been {verb}.\n\n"
        f"Candidate: {candidate_name}\n"
        f"When: {when}\n"
        f"{location_line}"
    )
    return (candidate_subject, candidate_body), (interviewer_subject, interviewer_body)


async def _send_email(
    api_key: str, from_address: str, to: str, subject: str, body: str,
    ics: Optional[bytes] = None, ics_content_type: Optional[str] = None,
) -> None:
    payload = {"from": from_address, "to": [to], "subject": subject, "text": body}
    if ics is not None:
        payload["attachments"] = [
            {
                "filename": "invite.ics",
                "content": base64.b64encode(ics).decode("ascii"),
                **({"content_type": ics_content_type} if ics_content_type else {}),
            }
        ]
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _RESEND_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
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
    kind: NotificationKind,
    round_id: str,
    sequence: int,
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
    interviewer -- each carrying the same ICS calendar attachment (`kind`
    controls both the email wording and the ICS's own METHOD/STATUS/SEQUENCE, see
    email_content/build_ics). Attempts both even if one fails, since each
    recipient's delivery is independent and both are worth getting through where
    possible; raises NotificationDeliveryError describing whichever failed (or
    both) if either did, so the caller can record a single notification_status/
    notification_error pair without losing which side failed.

    Raises immediately, without attempting either send, if api_key is falsy -- the
    "missing RESEND_API_KEY" case main.py's Settings.resend_api_key defaults to.
    """
    if not api_key:
        raise NotificationDeliveryError(
            "Email delivery not configured. Set RESEND_API_KEY in .env."
        )

    (candidate_subject, candidate_body), (interviewer_subject, interviewer_body) = email_content(
        kind,
        candidate_name=candidate_name, interviewer_name=interviewer_name,
        job_title=job_title, round_type=round_type,
        scheduled_at=scheduled_at, location=location,
    )
    cancelled = kind == "cancelled"
    ics = build_ics(
        round_id=round_id, sequence=sequence, cancelled=cancelled,
        candidate_email=candidate_email, candidate_name=candidate_name,
        interviewer_email=interviewer_email, interviewer_name=interviewer_name,
        job_title=job_title, round_type=round_type,
        scheduled_at=scheduled_at, location=location,
    )
    ics_content_type = f"text/calendar; method={'CANCEL' if cancelled else 'REQUEST'}; charset=utf-8"

    errors: list[str] = []
    try:
        await _send_email(
            api_key, from_address, candidate_email, candidate_subject, candidate_body,
            ics=ics, ics_content_type=ics_content_type,
        )
    except NotificationDeliveryError as exc:
        errors.append(f"candidate: {exc}")
    try:
        await _send_email(
            api_key, from_address, interviewer_email, interviewer_subject, interviewer_body,
            ics=ics, ics_content_type=ics_content_type,
        )
    except NotificationDeliveryError as exc:
        errors.append(f"interviewer: {exc}")

    if errors:
        raise NotificationDeliveryError("; ".join(errors))
