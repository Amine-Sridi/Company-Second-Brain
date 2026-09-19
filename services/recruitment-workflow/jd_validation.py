"""
Deterministic JD-extraction validation and normalization.

Pure functions only -- no DB, no HTTP, no LLM calls, the same discipline
`scoring.py` follows and for the same reason: this is a place a student can point
to and say "this cannot possibly call Gemini or touch a score," not just "it
currently doesn't." Runs immediately after Gemini's JD structuring
(`main.py: structure_job_description`) and before anything is persisted or
matched -- normalizes the extraction for storage/display, then flags (never
blocks) anything that looks unreliable.

Deliberately narrow, per CLAUDE.md's "no infra ahead of demonstrated need": this
module never calls Gemini itself (no automatic repair pass yet -- see
handoff.md's `[NEXT]`), never re-scores anything, and never determines
`final_score`/`qualified`/threshold comparisons -- `scoring.py` remains the only
place those are decided, unchanged and untouched by this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Hard-gate realism: check_required_skills_gate (scoring.py) requires an exact,
# case-insensitive match for every required skill -- no fuzzy/substring matching.
# A required-skill entry that reads like a responsibility phrase rather than a
# literal skill/tool name will not match any real candidate's Second Brain skill
# list, no matter how qualified they are. These thresholds are a cheap proxy for
# "this looks like a paraphrase, not a skill name" -- real atomic skill names
# ("Python", "Google Cloud Platform", "CI/CD pipelines") are short; responsibility
# phrases ("building and deploying REST APIs") are not.
_PHRASE_LIKE_MAX_WORDS = 4
_PHRASE_LIKE_MAX_CHARS = 40

# Above this many years, a stated minimum is unusual enough to be worth a second
# look (a legitimate senior/leadership role can genuinely ask for 20+ years, so
# this is a warning, never a rejection).
_SUSPICIOUS_MIN_YEARS_EXPERIENCE = 25

# Above this many target title variants, the list is more likely over-broad or
# hallucinated than a real, deliberate set of equivalent titles for one role.
_MAX_REASONABLE_TITLE_VARIANTS = 6


@dataclass
class ExtractionWarning:
    """One flagged concern about a specific field of a JD extraction -- never a
    correction, only an observation for a recruiter to optionally act on."""

    field: str
    message: str

    def to_dict(self) -> dict:
        return {"field": self.field, "message": self.message}


@dataclass
class NormalizedExtraction:
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    min_years_experience: Optional[float] = None
    titles: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    normalized: NormalizedExtraction
    status: str  # "clean" | "flagged"
    warnings: list[ExtractionWarning]


def _dedupe_trim(values: list[str]) -> list[str]:
    """Trims whitespace, drops empty/whitespace-only entries, and de-duplicates
    case-insensitively -- the first-seen casing is kept, since scoring.py's own
    matching is already case-insensitive (this is purely for clean storage/
    display, never a behavior-affecting change)."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        trimmed = value.strip()
        if not trimmed:
            continue
        key = trimmed.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(trimmed)
    return result


def normalize_extraction(
    required_skills: list[str],
    preferred_skills: list[str],
    min_years_experience: Optional[float],
    titles: list[str],
) -> NormalizedExtraction:
    """
    Trim/dedupe every list field, then remove any required_skills entry from
    preferred_skills (case-insensitive) -- required already implies preferred, so
    listing the same skill in both is redundant, never a real distinction Gemini
    intended. Never removes anything from required_skills itself: that list is
    the hard gate, and only the recruiter (via a new job) or the future repair
    pass gets to change what's actually required, not this normalization step.

    Does not touch scoring: compute_skill_score's target is the *union* of
    required and preferred, so removing a required-duplicate from preferred
    changes nothing about what candidates qualify -- only what's stored and
    displayed.
    """
    normalized_required = _dedupe_trim(required_skills)
    normalized_preferred = _dedupe_trim(preferred_skills)
    required_lower = {s.lower() for s in normalized_required}
    normalized_preferred = [s for s in normalized_preferred if s.lower() not in required_lower]
    normalized_titles = _dedupe_trim(titles)

    return NormalizedExtraction(
        required_skills=normalized_required,
        preferred_skills=normalized_preferred,
        min_years_experience=min_years_experience,
        titles=normalized_titles,
    )


def _is_phrase_like(skill: str) -> bool:
    return len(skill) > _PHRASE_LIKE_MAX_CHARS or len(skill.split()) > _PHRASE_LIKE_MAX_WORDS


def detect_warnings(normalized: NormalizedExtraction) -> list[ExtractionWarning]:
    """
    Runs on already-normalized data. Every check here is a proxy, not a certainty
    -- each one is a *warning*, never a rejection, per this task's own "do not
    block job creation" rule. Order matches the task's own list.
    """
    warnings: list[ExtractionWarning] = []

    phrase_like = [s for s in normalized.required_skills if _is_phrase_like(s)]
    if phrase_like:
        warnings.append(
            ExtractionWarning(
                field="required_skills",
                message=(
                    "These required skills look like responsibility phrases, not "
                    "literal skill/tool names, and may not match any real candidate "
                    f"under an exact-match hard gate: {phrase_like}."
                ),
            )
        )

    if (
        not normalized.required_skills
        and not normalized.preferred_skills
        and not normalized.titles
        and normalized.min_years_experience is None
    ):
        warnings.append(
            ExtractionWarning(
                field="extraction",
                message=(
                    "No required skills, preferred skills, titles, or experience "
                    "requirement were extracted at all -- this job will match almost "
                    "any candidate on semantic similarity alone. Verify the pasted "
                    "description actually contained requirements."
                ),
            )
        )

    if normalized.min_years_experience is not None and normalized.min_years_experience > _SUSPICIOUS_MIN_YEARS_EXPERIENCE:
        warnings.append(
            ExtractionWarning(
                field="min_years_experience",
                message=(
                    f"{normalized.min_years_experience} years of required experience is "
                    "unusually high -- verify Gemini didn't misread a date, a company "
                    "founding year, or another figure as a years-of-experience requirement."
                ),
            )
        )

    if len(normalized.titles) > _MAX_REASONABLE_TITLE_VARIANTS:
        warnings.append(
            ExtractionWarning(
                field="titles",
                message=(
                    f"{len(normalized.titles)} target title variants were extracted -- "
                    "this may be over-broad or include titles that don't actually "
                    "describe the same role."
                ),
            )
        )

    return warnings


def validate_and_normalize(
    required_skills: list[str],
    preferred_skills: list[str],
    min_years_experience: Optional[float],
    titles: list[str],
) -> ValidationResult:
    """The one entry point main.py's submit_job calls: normalize first, then
    detect warnings on the normalized result (so a warning is never triggered by
    formatting noise the normalization step would have cleaned up anyway)."""
    normalized = normalize_extraction(required_skills, preferred_skills, min_years_experience, titles)
    warnings = detect_warnings(normalized)
    status = "flagged" if warnings else "clean"
    return ValidationResult(normalized=normalized, status=status, warnings=warnings)
