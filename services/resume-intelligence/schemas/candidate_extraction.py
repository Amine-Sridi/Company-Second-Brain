"""
schemas/candidate_extraction.py
--------------------------------
Structured-output contract for the resume-parsing LLM call.

This is the exact shape requested from Gemini via shared.llm.LLMProvider's
generate_structured (its generated JSON Schema is sent as the response schema, and
Gemini's JSON output is validated against this same model on the way back). It
supersedes the old schemas/candidate_schema.json JSON-Schema file and the separate
hand-typed example dict that used to live in pipeline/parsing_agent.py -- there is
now exactly one representation of "what shape must a resume extraction be."

Every field is optional and has no cross-field requirement: the LLM only extracts
what is explicitly present in the resume text, and a resume missing some section
(no education, no LinkedIn, one job with no end date) is still a valid extraction,
not a failure. Whether a field's absence matters is judged afterwards,
deterministically, by pipeline.parsing_agent.find_missing_fields -- this model
performs no scoring, matching, or recruitment decisions, extraction only.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CandidateLinks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    linkedin: Optional[str] = Field(None, max_length=300)
    github: Optional[str] = Field(None, max_length=300)
    portfolio: Optional[str] = Field(None, max_length=300)


class CandidateBasics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=200)
    phone: Optional[str] = Field(None, max_length=50)
    job_title: Optional[str] = Field(None, max_length=200, description="Current or most recent job title")
    location: Optional[str] = Field(None, max_length=200, description="City / region")
    links: CandidateLinks = Field(default_factory=CandidateLinks)


class WorkExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: Optional[str] = Field(None, max_length=300, description="Employer name (ESN/SSII or direct employer)")
    client: Optional[str] = Field(None, max_length=300, description="End-client if deployed by a consulting firm")
    position: Optional[str] = Field(None, max_length=300, description="Job title for this role")
    start_date: Optional[str] = Field(None, max_length=10, description="YYYY-MM format")
    end_date: Optional[str] = Field(None, max_length=10, description="YYYY-MM format, or null if current")
    description: Optional[str] = Field(None, max_length=4000)
    tech_stack: list[str] = Field(
        default_factory=list,
        description="All tools, languages, frameworks mentioned for this role",
    )
    location: Optional[str] = Field(None, max_length=200)


class EducationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    institution: Optional[str] = Field(None, max_length=300)
    degree: Optional[str] = Field(None, max_length=200)
    field: Optional[str] = Field(None, max_length=200)
    start_date: Optional[str] = Field(None, max_length=10)
    end_date: Optional[str] = Field(None, max_length=10)


class SpokenLanguage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str = Field(max_length=100)
    level: Optional[str] = Field(None, max_length=50)


class CandidateExtraction(BaseModel):
    """
    The full structured-extraction contract requested from Gemini for one resume.

    Two deliberate shape choices, both forced by empirically bisecting a real
    400 INVALID_ARGUMENT (no further detail) from Gemini's structured-output
    schema compiler against this exact SDK/model:

    1. skills/certifications/spoken_languages are flat, top-level fields rather
       than nested under one "skills" object. A categorized skills object
       (languages/frameworks/tools/certifications sub-lists, as the old
       schemas/candidate_schema.json had) triggered the 400 once combined with
       sibling top-level fields -- flattening resolved it, and is arguably
       better extraction quality besides: it stops asking the LLM to make an
       often-arbitrary languages/frameworks/tools categorization call.
    2. None of this model's list fields (work, education, skills,
       certifications, spoken_languages) carry a max_length/maxItems
       constraint. Multiple top-level array fields each with their own maxItems
       reproduced the same 400 even after flattening; removing maxItems from
       every top-level list (list-length bounds on nested fields, e.g. inside
       WorkExperience, were never implicated and are not needed here anyway)
       resolved it. Confirmed against the real configured model
       (gemini-3.6-flash) with a real sample resume, including an embedded
       prompt-injection attempt that was correctly ignored.
    """

    model_config = ConfigDict(extra="forbid")

    basics: CandidateBasics = Field(default_factory=CandidateBasics)
    work: list[WorkExperience] = Field(
        default_factory=list,
        description="Work experience, one entry per position, most-recent first",
    )
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[str] = Field(
        default_factory=list,
        description="All languages, frameworks, tools, and platforms mentioned anywhere in the resume",
    )
    certifications: list[str] = Field(default_factory=list)
    spoken_languages: list[SpokenLanguage] = Field(default_factory=list)
