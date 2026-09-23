"""
tests/test_interview_round_mailer.py
------------------------------
Pure unit tests for interview_round_mailer.py's ICS/email-content builders (phase
2: real calendar attachments, reschedule/cancel notifications -- see handoff.md).
No DB, no real Resend call -- build_ics/email_content are plain data in, plain
data out, the same "one function decides this, test every branch directly" style
test_jd_validation.py/test_match_anomaly.py already use.

The final section does exercise the real _send_email/send_interview_round_notification
error paths (with a stubbed httpx transport, still no network), because the exact
*text* those raise is not cosmetic: main.py persists it verbatim into
recruitment.interview_rounds.notification_error. It must never contain the recipient
address or the provider's response body, or scheduling a round would write candidate
PII into RWS's own database. That is checked here, on the real code, rather than
anywhere that mocks the mailer away.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import interview_round_mailer
from interview_round_mailer import (
    NotificationDeliveryError,
    build_ics,
    email_content,
    send_interview_round_notification,
)

_SCHEDULED_AT = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)


def _ics(**overrides):
    kwargs = dict(
        round_id="11111111-1111-1111-1111-111111111111",
        sequence=0,
        cancelled=False,
        candidate_email="candidate@example.com",
        candidate_name="Ada Lovelace",
        interviewer_email="jane.doe@example.com",
        interviewer_name="Jane Doe",
        job_title="Backend Engineer",
        round_type="technical",
        scheduled_at=_SCHEDULED_AT,
        location="https://meet.example.com/room",
    )
    kwargs.update(overrides)
    return build_ics(**kwargs).decode("utf-8")


# ---------------------------------------------------------------------------
# build_ics -- structure, UID stability, SEQUENCE, METHOD/STATUS
# ---------------------------------------------------------------------------

def test_build_ics_uses_crlf_line_endings_per_rfc_5545():
    ics = _ics()
    assert "\r\n" in ics
    assert "BEGIN:VCALENDAR\r\n" in ics
    assert ics.endswith("END:VCALENDAR\r\n")


def test_build_ics_uid_is_derived_only_from_round_id():
    """UID must stay identical across a reschedule (different sequence/time) so a
    real calendar client updates the same event instead of creating a duplicate."""
    first = _ics(sequence=0, scheduled_at=_SCHEDULED_AT)
    second = _ics(sequence=1, scheduled_at=_SCHEDULED_AT + timedelta(hours=2))
    uid_line = next(line for line in first.split("\r\n") if line.startswith("UID:"))
    assert uid_line in second
    assert "11111111-1111-1111-1111-111111111111@company-second-brain" in uid_line


def test_build_ics_different_round_ids_get_different_uids():
    a = _ics(round_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    b = _ics(round_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    uid_a = next(line for line in a.split("\r\n") if line.startswith("UID:"))
    uid_b = next(line for line in b.split("\r\n") if line.startswith("UID:"))
    assert uid_a != uid_b


def test_build_ics_renders_the_given_sequence_verbatim():
    assert "SEQUENCE:0\r\n" in _ics(sequence=0)
    assert "SEQUENCE:3\r\n" in _ics(sequence=3)


def test_build_ics_scheduled_is_a_request_with_confirmed_status():
    ics = _ics(cancelled=False)
    assert "METHOD:REQUEST\r\n" in ics
    assert "STATUS:CONFIRMED\r\n" in ics


def test_build_ics_cancelled_is_a_cancel_with_cancelled_status():
    ics = _ics(cancelled=True)
    assert "METHOD:CANCEL\r\n" in ics
    assert "STATUS:CANCELLED\r\n" in ics


def test_build_ics_dtstart_dtend_reflect_scheduled_at_and_default_duration():
    ics = _ics(scheduled_at=_SCHEDULED_AT)
    assert "DTSTART:20261201T100000Z\r\n" in ics
    # Default duration is 60 minutes -- see _ICS_DEFAULT_DURATION_MINUTES.
    assert "DTEND:20261201T110000Z\r\n" in ics


def test_build_ics_omits_location_when_none():
    ics = _ics(location=None)
    assert "LOCATION:" not in ics


def test_build_ics_includes_location_when_given():
    ics = _ics(location="Building 3, Room 210")
    assert "LOCATION:Building 3\\, Room 210\r\n" in ics


def test_build_ics_escapes_special_characters_in_free_text_fields():
    ics = _ics(job_title="R&D; Platform, Team", location=None)
    summary_line = next(line for line in ics.split("\r\n") if line.startswith("SUMMARY:"))
    assert "R&D\\; Platform\\, Team" in summary_line


def test_build_ics_includes_organizer_and_both_attendees():
    ics = _ics()
    assert "ORGANIZER;CN=Jane Doe:mailto:jane.doe@example.com\r\n" in ics
    assert "mailto:candidate@example.com" in ics
    assert "mailto:jane.doe@example.com" in ics


# ---------------------------------------------------------------------------
# email_content -- wording varies by kind, resend never claims a "change"
# ---------------------------------------------------------------------------

def _content(kind, **overrides):
    kwargs = dict(
        candidate_name="Ada Lovelace",
        interviewer_name="Jane Doe",
        job_title="Backend Engineer",
        round_type="technical",
        scheduled_at=_SCHEDULED_AT,
        location="https://meet.example.com/room",
    )
    kwargs.update(overrides)
    return email_content(kind, **kwargs)


def test_email_content_scheduled_mentions_scheduling_not_a_change():
    (candidate_subject, candidate_body), (interviewer_subject, interviewer_body) = _content("scheduled")
    assert "scheduled" in candidate_subject
    assert "rescheduled" not in candidate_subject
    assert "cancelled" not in candidate_subject
    assert "Jane Doe" in candidate_body
    assert "Ada Lovelace" in interviewer_body


def test_email_content_rescheduled_says_rescheduled_not_scheduled():
    (candidate_subject, _), (interviewer_subject, _) = _content("rescheduled")
    assert "rescheduled" in candidate_subject
    assert "rescheduled" in interviewer_subject


def test_email_content_cancelled_says_cancelled_and_names_the_original_time():
    (candidate_subject, candidate_body), (interviewer_subject, interviewer_body) = _content("cancelled")
    assert "cancelled" in candidate_subject
    assert "cancelled" in interviewer_subject.lower()
    assert "2026" in candidate_body  # names when it *was* scheduled for
    assert "Ada Lovelace" in interviewer_body


def test_email_content_cancelled_omits_the_location_line():
    """A cancelled meeting's location is no longer relevant -- unlike
    scheduled/rescheduled, which both include it."""
    (_, candidate_body), (_, interviewer_body) = _content("cancelled", location="https://meet.example.com/room")
    assert "Location" not in candidate_body
    assert "Location" not in interviewer_body


def test_email_content_omits_location_line_when_none():
    (_, candidate_body), (_, interviewer_body) = _content("scheduled", location=None)
    assert "Location" not in candidate_body
    assert "Location" not in interviewer_body


# ---------------------------------------------------------------------------
# _send_email / send_interview_round_notification -- PII constraint on the error
# text that main.py persists into interview_rounds.notification_error.
#
# Real code, stubbed transport: only httpx.AsyncClient is replaced, so the actual
# error-construction branches in interview_round_mailer run.
# ---------------------------------------------------------------------------

_CANDIDATE_EMAIL = "ada.lovelace@example.com"
_CANDIDATE_NAME = "Ada Lovelace"
_INTERVIEWER_EMAIL = "panel.lead@example.com"

# Shaped like Resend's real sandbox rejection, which echoes the recipient address
# back in its body -- the exact reason the body must never be persisted.
_PROVIDER_BODY = (
    '{"statusCode":403,"name":"validation_error","message":"You can only send '
    f'testing emails to your own email address ({_CANDIDATE_EMAIL})."}}'
)


class _FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


def _stub_transport(monkeypatch, *, response=None, raise_exc=None):
    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, *args, **kwargs):
            if raise_exc is not None:
                raise raise_exc
            return response

    monkeypatch.setattr(interview_round_mailer.httpx, "AsyncClient", _FakeAsyncClient)


def _send_email(**overrides):
    kwargs = dict(
        api_key="test-key",
        from_address="Company Second Brain <onboarding@resend.dev>",
        to=_CANDIDATE_EMAIL,
        subject="Your technical interview",
        body="Hi Ada,",
    )
    kwargs.update(overrides)
    return asyncio.run(interview_round_mailer._send_email(**kwargs))


def test_send_email_rejection_error_never_names_the_recipient(monkeypatch):
    _stub_transport(monkeypatch, response=_FakeResponse(403, _PROVIDER_BODY))

    with pytest.raises(NotificationDeliveryError) as excinfo:
        _send_email()

    assert _CANDIDATE_EMAIL not in str(excinfo.value)


def test_send_email_rejection_error_never_includes_the_provider_body(monkeypatch):
    """Resend's own rejection body echoes the recipient address back, so including
    it would leak PII even if the message itself named no one."""
    _stub_transport(monkeypatch, response=_FakeResponse(403, _PROVIDER_BODY))

    with pytest.raises(NotificationDeliveryError) as excinfo:
        _send_email()

    assert "You can only send testing emails" not in str(excinfo.value)


def test_send_email_rejection_error_still_reports_the_status_code(monkeypatch):
    """Sanitized, not useless: the status code is what distinguishes an unverified
    sending domain (403) from a malformed recipient (422) from a provider outage."""
    _stub_transport(monkeypatch, response=_FakeResponse(403, _PROVIDER_BODY))

    with pytest.raises(NotificationDeliveryError) as excinfo:
        _send_email()

    assert "403" in str(excinfo.value)


def test_send_email_transport_error_never_names_the_recipient(monkeypatch):
    _stub_transport(monkeypatch, raise_exc=httpx.ConnectError("connection refused"))

    with pytest.raises(NotificationDeliveryError) as excinfo:
        _send_email()

    assert _CANDIDATE_EMAIL not in str(excinfo.value)
    assert "ConnectError" in str(excinfo.value)


def test_notification_failure_message_never_contains_candidate_contact(monkeypatch):
    """The end-to-end guard for what actually gets persisted: this exact string is
    what main.py writes into interview_rounds.notification_error."""
    _stub_transport(monkeypatch, response=_FakeResponse(403, _PROVIDER_BODY))

    with pytest.raises(NotificationDeliveryError) as excinfo:
        asyncio.run(
            send_interview_round_notification(
                api_key="test-key",
                from_address="Company Second Brain <onboarding@resend.dev>",
                kind="scheduled",
                round_id="11111111-1111-1111-1111-111111111111",
                sequence=0,
                candidate_email=_CANDIDATE_EMAIL,
                candidate_name=_CANDIDATE_NAME,
                interviewer_email=_INTERVIEWER_EMAIL,
                interviewer_name="Jane Doe",
                job_title="Backend Engineer",
                round_type="technical",
                scheduled_at=_SCHEDULED_AT,
                location="https://meet.example.com/room",
            )
        )

    message = str(excinfo.value)
    assert _CANDIDATE_EMAIL not in message
    assert _CANDIDATE_NAME not in message
    assert _INTERVIEWER_EMAIL not in message
    # Still says which side failed -- that is what makes the sanitized message
    # actionable without naming anyone.
    assert "candidate:" in message
    assert "interviewer:" in message
