"""
tests/test_jd_validation.py
------------------------------
Pure unit tests for jd_validation.py -- no DB, no HTTP, no LLM, no mocking: every
case is plain data in, plain data out. Mirrors test_language_gate.py's own style
for the same reason: this is the one function (well, two) that decides whether a
JD extraction gets flagged, so every branch is covered directly rather than only
through a route-level test.
"""
from __future__ import annotations

from jd_validation import (
    detect_warnings,
    normalize_extraction,
    validate_and_normalize,
)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def test_normalize_trims_whitespace_and_drops_empty_entries():
    normalized = normalize_extraction(
        required_skills=["  Python ", "", "   ", "Docker"],
        preferred_skills=["  AWS"],
        min_years_experience=None,
        titles=[" Backend Engineer "],
    )
    assert normalized.required_skills == ["Python", "Docker"]
    assert normalized.preferred_skills == ["AWS"]
    assert normalized.titles == ["Backend Engineer"]


def test_normalize_dedupes_case_insensitively_keeping_first_seen_casing():
    normalized = normalize_extraction(
        required_skills=["Python", "python", "PYTHON", "Docker"],
        preferred_skills=[],
        min_years_experience=None,
        titles=["Engineer", "engineer"],
    )
    assert normalized.required_skills == ["Python", "Docker"]
    assert normalized.titles == ["Engineer"]


def test_normalize_removes_required_preferred_overlap_from_preferred_only():
    normalized = normalize_extraction(
        required_skills=["Python", "Docker"],
        preferred_skills=["python", "AWS", "docker"],
        min_years_experience=None,
        titles=[],
    )
    # Required stays exactly as given; only the duplicate is dropped from preferred.
    assert normalized.required_skills == ["Python", "Docker"]
    assert normalized.preferred_skills == ["AWS"]


def test_normalize_never_removes_anything_from_required_skills_itself():
    normalized = normalize_extraction(
        required_skills=["Python", "Python", "Docker"],
        preferred_skills=["Python"],
        min_years_experience=None,
        titles=[],
    )
    assert normalized.required_skills == ["Python", "Docker"]
    assert normalized.preferred_skills == []


def test_normalize_passes_min_years_experience_through_unchanged():
    normalized = normalize_extraction(
        required_skills=[], preferred_skills=[], min_years_experience=3.5, titles=[],
    )
    assert normalized.min_years_experience == 3.5


# ---------------------------------------------------------------------------
# detect_warnings: one test per rule, run on already-normalized data
# ---------------------------------------------------------------------------

def test_no_warnings_for_a_well_formed_extraction():
    normalized = normalize_extraction(
        required_skills=["Python", "Docker"],
        preferred_skills=["AWS"],
        min_years_experience=3.0,
        titles=["Backend Engineer", "Software Engineer"],
    )
    assert detect_warnings(normalized) == []


def test_phrase_like_required_skill_by_word_count():
    normalized = normalize_extraction(
        required_skills=["Python", "building and deploying REST APIs"],
        preferred_skills=[], min_years_experience=None, titles=[],
    )
    warnings = detect_warnings(normalized)
    assert len(warnings) == 1
    assert warnings[0].field == "required_skills"
    assert "building and deploying REST APIs" in warnings[0].message


def test_phrase_like_required_skill_by_character_length_alone():
    # Only 2 words (well under the word-count threshold) but over 40 characters --
    # isolates the length-based branch from the word-count-based one above.
    long_skill = "A" * 20 + " " + "B" * 25
    assert len(long_skill.split()) == 2
    assert len(long_skill) > 40
    normalized = normalize_extraction(
        required_skills=[long_skill], preferred_skills=[], min_years_experience=None, titles=[],
    )
    warnings = detect_warnings(normalized)
    assert len(warnings) == 1
    assert warnings[0].field == "required_skills"


def test_short_multi_word_skill_is_not_flagged():
    # A real, legitimate skill name that happens to be a few words -- must not
    # be flagged just for having more than one word.
    normalized = normalize_extraction(
        required_skills=["Google Cloud Platform"], preferred_skills=[], min_years_experience=None, titles=[],
    )
    assert detect_warnings(normalized) == []


def test_total_under_extraction_flagged_when_everything_is_empty():
    normalized = normalize_extraction(
        required_skills=[], preferred_skills=[], min_years_experience=None, titles=[],
    )
    warnings = detect_warnings(normalized)
    assert len(warnings) == 1
    assert warnings[0].field == "extraction"


def test_under_extraction_not_flagged_if_any_single_field_has_content():
    # Only min_years_experience is set -- not a total under-extraction.
    normalized = normalize_extraction(
        required_skills=[], preferred_skills=[], min_years_experience=2.0, titles=[],
    )
    assert not any(w.field == "extraction" for w in detect_warnings(normalized))

    # Only titles is set.
    normalized2 = normalize_extraction(
        required_skills=[], preferred_skills=[], min_years_experience=None, titles=["Engineer"],
    )
    assert not any(w.field == "extraction" for w in detect_warnings(normalized2))


def test_suspicious_experience_above_threshold_is_flagged():
    normalized = normalize_extraction(
        required_skills=["Python"], preferred_skills=[], min_years_experience=30.0, titles=[],
    )
    warnings = detect_warnings(normalized)
    assert len(warnings) == 1
    assert warnings[0].field == "min_years_experience"
    assert "30" in warnings[0].message


def test_experience_at_or_below_threshold_is_not_flagged():
    normalized = normalize_extraction(
        required_skills=["Python"], preferred_skills=[], min_years_experience=25.0, titles=[],
    )
    assert detect_warnings(normalized) == []


def test_too_many_title_variants_is_flagged():
    normalized = normalize_extraction(
        required_skills=["Python"], preferred_skills=[], min_years_experience=None,
        titles=["A", "B", "C", "D", "E", "F", "G"],  # 7 > max of 6
    )
    warnings = detect_warnings(normalized)
    assert len(warnings) == 1
    assert warnings[0].field == "titles"


def test_title_variant_count_at_threshold_is_not_flagged():
    normalized = normalize_extraction(
        required_skills=["Python"], preferred_skills=[], min_years_experience=None,
        titles=["A", "B", "C", "D", "E", "F"],  # exactly 6, the max reasonable count
    )
    assert detect_warnings(normalized) == []


def test_multiple_rules_can_fire_together():
    normalized = normalize_extraction(
        required_skills=["building and deploying REST APIs"],
        preferred_skills=[],
        min_years_experience=40.0,
        titles=["A", "B", "C", "D", "E", "F", "G"],
    )
    warnings = detect_warnings(normalized)
    fields = {w.field for w in warnings}
    assert fields == {"required_skills", "min_years_experience", "titles"}


# ---------------------------------------------------------------------------
# validate_and_normalize: the one entry point main.py calls
# ---------------------------------------------------------------------------

def test_validate_and_normalize_clean_extraction():
    result = validate_and_normalize(
        required_skills=["Python", "Docker"],
        preferred_skills=["AWS"],
        min_years_experience=3.0,
        titles=["Backend Engineer"],
    )
    assert result.status == "clean"
    assert result.warnings == []
    assert result.normalized.required_skills == ["Python", "Docker"]


def test_validate_and_normalize_flagged_extraction():
    result = validate_and_normalize(
        required_skills=[], preferred_skills=[], min_years_experience=None, titles=[],
    )
    assert result.status == "flagged"
    assert len(result.warnings) == 1
    assert result.warnings[0].field == "extraction"


def test_validate_and_normalize_runs_detection_on_normalized_data_not_raw():
    # Two "Python" entries (one lowercase) collapse to one during normalization --
    # detection must run on the deduped list, not raw input, so this stays clean
    # rather than somehow double-counting a duplicate as suspicious.
    result = validate_and_normalize(
        required_skills=["Python", "python"],
        preferred_skills=[],
        min_years_experience=None,
        titles=["Engineer"],
    )
    assert result.status == "clean"
    assert result.normalized.required_skills == ["Python"]


def test_extraction_warning_to_dict_shape():
    result = validate_and_normalize(
        required_skills=[], preferred_skills=[], min_years_experience=None, titles=[],
    )
    warning_dicts = [w.to_dict() for w in result.warnings]
    assert warning_dicts == [
        {
            "field": "extraction",
            "message": result.warnings[0].message,
        }
    ]
    assert set(warning_dicts[0].keys()) == {"field", "message"}
