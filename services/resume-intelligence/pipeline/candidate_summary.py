"""
pipeline/candidate_summary.py
-------------------------------
Builds the PII-safe shared.schemas.CandidateSummary sent to Second Brain, from
the already-normalized candidate_json produced by pipeline.parsing_agent and
stored by db.store_candidate.

PII boundary, enforced by construction, not by convention
-----------------------------------------------------------
build_candidate_summary() reads ONLY an explicit allow-list of professional
fields off candidate_json: work[].position/company/tech_stack/description,
education[], skills, certifications, spoken_languages. It never reads
candidate_json["basics"] -- the one section that holds name/email/phone/
location/links -- and that section's keys do not appear anywhere in this
file. This is a positive allow-list, not "everything except basics": adding a
new field to candidate_json in the future does NOT automatically leak into
CandidateSummary, since nothing here does a blanket copy.

CandidateSummary itself also independently enforces this at the schema level
(extra="forbid", no name/email/phone/address fields exist on the model at
all) -- this module is the second, earlier layer of the same guarantee.
"""
from __future__ import annotations

import uuid

from pipeline.parsing_agent import years_of_experience_from_work
from shared.schemas import CandidateSummary

# Bumped when this builder's field selection or synthesized-text format changes,
# so Second Brain can (in principle) detect staleness of a given candidate's
# knowledge relative to the current extraction/summary pipeline.
SUMMARY_SOURCE_VERSION = "1"


def _dedup_preserve_casing(items: list[str]) -> list[str]:
    """Case-insensitive dedup preserving first-seen display casing. No taxonomy,
    no synonym resolution -- same rule as pipeline.parsing_agent's own skill
    normalization."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in items:
        cleaned = (raw or "").strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _flatten_skills(candidate_json: dict) -> list[str]:
    """
    CandidateSummary has a single 'skills' field and no separate
    certifications field, so certifications are folded into it here rather
    than silently dropped. Deduped against skills too (case-insensitive).
    """
    skills = candidate_json.get("skills") or []
    certifications = candidate_json.get("certifications") or []
    return _dedup_preserve_casing([*skills, *certifications])


def _job_titles(candidate_json: dict) -> list[str]:
    """Distinct job titles (work[].position), most-recent first, as extracted --
    no title normalization/taxonomy."""
    titles = [job.get("position") for job in candidate_json.get("work") or []]
    return _dedup_preserve_casing([t for t in titles if t])


def _synthesize_raw_text(candidate_json: dict) -> str:
    """
    Build the retrieval/embedding text from allow-listed professional fields
    only. This is NEVER the original extracted resume text, and never touches
    candidate_json["basics"] -- the section holding name/email/phone/location/
    links -- so no identity *field* can reach this string.

    Known limit, stated precisely rather than overclaimed: the allow-list is
    field-level, not content-level. work[].description and education[] entries are
    free text Gemini extracted from the resume, so a resume that writes contact
    details inside a bullet ("reachable at ...") could carry them through here.
    Scrubbing that would need content inspection (regex/NER) over extracted text,
    which is a real feature, not a tweak -- deliberately not built. What this
    function does guarantee is that no *structured* identity field is ever copied.
    """
    lines: list[str] = []

    for job in candidate_json.get("work") or []:
        position = job.get("position") or "Unknown role"
        company = job.get("company") or "Unknown company"
        segment = [f"{position} at {company}."]
        tech_stack = job.get("tech_stack") or []
        if tech_stack:
            segment.append(f"Tech stack: {', '.join(tech_stack)}.")
        description = job.get("description")
        if description:
            segment.append(description)
        lines.append(" ".join(segment))

    for edu in candidate_json.get("education") or []:
        degree = edu.get("degree")
        field = edu.get("field")
        institution = edu.get("institution")
        label = " ".join(p for p in (degree, field) if p)
        if institution and label:
            lines.append(f"{label}, {institution}.")
        elif institution:
            lines.append(f"{institution}.")
        elif label:
            lines.append(f"{label}.")

    skills = _flatten_skills(candidate_json)
    if skills:
        lines.append("Skills: " + ", ".join(skills) + ".")

    spoken_bits = []
    for entry in candidate_json.get("spoken_languages") or []:
        language = entry.get("language")
        if not language:
            continue
        level = entry.get("level")
        spoken_bits.append(f"{language} ({level})" if level else language)
    if spoken_bits:
        lines.append("Spoken languages: " + ", ".join(spoken_bits) + ".")

    text = "\n".join(line for line in lines if line and line.strip())
    return text or "No professional experience information was extracted from this resume."


def build_candidate_summary(candidate_id: uuid.UUID, candidate_json: dict) -> CandidateSummary:
    """
    Build the PII-safe CandidateSummary for candidate_id from candidate_json
    (Resume Intelligence's own stored extraction -- see db.Candidate.candidate_json).

    candidate_id must be the exact Resume Intelligence candidate id, preserving
    opaque identity across the service boundary -- callers pass the id returned
    by db.store_candidate, never a new one minted here.
    """
    work = candidate_json.get("work") or []
    return CandidateSummary(
        candidate_id=candidate_id,
        skills=_flatten_skills(candidate_json),
        years_of_experience=years_of_experience_from_work(work),
        job_titles=_job_titles(candidate_json),
        raw_text=_synthesize_raw_text(candidate_json),
        source_version=SUMMARY_SOURCE_VERSION,
    )
