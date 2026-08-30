"""
pipeline/parsing_agent.py
--------------------------
Structured extraction agent.

Responsibility
--------------
Take cleaned raw text from ExtractionResult and return a structured candidate
extraction conforming to schemas.candidate_extraction.CandidateExtraction, using
the project-wide Gemini LLMProvider (shared.llm.GeminiProvider) via
generate_structured. The LLM extracts information only -- it never computes
scores, confidence, or decisions; that arithmetic lives entirely below, in plain
deterministic Python, and never in the prompt.

Strategy: one generate_structured call per attempt, bounded retry loop (max 3
attempts) on provider/validation failure. No markdown-fence stripping and no
brace-counting JSON extraction -- generate_structured returns an already-validated
CandidateExtraction instance or raises.

Output contract
---------------
  ParseResult dataclass  (see bottom of file)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from schemas.candidate_extraction import CandidateExtraction
from shared.llm import LLMProvider, LLMProviderError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config (overrideable via config.py or env)
# ---------------------------------------------------------------------------
try:
    from config import PROMPTS_DIR
except ImportError:
    PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

# Maximum characters sent to the LLM (token guard)
_MAX_CHARS = 12_000

# Bounded retry: one generation + up to two retries on provider/validation failure,
# never unbounded.
MAX_RETRIES = 3

_DATE_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# Fields whose absence triggers a MISSING_CRITICAL_FIELD RED flag
_CRITICAL_FIELDS: list[tuple[str, str]] = [
    ("basics", "first_name"),
    ("basics", "last_name"),
    ("basics", "email"),
    ("basics", "phone"),
]

# Fields whose absence triggers a MISSING_IMPORTANT_FIELD YELLOW flag
_IMPORTANT_FIELDS: list[tuple[str, str]] = [
    ("basics", "job_title"),
]


# ===========================================================================
# 1.  Prompt construction
# ===========================================================================

def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _build_prompt(raw_text: str) -> tuple[str, bool]:
    """Build the full prompt string. Returns (prompt, truncated)."""
    system_prompt = _load_prompt("parsing_system.txt")
    user_template = _load_prompt("parsing_user.txt")

    truncated = False
    if len(raw_text) > _MAX_CHARS:
        raw_text = raw_text[:_MAX_CHARS]
        truncated = True

    user_prompt = user_template.replace("{RESUME_TEXT}", raw_text)
    return f"{system_prompt}\n\n{user_prompt}", truncated


# ===========================================================================
# 2.  Deterministic post-processing -- never done by the LLM
# ===========================================================================

def _clean_str(value: Optional[str]) -> Optional[str]:
    """Trim whitespace; an all-blank string is treated as absent."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _normalize_date(value: Optional[str]) -> tuple[Optional[str], bool]:
    """
    Return (normalized_value, was_invalid).

    A malformed date (anything not exactly YYYY-MM) is cleared to None rather than
    propagated -- it would otherwise silently confuse calculate_total_experience or
    get stored as an unusable value. Clearing it and flagging it is safer than
    guessing a correction.
    """
    value = _clean_str(value)
    if value is None:
        return None, False
    if _DATE_PATTERN.match(value):
        return value, False
    return None, True


def _dedup_skills(items: list[str]) -> list[str]:
    """Trim, drop blanks, and case-insensitive-dedup while keeping first-seen casing."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in items:
        cleaned = _clean_str(raw)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def normalize_candidate_data(data: dict) -> tuple[dict, list[dict]]:
    """
    Deterministic post-processing pass over a raw CandidateExtraction.model_dump().

    - trims every string field, treating blank strings as absent
    - drops empty skill entries and case-insensitive-dedups each skill bucket,
      preserving first-seen display casing (no taxonomy/synonym resolution)
    - validates every start_date/end_date as YYYY-MM, clearing (not guessing) any
      malformed value and recording a flag

    Returns (normalized_data, flags) where flags follow the same dict shape as
    find_missing_fields (type/field/color/message).
    """
    flags: list[dict] = []

    basics = data.get("basics") or {}
    for key in ("first_name", "last_name", "email", "phone", "job_title", "location"):
        basics[key] = _clean_str(basics.get(key))
    links = basics.get("links") or {}
    for key in ("linkedin", "github", "portfolio"):
        links[key] = _clean_str(links.get(key))
    basics["links"] = links
    data["basics"] = basics

    work = data.get("work") or []
    for job in work:
        for key in ("company", "client", "position", "description", "location"):
            job[key] = _clean_str(job.get(key))
        job["tech_stack"] = _dedup_skills(job.get("tech_stack") or [])
        for date_field in ("start_date", "end_date"):
            normalized, invalid = _normalize_date(job.get(date_field))
            job[date_field] = normalized
            if invalid:
                label = job.get("company") or "unknown"
                flags.append({
                    "type": "INVALID_DATE_NORMALIZED",
                    "field": f"work[{label}].{date_field}",
                    "color": "YELLOW",
                    "message": f"Malformed {date_field} for '{label}' was cleared (expected YYYY-MM)",
                })
    data["work"] = work

    education = data.get("education") or []
    for edu in education:
        for key in ("institution", "degree", "field"):
            edu[key] = _clean_str(edu.get(key))
        for date_field in ("start_date", "end_date"):
            normalized, invalid = _normalize_date(edu.get(date_field))
            edu[date_field] = normalized
            if invalid:
                label = edu.get("institution") or "unknown"
                flags.append({
                    "type": "INVALID_DATE_NORMALIZED",
                    "field": f"education[{label}].{date_field}",
                    "color": "YELLOW",
                    "message": f"Malformed {date_field} for '{label}' was cleared (expected YYYY-MM)",
                })
    data["education"] = education

    # skills/certifications/spoken_languages are flat, top-level fields -- see
    # schemas/candidate_extraction.py's CandidateExtraction docstring for why.
    data["skills"] = _dedup_skills(data.get("skills") or [])
    data["certifications"] = _dedup_skills(data.get("certifications") or [])

    cleaned_spoken: list[dict] = []
    seen_langs: set[str] = set()
    for entry in data.get("spoken_languages") or []:
        lang = _clean_str(entry.get("language"))
        if not lang:
            continue
        key = lang.lower()
        if key in seen_langs:
            continue
        seen_langs.add(key)
        cleaned_spoken.append({"language": lang, "level": _clean_str(entry.get("level"))})
    data["spoken_languages"] = cleaned_spoken

    return data, flags


# ===========================================================================
# 3.  Missing-field detection
# ===========================================================================

def find_missing_fields(data: dict) -> list[dict]:
    """
    Inspect the parsed candidate dict and return a list of flag dicts.

    Each flag dict has keys: type, field, color, message.
    """
    flags: list[dict] = []

    # --- Critical fields ---
    for section, key in _CRITICAL_FIELDS:
        value = data.get(section, {}).get(key)
        if not value:
            flags.append({
                "type":    "MISSING_CRITICAL_FIELD",
                "field":   f"{section}.{key}",
                "color":   "RED",
                "message": f"Field '{section}.{key}' is missing or null",
            })

    # --- Important fields ---
    for section, key in _IMPORTANT_FIELDS:
        value = data.get(section, {}).get(key)
        if not value:
            flags.append({
                "type":    "MISSING_IMPORTANT_FIELD",
                "field":   f"{section}.{key}",
                "color":   "YELLOW",
                "message": f"Field '{section}.{key}' is missing or null",
            })

    # --- LinkedIn check ---
    links = data.get("basics", {}).get("links") or {}
    if not links.get("linkedin"):
        flags.append({
            "type":    "MISSING_LINKEDIN",
            "field":   "basics.links.linkedin",
            "color":   "YELLOW",
            "message": "LinkedIn URL not found",
        })

    # --- Work experience checks ---
    for i, job in enumerate(data.get("work", [])):
        company = job.get("company") or f"job[{i}]"

        if not job.get("tech_stack"):
            flags.append({
                "type":    "MISSING_TECH_STACK",
                "field":   f"work[{company}].tech_stack",
                "color":   "ORANGE",
                "message": f"No tech stack listed for '{company}'",
            })

        if not job.get("start_date"):
            flags.append({
                "type":    "MISSING_DATE",
                "field":   f"work[{company}].start_date",
                "color":   "RED",
                "message": f"Missing start_date for '{company}'",
            })

    return flags


# ===========================================================================
# 4.  Experience calculation
# ===========================================================================

def _sum_experience_months(work_list: list[dict]) -> int:
    """
    Deterministic month-sum shared by calculate_total_experience (human string)
    and years_of_experience_from_work (float, for CandidateSummary) -- one
    calculation, two presentations. Never asked of the LLM.

    Overlapping jobs are not deduplicated (conservative overestimate).
    """
    try:
        from dateutil.relativedelta import relativedelta  # type: ignore
    except ImportError:
        logger.error("python-dateutil not installed. Run: pip install python-dateutil")
        return 0

    total_months = 0
    today_str = date.today().strftime("%Y-%m")

    for job in work_list:
        start_str = job.get("start_date")
        end_str   = job.get("end_date") or today_str
        if not start_str:
            continue
        try:
            s = date.fromisoformat(start_str + "-01")
            e = date.fromisoformat(end_str   + "-01")
            if e < s:
                continue
            delta = relativedelta(e, s)
            total_months += delta.years * 12 + delta.months
        except (ValueError, TypeError):
            logger.warning("Cannot parse dates for total-experience calculation")

    return total_months


def calculate_total_experience(work_list: list[dict]) -> str:
    """
    Sum all job durations and return a human-readable French string.

    Returns e.g. "5 ans 3 mois", "2 ans", "8 mois".
    """
    total_months = _sum_experience_months(work_list)
    years, months = divmod(total_months, 12)
    if years and months:
        return f"{years} an{'s' if years > 1 else ''} {months} mois"
    elif years:
        return f"{years} an{'s' if years > 1 else ''}"
    else:
        return f"{months} mois"


def years_of_experience_from_work(work_list: list[dict]) -> float:
    """
    Numeric years of experience for CandidateSummary.years_of_experience.

    Same deterministic month-sum as calculate_total_experience (see
    _sum_experience_months), expressed as a float instead of a human-readable
    string. Never computed by the LLM.
    """
    return round(_sum_experience_months(work_list) / 12, 1)


# ===========================================================================
# 5.  Output dataclass
# ===========================================================================

@dataclass
class ParseResult:
    """
    Output contract for the structured-extraction stage.

    Attributes
    ----------
    data              : The parsed candidate dict. Empty ONLY when success is False.
    success           : True if a schema-valid CandidateExtraction was obtained
                        (a valid extraction with missing optional fields still
                        counts as success). False means every retry attempt
                        failed -- callers must not store a candidate in this case.
    flags             : All flags raised (strings or dicts).
    total_experience  : Human-readable experience string, e.g. "5 ans 3 mois".
    confidence        : Overall confidence, computed deterministically from flags
                        (0.0 when success is False).
    attempts          : Number of LLM calls made.
    """
    data: dict
    success: bool
    flags: list = field(default_factory=list)
    total_experience: str = "N/A"
    confidence: float = 0.0
    attempts: int = 1


# ===========================================================================
# 6.  Public entry point
# ===========================================================================

async def parse_resume_text(
    llm: LLMProvider,
    raw_text: str,
    file_meta: Optional[dict] = None,
    *,
    model: Optional[str] = None,
) -> ParseResult:
    """
    Parse *raw_text* into a structured candidate dict using *llm*.

    Parameters
    ----------
    llm       : an already-constructed LLMProvider (Gemini in this project) --
                this module never constructs its own provider or client.
    raw_text  : Cleaned plain text from the extraction stage.
    file_meta : Optional metadata from text_extractor.parse_filename()
                {profil, job_board, name_from_file}.
    model     : Optional model override, forwarded to generate_structured.

    Returns
    -------
    ParseResult. If every attempt fails, ``success`` is False and ``data`` is
    empty -- callers (api.py) must treat this as a clean failure, never insert a
    placeholder candidate.
    """
    prompt, truncated = _build_prompt(raw_text)
    flags: list = ["TEXT_TRUNCATED"] if truncated else []

    attempts = 0
    data: dict = {}
    succeeded = False

    for attempt in range(1, MAX_RETRIES + 1):
        attempts = attempt
        try:
            logger.info("Calling Gemini structured extraction (attempt %d/%d)…", attempt, MAX_RETRIES)
            extraction = await llm.generate_structured(prompt, CandidateExtraction, model=model)
            # Never log the extracted data or the raw exception message elsewhere in
            # this function -- both can embed candidate PII (name/email/phone).
            data = extraction.model_dump(mode="json")
            succeeded = True
            break
        except LLMProviderError as exc:
            logger.warning(
                "Attempt %d/%d: structured extraction failed (%s)",
                attempt, MAX_RETRIES, type(exc).__name__,
            )
            flags.append(f"GEMINI_EXTRACTION_FAILED_attempt_{attempt}: {type(exc).__name__}")

    if not succeeded:
        flags.append("PARSING_AGENT_FAILED_ALL_RETRIES")
        logger.error("Structured extraction failed after %d attempt(s).", attempts)
        return ParseResult(data={}, success=False, flags=flags, attempts=attempts)

    data, normalize_flags = normalize_candidate_data(data)
    flags.extend(normalize_flags)
    flags.extend(find_missing_fields(data))

    # Inject file_meta into meta section if not already present
    if file_meta:
        meta = data.setdefault("meta", {})
        meta.setdefault("profil",    file_meta.get("profil"))
        meta.setdefault("job_board", file_meta.get("job_board"))
        # If the LLM didn't extract a name, use the filename name as a hint
        basics = data.setdefault("basics", {})
        name_from_file = file_meta.get("name_from_file", "")
        if name_from_file and not basics.get("first_name"):
            parts = name_from_file.split(" ", 1)
            basics["first_name"] = parts[0] if parts else None
            basics["last_name"]  = parts[1] if len(parts) > 1 else None

    total_exp = calculate_total_experience(data.get("work", []))
    confidence = 1.0 if not any(
        isinstance(f, dict) and f.get("color") == "RED" for f in flags
    ) else 0.7

    return ParseResult(
        data=data,
        success=True,
        flags=flags,
        total_experience=total_exp,
        confidence=confidence,
        attempts=attempts,
    )
