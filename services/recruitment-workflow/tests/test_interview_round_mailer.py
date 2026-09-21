"""
tests/test_interview_round_mailer.py
------------------------------
Pure unit tests for interview_round_mailer.py's ICS/email-content builders (phase
2: real calendar attachments, reschedule/cancel notifications -- see handoff.md).
No DB, no HTTP, no Resend call -- build_ics/email_content are plain data in, plain
data out, the same "one function decides this, test every branch directly" style
test_jd_validation.py/test_match_anomaly.py already use. _send_email/
send_interview_round_notification's own network/error-handling behavior is covered
at the route level (services/recruitment-workflow/tests/test_interview_rounds.py)
and the repo-root integration suite, via mocking, not here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from interview_round_mailer import build_ics, email_content

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
