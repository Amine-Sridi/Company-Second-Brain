"""Cross-service Pydantic contracts.

Privacy rule: CandidateSummary, RetrieveCandidatesRequest/Response, WebhookPayload, and
CandidateFeedbackRequest must never carry PII (name, email, phone, address). Only
ContactLookupResponse and InvitationRequest are intentionally PII-bearing — they are
used transiently by RWS and must never be persisted by the receiving service.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, UUID4


# ---------------------------------------------------------------------------
# CandidateSummary
# PII-free candidate knowledge written into Second Brain.
# ---------------------------------------------------------------------------

class CandidateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID4
    # Extracted skills; items are plain strings (no length cap per item enforced here —
    # Resume Intelligence owns the extraction and normalisation).
    skills: list[str] = Field(default_factory=list, max_length=200)
    years_of_experience: Optional[float] = Field(None, ge=0.0, le=100.0)
    job_titles: list[str] = Field(default_factory=list, max_length=50)
    # PII-stripped text used to generate the pgvector embedding.
    raw_text: str = Field(min_length=1, max_length=50_000)
    # Version tag written by Resume Intelligence so Second Brain can detect staleness.
    source_version: str = Field(default="1", max_length=40)


# ---------------------------------------------------------------------------
# ContactLookupResponse
# Resume Intelligence returns this to RWS; contains PII — never persist in callers.
# ---------------------------------------------------------------------------

class ContactLookupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID4
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone: Optional[str] = Field(None, max_length=30)


# ---------------------------------------------------------------------------
# RetrieveCandidatesRequest / Response
# RWS → Second Brain semantic retrieval.  No PII on either side.
# ---------------------------------------------------------------------------

class RetrieveCandidatesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_text: str = Field(min_length=1, max_length=10_000)
    top_k: int = Field(default=20, ge=1, le=100)
    # Optional pre-filter: restrict search to this subset of candidates.
    candidate_ids: Optional[list[UUID4]] = None


class CandidateMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID4
    similarity_score: float = Field(ge=0.0, le=1.0)


class RetrieveCandidatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[CandidateMatch]


# ---------------------------------------------------------------------------
# InvitationRequest / Response
# RWS → VoiceHR.  InvitationRequest is PII-bearing (transient use only).
# external_reference_id is echoed back in the outbound webhook so RWS can
# correlate the completion event with the right Application row.
# ---------------------------------------------------------------------------

class InvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_name: str = Field(min_length=2, max_length=100)
    candidate_email: EmailStr
    language: Literal["en", "fr"]
    # Opaque RWS-issued token; VoiceHR stores it and includes it in the webhook.
    external_reference_id: str = Field(min_length=1, max_length=100)
    # RWS's Job.title -- used only for the candidate invitation email's greeting
    # ("... for the {job_title} role ..."). VoiceHR has no other concept of a job;
    # never used for matching/scoring/eligibility. Optional so non-RWS callers of
    # POST /api/invite keep working without it.
    job_title: Optional[str] = Field(default=None, max_length=200)


class InvitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_reference_id: str = Field(min_length=1, max_length=100)
    interview_link: str = Field(min_length=1)
    expires_at: datetime


# ---------------------------------------------------------------------------
# WebhookPayload
# VoiceHR → RWS HMAC-signed completion notification.  No PII.
# The HMAC signature is passed in the X-VoiceHR-Signature HTTP header, not here.
# ---------------------------------------------------------------------------

class InterviewAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_score: Optional[int] = Field(None, ge=0, le=100)
    # The language VoiceHR actually conducted/evaluated the interview in (its own
    # `interviews.language` column, echoed back here) -- distinct from a job's
    # *required* language, which RWS alone owns and snapshots on the Application at
    # selection time (see recruitment-workflow/language_gate.py). Optional so a
    # webhook replayed from before this field existed still validates.
    language: Optional[Literal["en", "fr"]] = None
    cefr_level: Optional[str] = Field(None, max_length=10)
    fluency_score: Optional[int] = Field(None, ge=0, le=100)
    vocabulary_score: Optional[int] = Field(None, ge=0, le=100)
    grammar_score: Optional[int] = Field(None, ge=0, le=100)
    pronunciation_score: Optional[int] = Field(None, ge=0, le=100)
    comprehension_score: Optional[int] = Field(None, ge=0, le=100)
    inconclusive: bool = False
    evaluation_status: Literal["completed", "failed", "inconclusive"]


class WebhookPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Unique per delivery; RWS uses this to deduplicate retried webhooks.
    delivery_id: str = Field(min_length=1, max_length=100)
    external_reference_id: str = Field(min_length=1, max_length=100)
    interview_status: Literal["completed", "inconclusive", "failed"]
    assessment: InterviewAssessment
    completed_at: datetime


# ---------------------------------------------------------------------------
# CandidateFeedbackRequest
# RWS → Second Brain, fire-and-forget after a recruiter's accept/reject decision.
# No PII: only the job title, which required skills matched/were missing, and the
# score/outcome. candidate_id is the URL path parameter, not part of this body.
# Idempotency key is application_id -- a decision happens at most once per
# application (the state machine is terminal), so the receiving upsert is keyed on it.
# ---------------------------------------------------------------------------

class CandidateFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: UUID4
    decision: Literal["accepted", "rejected"]
    job_title: str = Field(min_length=1, max_length=200)
    matched_requirements: list[str] = Field(default_factory=list, max_length=50)
    missing_requirements: list[str] = Field(default_factory=list, max_length=50)
    final_score: float = Field(ge=0.0, le=1.0)
    decided_at: datetime
