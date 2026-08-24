"""
RWS-local Pydantic contracts. These are internal to this service's own API surface
(recruiter-facing JD submission, scoring config admin, match inspection) — not shared
cross-service contracts, which live in /shared/schemas.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import UUID4, BaseModel, ConfigDict, Field, model_validator

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
