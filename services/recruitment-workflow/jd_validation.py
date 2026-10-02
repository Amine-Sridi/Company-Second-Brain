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
module never calls Gemini itself, never re-scores anything, and never
determines `final_score`/`qualified`/threshold comparisons -- `scoring.py`
remains the only place those are decided, unchanged and untouched by this
module. The one automatic repair pass this project has (main.py's
`repair_structured_extraction`, run when this module's own
`validate_and_normalize` returns `"flagged"`) lives in main.py, not here, for
exactly that reason: it calls Gemini, so it cannot live in the one module
this codebase can point to and say "this cannot possibly call Gemini." This
module still decides, on the repaired result, whether it's actually clean --
`main.py` calls `validate_and_normalize` a second time on the repair
attempt's output rather than trusting Gemini's own claim that it fixed
anything.
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

# How many skills may sit behind the hard gate.
#
# required_skills feeds check_required_skills_gate, which a candidate must satisfy to
# qualify at all. Unbounded, the extraction returns every technology a posting mentions:
# one real job description produced 18, the strongest candidate in the pool matched 4 of
# them, and the gate therefore rejected all 20 retrieved candidates on all four jobs. A
# gate nobody can pass is not a gate, it is an unconditional reject.
#
# Six is a judgment call, not a measurement: it is the upper end of what a real recruiter
# brief treats as non-negotiable, and it leaves the graded gate (MIN_REQUIRED_COVERAGE in
# scoring.py) room to tolerate one miss without tolerating four.
MAX_REQUIRED_SKILLS = 6

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
    # Required skills normalize_extraction moved to preferred because the extraction
    # exceeded MAX_REQUIRED_SKILLS. Carried on the result, not inferred later, because
    # detect_warnings runs on already-normalized data and by then the evidence is gone.
    # Default empty so every existing constructor and test keeps working unchanged.
    demoted_required: list[str] = field(default_factory=list)
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
    intended.

    **Required skills beyond MAX_REQUIRED_SKILLS are demoted to preferred.** This is the
    one case where this step does change required_skills, and it is a deliberate
    narrowing of the rule that used to forbid that outright. The original reasoning was
    that only the recruiter should decide what is non-negotiable -- which is right, and
    is exactly why this applies only to an over-long list, which is never a recruiter's
    intent but an extraction defect: Gemini listing every technology a posting mentions.
    An 18-skill required list makes the hard gate an unconditional reject (measured: the
    best candidate matched 4 of 18, so all 20 retrieved candidates failed).

    Demotion is information-preserving, for the same reason the dedupe above is:
    compute_skill_score's target is the *union* of required and preferred, so a demoted
    skill still contributes identically to the score. Only what *gates* changes. The
    first MAX_REQUIRED_SKILLS are kept, in the extraction's own order, since that order
    is the only signal available about which it considered most important -- and
    detect_warnings flags the demotion so the repair pass gets a chance to choose better
    than "the first six".
    """
    normalized_required = _dedupe_trim(required_skills)
    normalized_preferred = _dedupe_trim(preferred_skills)

    demoted = normalized_required[MAX_REQUIRED_SKILLS:]
    normalized_required = normalized_required[:MAX_REQUIRED_SKILLS]
    normalized_preferred = demoted + normalized_preferred

    required_lower = {s.lower() for s in normalized_required}
    normalized_preferred = _dedupe_trim(
        [s for s in normalized_preferred if s.lower() not in required_lower]
    )
    normalized_titles = _dedupe_trim(titles)

    return NormalizedExtraction(
        required_skills=normalized_required,
        preferred_skills=normalized_preferred,
        demoted_required=demoted,
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

    if normalized.demoted_required:
        warnings.append(
            ExtractionWarning(
                field="required_skills",
                message=(
                    f"The extraction listed more than {MAX_REQUIRED_SKILLS} required "
                    f"skills, so these were moved to preferred and no longer gate "
                    f"qualification: {', '.join(normalized.demoted_required)}. They still "
                    f"count toward the match score. Keep only the skills whose absence "
                    f"would genuinely disqualify an applicant -- the ones kept were "
                    f"simply the first {MAX_REQUIRED_SKILLS} extracted, which is a "
                    f"positional guess, not a judgment about importance."
                ),
            )
        )

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
