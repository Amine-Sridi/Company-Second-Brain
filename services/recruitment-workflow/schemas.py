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

# Mirrors VoiceHR's own supported interview languages (Literal["en", "fr"] in
# backend/database.py's CHECK constraint) and language_gate.CEFR_RANK's keys. Kept as
# plain Literal aliases (not an import from language_gate, which is a pure-logic module
# with no Pydantic dependency) so this file's own validators stay self-contained.
LanguageCode = Literal["en", "fr"]
CefrLevel = Literal["A1", "A2", "B1", "B2", "C1", "C2"]


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

def _validate_paired_language_requirement(
    required_language: Optional[str], min_language_cefr: Optional[str]
) -> None:
    """Shared rule for every request shape below that carries this pair: either both
    are set (a real requirement) or neither is -- never a CEFR floor with no language,
    or a language with no floor. Mirrors the DB's own paired CHECK constraint
    (alembic/versions/0006_job_language_requirement.py) as defense in depth."""
    if (required_language is None) != (min_language_cefr is None):
        raise ValueError(
            "required_language and min_language_cefr must be set together, or both left unset"
        )


class JobSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    raw_description: str = Field(min_length=1, max_length=20_000)
    threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    top_k: int = Field(default=20, ge=1, le=100)
    scoring_config_version: Optional[int] = Field(default=None, ge=1)
    # Optional VoiceHR language eligibility requirement (see language_gate.py). Gemini
    # never sees or sets these -- explicit recruiter input only, same as `threshold`.
    required_language: Optional[LanguageCode] = None
    min_language_cefr: Optional[CefrLevel] = None

    @model_validator(mode="after")
    def _language_requirement_paired(self) -> "JobSubmissionRequest":
        _validate_paired_language_requirement(self.required_language, self.min_language_cefr)
        return self


class JobLanguageRequirementRequest(BaseModel):
    """PATCH .../language-requirement body -- configures or clears (both null) an
    existing job's requirement. Deliberately its own small request shape rather than
    reusing JobSubmissionRequest: this endpoint only ever touches these two fields."""

    model_config = ConfigDict(extra="forbid")

    required_language: Optional[LanguageCode] = None
    min_language_cefr: Optional[CefrLevel] = None

    @model_validator(mode="after")
    def _language_requirement_paired(self) -> "JobLanguageRequirementRequest":
        _validate_paired_language_requirement(self.required_language, self.min_language_cefr)
        return self


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


class ExtractionWarningResponse(BaseModel):
    """One flagged concern about a JD extraction (jd_validation.py) -- an
    observation for a recruiter to optionally act on, never a correction: the
    field it names still holds whatever was actually extracted and used for
    matching."""

    model_config = ConfigDict(extra="forbid")

    field: str
    message: str


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
    required_language: Optional[str] = None
    min_language_cefr: Optional[str] = None
    extraction_status: Literal["clean", "flagged", "repaired"] = "clean"
    extraction_warnings: list[ExtractionWarningResponse] = Field(default_factory=list)
    matches: list[MatchResultResponse]


class JobSummaryResponse(BaseModel):
    """Lightweight row for the recruiter-facing GET /v1/jobs list -- deliberately not
    JobResponse: no requirements/threshold/matches, just enough to identify a job and
    jump into it (plus the language requirement, which Job Detail/List both display).
    match_count is the number of persisted MatchResults for the job.
    extraction_status/extraction_warnings (jd_validation.py) are what
    `JobsListPage`'s badge and `JobDetailPage`'s banner both read -- both pages
    get this from this same list response, RWS has no dedicated
    `GET /v1/jobs/{id}` (see FRONTEND_HANDOFF.md)."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID4
    title: str = Field(min_length=1, max_length=200)
    created_at: datetime
    match_count: int = Field(ge=0)
    required_language: Optional[str] = None
    min_language_cefr: Optional[str] = None
    extraction_status: Literal["clean", "flagged", "repaired"] = "clean"
    extraction_warnings: list[ExtractionWarningResponse] = Field(default_factory=list)


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

class LanguageGateInfo(BaseModel):
    """
    The deterministic VoiceHR language eligibility gate's current state for one
    application (see language_gate.py) -- computed fresh on every read from
    Application.required_language/required_cefr (the selection-time snapshot) and
    Application.interview_assessment's language/cefr_level (VoiceHR's webhook-reported
    result), never stored as its own column. required_* is null only when this
    application's job had no requirement configured at selection time (status is then
    always "not_required"); assessed_* is null until a terminal interview result exists.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["not_required", "pending", "passed", "failed", "needs_review"]
    required_language: Optional[str] = None
    required_cefr: Optional[str] = None
    assessed_language: Optional[str] = None
    assessed_cefr: Optional[str] = None


class InterviewRoundResponse(BaseModel):
    """
    One technical/managerial interview round (see db.InterviewRound's own
    docstring for why this is a parallel signal, never a new Application.status
    value). `outcome` is null unless `status == "completed"` -- enforced by a DB
    CHECK constraint, mirrored here only as a type, not re-validated.

    interviewer_email/location are Optional despite being required on the request
    schema below: a round created before migration 0010 has neither (nullable at
    the DB level -- see that migration's own docstring for why there's no sensible
    backfill default). notification_status/notification_error record the outcome
    of the best-effort candidate+interviewer email send (interview_round_mailer.py)
    -- purely advisory, never re-validated or re-derived here, just read back.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID4
    application_id: UUID4
    round_type: Literal["technical", "managerial"]
    status: Literal["scheduled", "completed", "cancelled"]
    outcome: Optional[Literal["pass", "fail", "needs_review"]] = None
    interviewer_name: str
    interviewer_email: Optional[str] = None
    location: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    notes: Optional[str] = None
    notification_status: Literal["pending", "sent", "failed"] = "pending"
    notification_error: Optional[str] = None
    created_by: UUID4
    created_at: datetime
    updated_at: datetime


class InterviewRoundRequest(BaseModel):
    """POST /v1/applications/{id}/interview-rounds' request body -- schedules a new
    round in the 'scheduled' state. There is no `status`/`outcome` here: a round is
    always created scheduled, with no verdict yet (see the lifecycle actions below --
    reschedule/complete/cancel).

    interviewer_email is required (EmailStr, so a malformed address is rejected here
    with a 422 rather than failing later at send time) -- every round scheduled from
    here on is expected to actually notify its interviewer; see handoff.md for the
    phase-1 automated-notification design this supports. location is optional free
    text (a meeting URL or a physical address, recruiter's choice) -- not split into
    separate fields, the same "unstructured over speculative structure" treatment
    interviewer_name/notes already get.
    """

    model_config = ConfigDict(extra="forbid")

    round_type: Literal["technical", "managerial"]
    interviewer_name: str = Field(min_length=1, max_length=200)
    interviewer_email: EmailStr
    location: Optional[str] = Field(None, max_length=500)
    scheduled_at: datetime
    notes: Optional[str] = Field(None, max_length=2000)


class RescheduleInterviewRoundRequest(BaseModel):
    """.../interview-rounds/{round_id}/reschedule's request body -- only ever
    moves scheduled_at, never touches round_type/interviewer_name/status."""

    model_config = ConfigDict(extra="forbid")

    scheduled_at: datetime


class CompleteInterviewRoundRequest(BaseModel):
    """.../interview-rounds/{round_id}/complete's request body -- outcome is
    required (never null): a round only reaches 'completed' with a verdict already
    attached, matching migration 0007's paired outcome/status CHECK constraint at
    the API boundary instead of only at the DB. `notes` is optional -- omitting it
    leaves whatever was recorded at scheduling time untouched (see
    main.py: complete_interview_round_endpoint)."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["pass", "fail", "needs_review"]
    notes: Optional[str] = Field(None, max_length=2000)


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
    language_gate: LanguageGateInfo
    interview_rounds: list[InterviewRoundResponse] = Field(default_factory=list)


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

    # Only used when the application's job has no language requirement snapshotted --
    # main.py's invite_application overrides this with that snapshot when one exists,
    # so VoiceHR always interviews in the exact language the gate will later compare
    # against (see language_gate.py).
    language: Literal["en", "fr"] = "en"


# ---------------------------------------------------------------------------
# Candidate/job knowledge graph (recruiter-facing, RWS-native -- no cross-service
# call, no new persistence). Built entirely from already-persisted MatchResult +
# Job rows (see db.py: list_all_match_results) -- a read of the existing audit
# trail, never a recomputation of it. Candidate nodes are deliberately not
# enumerated here: RWS has no display-worthy candidate data of its own (name lives
# in Resume Intelligence), so the frontend derives the candidate-node set from
# `edges[].candidate_id` and resolves names via the already-existing
# GET /v1/candidates in a second call, rather than this endpoint proxying Resume
# Intelligence for no reason.
# ---------------------------------------------------------------------------

class GraphJobNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID4
    title: str = Field(min_length=1, max_length=200)


class GraphEdge(BaseModel):
    """One immutable MatchResult, exactly as scored -- qualified and disqualified
    matches are both included (see get_candidates_jobs_graph's own docstring).
    Every field here is copied straight off MatchResult; nothing is recomputed."""

    model_config = ConfigDict(extra="forbid")

    match_id: UUID4
    candidate_id: UUID4
    job_id: UUID4
    final_score: float
    qualified: bool
    hard_gate_passed: bool
    matched_requirements: list[str]
    missing_requirements: list[str]


class CandidateJobGraphResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: list[GraphJobNode]
    edges: list[GraphEdge]
