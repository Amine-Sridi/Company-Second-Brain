"""
Tests for the two changes that stop one unmatched token from rejecting a strong
candidate: the required-skills cap (jd_validation) and the graded hard gate (scoring).

The case these exist for is real and measured, not hypothetical. A consultant with 15.7
years of experience listing Power BI, SQL Server, SSAS, SSIS, Azure Synapse and Azure
Data Factory scored 0.549 against a brief requiring Power BI, SQL and SQL Server -- and
was disqualified with missing=["SQL"], because the gate demanded all three as exact
strings. Separately, a job description that mentioned 18 technologies produced 18
*required* skills, which no candidate in a 70-person pool could satisfy, so the gate
rejected all 20 retrieved candidates on all four such jobs.
"""
import pytest

from jd_validation import MAX_REQUIRED_SKILLS, normalize_extraction, validate_and_normalize
from scoring import ScoringWeights, check_required_skills_gate, score_candidate
from scoring import CandidateScoringInput, JobRequirements


# --------------------------------------------------------------------- the graded gate

def test_all_of_remains_the_default():
    """A caller that does not ask for a floor keeps the strict behaviour. Existing stored
    MatchResults were scored that way and nothing should loosen silently."""
    passed, matched, missing = check_required_skills_gate(["Python"], ["Python", "SQL"])
    assert passed is False
    assert matched == ["Python"]
    assert missing == ["SQL"]


def test_one_miss_of_three_passes_at_eighty_percent():
    """The motivating case: Power BI and SQL Server present, bare "SQL" absent."""
    candidate = ["Power BI", "SQL Server", "SSAS", "SSIS", "Azure Synapse"]
    required = ["Power BI", "SQL", "SQL Server"]

    strict, _, missing = check_required_skills_gate(candidate, required, min_coverage=1.0)
    graded, matched, missing_graded = check_required_skills_gate(
        candidate, required, min_coverage=0.8
    )

    assert strict is False, "precondition: this is the case that used to fail"
    # 2 of 3 is 0.667, below 0.8 -- so even graded, three required skills is unforgiving.
    assert graded is False
    assert missing_graded == ["SQL"]
    # Which is the point of capping required_skills: with five, one miss is 0.8 and passes.
    five = ["Power BI", "SQL", "SQL Server", "SSAS", "SSIS"]
    passed_five, _, missing_five = check_required_skills_gate(candidate, five, min_coverage=0.8)
    assert passed_five is True
    assert missing_five == ["SQL"]


def test_four_of_five_is_exactly_the_floor_and_must_pass():
    """4/5 == 0.8 exactly in binary, so >= must not be fudged with an epsilon."""
    passed, _, _ = check_required_skills_gate(
        ["a", "b", "c", "d"], ["a", "b", "c", "d", "e"], min_coverage=0.8
    )
    assert passed is True


def test_missing_most_of_the_list_still_disqualifies():
    """The gate still has to do its job: a high semantic score must not be able to paper
    over absent skills."""
    passed, _, missing = check_required_skills_gate(
        ["Python"], ["Python", "Spark", "Kafka", "Airflow", "dbt"], min_coverage=0.8
    )
    assert passed is False
    assert len(missing) == 4


def test_missing_skills_are_still_reported_when_the_gate_passes():
    """A recruiter must still see the gap, even though it no longer disqualifies."""
    passed, matched, missing = check_required_skills_gate(
        ["a", "b", "c", "d"], ["a", "b", "c", "d", "e"], min_coverage=0.8
    )
    assert passed is True
    assert missing == ["e"], "the gap has to stay visible or the pass is unexplainable"


def test_no_required_skills_passes():
    passed, matched, missing = check_required_skills_gate(["Python"], [])
    assert passed is True
    assert (matched, missing) == ([], [])


def test_the_floor_travels_on_the_versioned_config():
    """min_required_coverage lives on ScoringWeights, which comes from the versioned
    scoring_configs row -- so a MatchResult's scoring_config_version keeps fully
    describing how it was scored."""
    assert ScoringWeights(0.5, 0.5, 0.6, 0.2, 0.2).min_required_coverage == 1.0
    loose = ScoringWeights(0.5, 0.5, 0.6, 0.2, 0.2, min_required_coverage=0.8)
    assert loose.min_required_coverage == 0.8


def test_score_candidate_honours_the_configured_floor():
    """End to end through the real scoring entry point, not just the gate helper."""
    candidate = CandidateScoringInput(
        candidate_id="c1",
        skills=["Power BI", "SQL Server", "SSAS", "SSIS"],
        years_of_experience=15.0,
        job_titles=["Consultant BI"],
        semantic_score=0.70,
    )
    job = JobRequirements(
        required_skills=["Power BI", "SQL", "SQL Server", "SSAS", "SSIS"],
        preferred_skills=[],
        min_years_experience=5.0,
        titles=["Consultant BI"],
    )
    strict = score_candidate(
        candidate, job, ScoringWeights(0.5, 0.5, 0.6, 0.2, 0.2), threshold=0.6
    )
    graded = score_candidate(
        candidate, job,
        ScoringWeights(0.5, 0.5, 0.6, 0.2, 0.2, min_required_coverage=0.8),
        threshold=0.6,
    )

    assert strict.hard_gate_passed is False
    assert strict.qualified is False
    assert graded.hard_gate_passed is True
    # Same scores either way -- only the gate changed, which is what makes a
    # before/after comparison meaningful.
    assert graded.final_score == strict.final_score
    assert graded.qualified is (graded.final_score >= 0.6)


# ------------------------------------------------------------- the required-skills cap

def test_required_skills_beyond_the_cap_are_demoted_not_dropped():
    many = [f"Skill{i}" for i in range(1, 19)]          # 18, as a real extraction produced
    result = normalize_extraction(many, ["Extra"], None, ["Data Engineer"])

    assert len(result.required_skills) == MAX_REQUIRED_SKILLS
    assert result.required_skills == many[:MAX_REQUIRED_SKILLS]
    assert result.demoted_required == many[MAX_REQUIRED_SKILLS:]
    # Information-preserving: compute_skill_score targets required ∪ preferred, so a
    # demoted skill still contributes to the score. Only what *gates* changed.
    for skill in many[MAX_REQUIRED_SKILLS:]:
        assert skill in result.preferred_skills
    assert "Extra" in result.preferred_skills


def test_a_list_within_the_cap_is_untouched():
    few = ["Python", "SQL", "Airflow"]
    result = normalize_extraction(few, ["Spark"], None, [])
    assert result.required_skills == few
    assert result.demoted_required == []


def test_demotion_is_flagged_so_the_repair_pass_can_do_better():
    """The kept skills are just the first N, which is positional, not a judgment about
    importance -- so it has to be visible rather than silent."""
    many = [f"Skill{i}" for i in range(1, 19)]
    result = validate_and_normalize(many, [], None, ["Data Engineer"])

    assert result.status == "flagged"
    messages = " ".join(w.message for w in result.warnings if w.field == "required_skills")
    assert "moved to preferred" in messages
    assert "Skill18" in messages, "the demoted skills must be named, not just counted"


def test_cap_does_not_create_duplicates_between_the_two_lists():
    many = ["Python", "SQL", "Spark", "Kafka", "Airflow", "dbt", "Python", "Snowflake"]
    result = normalize_extraction(many, ["Snowflake", "SQL"], None, [])
    overlap = {s.lower() for s in result.required_skills} & {
        s.lower() for s in result.preferred_skills
    }
    assert overlap == set(), f"a skill appears in both lists: {overlap}"
