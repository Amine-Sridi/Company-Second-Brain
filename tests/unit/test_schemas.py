"""Unit tests for shared Pydantic schemas.

Covers: valid construction, extra-field rejection, boundary validation, PII field
rejection on PII-free schemas, and round-trip serialisation.
"""
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from shared.schemas import (
    CandidateFeedbackRequest,
    CandidateMatch,
    CandidateSummary,
    ContactLookupResponse,
    InterviewAssessment,
    InvitationRequest,
    InvitationResponse,
    RetrieveCandidatesRequest,
    RetrieveCandidatesResponse,
    WebhookPayload,
)

CANDIDATE_ID = uuid.uuid4()
NOW = datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# CandidateSummary
# ---------------------------------------------------------------------------

def test_candidate_summary_valid():
    s = CandidateSummary(
        candidate_id=CANDIDATE_ID,
        skills=["Python", "FastAPI"],
        years_of_experience=3.5,
        job_titles=["Backend Engineer"],
        raw_text="Experienced backend engineer with Python skills.",
    )
    assert s.candidate_id == CANDIDATE_ID
    assert s.source_version == "1"


def test_candidate_summary_minimal():
    # skills, job_titles have defaults; years_of_experience is optional
    s = CandidateSummary(candidate_id=CANDIDATE_ID, raw_text="some text")
    assert s.skills == []
    assert s.job_titles == []
    assert s.years_of_experience is None


def test_candidate_summary_extra_field_rejected():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CandidateSummary(candidate_id=CANDIDATE_ID, raw_text="x", email="a@b.com")


def test_candidate_summary_pii_name_rejected():
    with pytest.raises(ValidationError):
        CandidateSummary(candidate_id=CANDIDATE_ID, raw_text="x", name="Alice")


def test_candidate_summary_raw_text_empty_rejected():
    with pytest.raises(ValidationError):
        CandidateSummary(candidate_id=CANDIDATE_ID, raw_text="")


def test_candidate_summary_raw_text_too_long_rejected():
    with pytest.raises(ValidationError):
        CandidateSummary(candidate_id=CANDIDATE_ID, raw_text="x" * 50_001)


def test_candidate_summary_years_negative_rejected():
    with pytest.raises(ValidationError):
        CandidateSummary(candidate_id=CANDIDATE_ID, raw_text="x", years_of_experience=-1)


def test_candidate_summary_skills_too_many_rejected():
    with pytest.raises(ValidationError):
        CandidateSummary(candidate_id=CANDIDATE_ID, raw_text="x", skills=["s"] * 201)


def test_candidate_summary_serialises_uuid_as_string():
    s = CandidateSummary(candidate_id=CANDIDATE_ID, raw_text="text")
    data = s.model_dump(mode="json")
    assert isinstance(data["candidate_id"], str)


# ---------------------------------------------------------------------------
# ContactLookupResponse
# ---------------------------------------------------------------------------

def test_contact_lookup_valid():
    r = ContactLookupResponse(
        candidate_id=CANDIDATE_ID,
        name="Alice Dupont",
        email="alice@example.com",
        phone="+33600000000",
    )
    assert r.email == "alice@example.com"


def test_contact_lookup_phone_optional():
    r = ContactLookupResponse(
        candidate_id=CANDIDATE_ID, name="Bob", email="bob@example.com"
    )
    assert r.phone is None


def test_contact_lookup_invalid_email():
    with pytest.raises(ValidationError):
        ContactLookupResponse(
            candidate_id=CANDIDATE_ID, name="Bob", email="not-an-email"
        )


def test_contact_lookup_extra_field_rejected():
    with pytest.raises(ValidationError):
        ContactLookupResponse(
            candidate_id=CANDIDATE_ID,
            name="Bob",
            email="bob@example.com",
            address="123 Main St",
        )


def test_contact_lookup_name_empty_rejected():
    with pytest.raises(ValidationError):
        ContactLookupResponse(
            candidate_id=CANDIDATE_ID, name="", email="bob@example.com"
        )


# ---------------------------------------------------------------------------
# RetrieveCandidatesRequest
# ---------------------------------------------------------------------------

def test_retrieve_request_valid():
    r = RetrieveCandidatesRequest(query_text="Python engineer with FastAPI experience")
    assert r.top_k == 20


def test_retrieve_request_custom_top_k():
    r = RetrieveCandidatesRequest(query_text="q", top_k=5)
    assert r.top_k == 5


def test_retrieve_request_top_k_zero_rejected():
    with pytest.raises(ValidationError):
        RetrieveCandidatesRequest(query_text="q", top_k=0)


def test_retrieve_request_top_k_over_limit_rejected():
    with pytest.raises(ValidationError):
        RetrieveCandidatesRequest(query_text="q", top_k=101)


def test_retrieve_request_empty_query_rejected():
    with pytest.raises(ValidationError):
        RetrieveCandidatesRequest(query_text="")


def test_retrieve_request_with_candidate_filter():
    ids = [uuid.uuid4(), uuid.uuid4()]
    r = RetrieveCandidatesRequest(query_text="q", candidate_ids=ids)
    assert len(r.candidate_ids) == 2


def test_retrieve_request_pii_field_rejected():
    with pytest.raises(ValidationError):
        RetrieveCandidatesRequest(query_text="q", email="x@y.com")


# ---------------------------------------------------------------------------
# RetrieveCandidatesResponse / CandidateMatch
# ---------------------------------------------------------------------------

def test_retrieve_response_valid():
    resp = RetrieveCandidatesResponse(
        matches=[
            CandidateMatch(candidate_id=CANDIDATE_ID, similarity_score=0.92),
        ]
    )
    assert resp.matches[0].similarity_score == pytest.approx(0.92)


def test_retrieve_response_empty_matches():
    resp = RetrieveCandidatesResponse(matches=[])
    assert resp.matches == []


def test_candidate_match_score_out_of_range():
    with pytest.raises(ValidationError):
        CandidateMatch(candidate_id=CANDIDATE_ID, similarity_score=1.1)


def test_candidate_match_score_negative():
    with pytest.raises(ValidationError):
        CandidateMatch(candidate_id=CANDIDATE_ID, similarity_score=-0.1)


def test_retrieve_response_extra_field_rejected():
    with pytest.raises(ValidationError):
        RetrieveCandidatesResponse(matches=[], name="Alice")


# ---------------------------------------------------------------------------
# InvitationRequest / Response
# ---------------------------------------------------------------------------

def test_invitation_request_valid():
    r = InvitationRequest(
        candidate_name="Alice Dupont",
        candidate_email="alice@example.com",
        language="en",
        external_reference_id="app-123",
    )
    assert r.language == "en"


def test_invitation_request_invalid_language():
    with pytest.raises(ValidationError):
        InvitationRequest(
            candidate_name="Alice",
            candidate_email="alice@example.com",
            language="de",
            external_reference_id="app-123",
        )


def test_invitation_request_missing_external_reference_id():
    with pytest.raises(ValidationError):
        InvitationRequest(
            candidate_name="Alice",
            candidate_email="alice@example.com",
            language="fr",
        )


def test_invitation_request_candidate_name_too_short():
    with pytest.raises(ValidationError):
        InvitationRequest(
            candidate_name="A",
            candidate_email="alice@example.com",
            language="en",
            external_reference_id="app-123",
        )


def test_invitation_request_extra_field_rejected():
    with pytest.raises(ValidationError):
        InvitationRequest(
            candidate_name="Alice Dupont",
            candidate_email="alice@example.com",
            language="en",
            external_reference_id="app-123",
            internal_id="secret",
        )


def test_invitation_response_valid():
    r = InvitationResponse(
        external_reference_id="app-123",
        interview_link="http://localhost:8004/interview?token=abc",
        expires_at=NOW,
    )
    assert r.external_reference_id == "app-123"


def test_invitation_response_extra_field_rejected():
    with pytest.raises(ValidationError):
        InvitationResponse(
            external_reference_id="app-123",
            interview_link="http://x",
            expires_at=NOW,
            candidate_email="x@y.com",
        )


# ---------------------------------------------------------------------------
# InterviewAssessment / WebhookPayload
# ---------------------------------------------------------------------------

def _make_assessment(**kwargs) -> dict:
    base = {"evaluation_status": "completed", "inconclusive": False}
    base.update(kwargs)
    return base


def test_assessment_valid_completed():
    a = InterviewAssessment(
        overall_score=82,
        cefr_level="B2",
        fluency_score=80,
        vocabulary_score=85,
        grammar_score=78,
        pronunciation_score=88,
        comprehension_score=80,
        evaluation_status="completed",
    )
    assert a.overall_score == 82


def test_assessment_score_out_of_range():
    with pytest.raises(ValidationError):
        InterviewAssessment(**_make_assessment(overall_score=101))


def test_assessment_status_invalid():
    with pytest.raises(ValidationError):
        InterviewAssessment(**_make_assessment(evaluation_status="unknown"))


def test_assessment_extra_field_rejected():
    with pytest.raises(ValidationError):
        InterviewAssessment(**_make_assessment(candidate_name="Alice"))


def test_webhook_payload_valid():
    payload = WebhookPayload(
        delivery_id="dlv-abc123",
        external_reference_id="app-456",
        interview_status="completed",
        assessment=InterviewAssessment(**_make_assessment(overall_score=75)),
        completed_at=NOW,
    )
    assert payload.delivery_id == "dlv-abc123"


def test_webhook_payload_no_pii():
    """WebhookPayload must not accept any PII fields."""
    with pytest.raises(ValidationError):
        WebhookPayload(
            delivery_id="dlv-1",
            external_reference_id="app-1",
            interview_status="completed",
            assessment=InterviewAssessment(**_make_assessment()),
            completed_at=NOW,
            candidate_email="x@y.com",
        )


def test_webhook_payload_invalid_status():
    with pytest.raises(ValidationError):
        WebhookPayload(
            delivery_id="dlv-1",
            external_reference_id="app-1",
            interview_status="pending",
            assessment=InterviewAssessment(**_make_assessment()),
            completed_at=NOW,
        )


def test_webhook_payload_round_trip_json():
    payload = WebhookPayload(
        delivery_id="dlv-abc",
        external_reference_id="app-789",
        interview_status="inconclusive",
        assessment=InterviewAssessment(
            **_make_assessment(evaluation_status="inconclusive", inconclusive=True)
        ),
        completed_at=NOW,
    )
    data = payload.model_dump(mode="json")
    restored = WebhookPayload.model_validate(data)
    assert restored.delivery_id == payload.delivery_id
    assert restored.assessment.inconclusive is True


# ---------------------------------------------------------------------------
# CandidateFeedbackRequest
# ---------------------------------------------------------------------------

def _make_feedback(**kwargs) -> dict:
    base = {
        "application_id": uuid.uuid4(),
        "decision": "accepted",
        "job_title": "Backend Engineer",
        "matched_requirements": ["Python"],
        "missing_requirements": [],
        "final_score": 0.9,
        "decided_at": NOW,
    }
    base.update(kwargs)
    return base


def test_candidate_feedback_valid():
    feedback = CandidateFeedbackRequest(**_make_feedback())
    assert feedback.decision == "accepted"
    assert feedback.final_score == 0.9


def test_candidate_feedback_rejected_decision():
    feedback = CandidateFeedbackRequest(**_make_feedback(decision="rejected"))
    assert feedback.decision == "rejected"


def test_candidate_feedback_invalid_decision_rejected():
    with pytest.raises(ValidationError):
        CandidateFeedbackRequest(**_make_feedback(decision="maybe"))


def test_candidate_feedback_score_out_of_range_rejected():
    with pytest.raises(ValidationError):
        CandidateFeedbackRequest(**_make_feedback(final_score=1.5))


def test_candidate_feedback_empty_job_title_rejected():
    with pytest.raises(ValidationError):
        CandidateFeedbackRequest(**_make_feedback(job_title=""))


def test_candidate_feedback_no_pii():
    """CandidateFeedbackRequest must not accept any PII fields."""
    with pytest.raises(ValidationError):
        CandidateFeedbackRequest(**_make_feedback(candidate_email="x@y.com"))


def test_candidate_feedback_extra_field_rejected():
    with pytest.raises(ValidationError):
        CandidateFeedbackRequest(**_make_feedback(candidate_name="Alice"))


def test_candidate_feedback_round_trip_json():
    feedback = CandidateFeedbackRequest(**_make_feedback())
    data = feedback.model_dump(mode="json")
    restored = CandidateFeedbackRequest.model_validate(data)
    assert restored.application_id == feedback.application_id
    assert restored.decision == feedback.decision
