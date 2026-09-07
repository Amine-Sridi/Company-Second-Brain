"""
RWS-local Pydantic contracts. These are internal to this service's own API surface
(recruiter-facing JD submission, scoring config admin, match inspection) — not shared
cross-service contracts, which live in /shared/schemas.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import UUID4, BaseModel, ConfigDict, EmailStr, Field, model_validator

_WEIGHT_SUM_TOLERANCE = 1e-6


# ---------------------------------------------------------------------------
# JD structuring (LLM output shape — structure only, never score/qualify)
# ---------------------------------------------------------------------------

class StructuredJobDescription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_skills: list[str] = Field(default_factory=list, max_length=50)
    preferred_skills: list[str] = Field(default_factory=list, max_length=50)
    min_years_experience: Optional[float] = Field(None, ge=0.0, le=60.0)
    titles: list[str] = Field(default_factory=list, max_length=20)


# ---------------------------------------------------------------------------
# Job submission
# ---------------------------------------------------------------------------

class JobSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    raw_description: str = Field(min_length=1, max_length=20_000)
    threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    top_k: int = Field(default=20, ge=1, le=100)
    scoring_config_version: Optional[int] = Field(default=None, ge=1)


class MatchResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_id: UUID4
    candidate_id: UUID4
    semantic_score: float
    skill_score: float
    experience_score: float
    title_score: float
    deterministic_score: float
    final_score: float
    hard_gate_passed: bool
    matched_requirements: list[str]
    missing_requirements: list[str]
    qualified: bool
    explanation: Optional[str] = None


class JobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID4
    job_description_id: UUID4
    version: int
    title: str
    required_skills: list[str]
    preferred_skills: list[str]
    min_years_experience: Optional[float]
    titles: list[str]
    threshold: float
    scoring_config_version: int
    matches: list[MatchResultResponse]


class JobSummaryResponse(BaseModel):
    """Lightweight row for the recruiter-facing GET /v1/jobs list -- deliberately not
    JobResponse: no requirements/threshold/matches, just enough to identify a job and
    jump into it. match_count is the number of persisted MatchResults for the job."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID4
    title: str = Field(min_length=1, max_length=200)
    created_at: datetime
    match_count: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Scoring config admin
# ---------------------------------------------------------------------------

class ScoringConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weight_semantic: float = Field(ge=0.0, le=1.0)
    weight_deterministic: float = Field(ge=0.0, le=1.0)
    skill_weight: float = Field(ge=0.0, le=1.0)
    experience_weight: float = Field(ge=0.0, le=1.0)
    title_weight: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "ScoringConfigRequest":
        hybrid_sum = self.weight_semantic + self.weight_deterministic
        if abs(hybrid_sum - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                f"weight_semantic + weight_deterministic must sum to 1.0, got {hybrid_sum}"
            )
        component_sum = self.skill_weight + self.experience_weight + self.title_weight
        if abs(component_sum - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                "skill_weight + experience_weight + title_weight must sum to 1.0, "
                f"got {component_sum}"
            )
        return self


class ScoringConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID4
    version: int
    weight_semantic: float
    weight_deterministic: float
    skill_weight: float
    experience_weight: float
    title_weight: float
    created_at: datetime


# ---------------------------------------------------------------------------
# Recruiter authentication
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


# ---------------------------------------------------------------------------
# Candidate identity (recruiter-facing, transient proxy to Resume Intelligence)
#
# RWS's own data (MatchResult, Application) is deliberately PII-free -- every
# candidate reference is an opaque candidate_id. This is the one place RWS hands the
# recruiter UI a display name, fetched fresh per request from Resume Intelligence and
# never persisted here. Deliberately minimal: no phone, no raw resume text, no
# candidate_json -- see main.py's get_candidate_identity.
# ---------------------------------------------------------------------------

class CandidateIdentityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID4
    name: str = Field(min_length=1, max_length=200)
    email: Optional[EmailStr] = None


# ---------------------------------------------------------------------------
# Candidate selection + recruiter decisions (Application state machine)
# ---------------------------------------------------------------------------

class ApplicationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: UUID4
    job_id: UUID4
    match_result_id: UUID4
    candidate_id: UUID4
    status: Literal["selected", "invited", "interviewed", "accepted", "rejected"]
    selected_at: datetime
    invite_attempt_number: int
    invited_at: Optional[datetime]
    interview_status: Optional[Literal["completed", "inconclusive", "failed"]]
    interview_assessment: Optional[dict]
    interview_completed_at: Optional[datetime]
    decided_at: Optional[datetime]


class GlobalApplicationResponse(ApplicationResponse):
    """GET /v1/applications' row shape -- every ApplicationResponse field plus the
    job's title, since a global (unscoped) list needs to identify which job each row
    belongs to (the job-scoped GET /v1/jobs/{job_id}/applications doesn't need this,
    the job is already implied by the URL)."""

    job_title: str = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------------------
# Candidate list (recruiter-facing, transient proxy to Resume Intelligence)
#
# Same transient-proxy rule as CandidateIdentityResponse above: RWS fetches this
# fresh from Resume Intelligence per request and persists none of it.
# ---------------------------------------------------------------------------

class CandidateSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID4
    name: str = Field(min_length=1, max_length=200)
    email: Optional[EmailStr] = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Candidate professional knowledge (recruiter-facing, transient proxy to Second
# Brain -- reuses clients.get_candidate(), the same call POST /v1/jobs already
# makes per retrieved candidate during scoring). An explicit allow-list, not
# CandidateSummary minus a blocklist: no raw_text (the text Second Brain embeds --
# not PII, but not "useful structured data" either) and no source_version (an
# internal staleness-tracking tag, meaningless to a recruiter). Second Brain's own
# schema has no embedding field to even accidentally expose.
# ---------------------------------------------------------------------------

class CandidateKnowledgeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID4
    skills: list[str] = Field(default_factory=list, max_length=200)
    job_titles: list[str] = Field(default_factory=list, max_length=50)
    years_of_experience: Optional[float] = Field(None, ge=0.0, le=100.0)


# ---------------------------------------------------------------------------
# Interview invitation (RWS -> VoiceHR, gated on a Resume Intelligence contact lookup)
# ---------------------------------------------------------------------------

class InviteApplicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["en", "fr"] = "en"
