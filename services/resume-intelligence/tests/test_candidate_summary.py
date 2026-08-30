"""
tests/test_candidate_summary.py
---------------------------------
Unit tests for pipeline/candidate_summary.py -- pure functions, no mocks, no
network. The PII-boundary tests here are the most important tests in this
service: they prove build_candidate_summary() cannot leak name/email/phone
into what gets sent to Second Brain.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.candidate_summary import SUMMARY_SOURCE_VERSION, build_candidate_summary
from shared.schemas import CandidateSummary

# shared.schemas.CandidateSummary.candidate_id is typed UUID4 -- must be an
# actual version-4 UUID, not an arbitrary hex string.
CANDIDATE_ID = uuid.uuid4()

_FULL_CANDIDATE_JSON = {
    "basics": {
        "first_name": "Jean",
        "last_name": "Dupont",
        "email": "jean.dupont@example.com",
        "phone": "+33 6 12 34 56 78",
        "job_title": "DevOps Engineer",
        "location": "Paris, France",
        "links": {
            "linkedin": "https://linkedin.com/in/jeandupont",
            "github": "https://github.com/jeandupont",
            "portfolio": None,
        },
    },
    "work": [
        {
            "company": "Capgemini",
            "client": "BNP Paribas",
            "position": "DevOps Engineer",
            "start_date": "2021-03",
            "end_date": "2023-06",
            "description": "CI/CD pipeline automation for a banking client.",
            "tech_stack": ["Docker", "Kubernetes"],
            "location": None,
        },
        {
            "company": "Atos",
            "client": None,
            "position": "Junior Systems Administrator",
            "start_date": "2019-09",
            "end_date": "2021-02",
            "description": "Linux server administration.",
            "tech_stack": ["Bash", "Ansible"],
            "location": None,
        },
    ],
    "education": [
        {"institution": "INSA Lyon", "degree": "Ingenieur", "field": "Informatique",
         "start_date": "2015-09", "end_date": "2018-06"},
    ],
    "skills": ["Docker", "docker", "Kubernetes", "Bash", "Ansible"],
    "certifications": ["AWS Solutions Architect"],
    "spoken_languages": [
        {"language": "French", "level": "native"},
        {"language": "English", "level": "C1"},
    ],
}


class TestBuildCandidateSummary:
    def test_returns_a_valid_candidate_summary(self):
        summary = build_candidate_summary(CANDIDATE_ID, _FULL_CANDIDATE_JSON)
        assert isinstance(summary, CandidateSummary)

    def test_candidate_id_is_preserved_exactly(self):
        summary = build_candidate_summary(CANDIDATE_ID, _FULL_CANDIDATE_JSON)
        assert summary.candidate_id == CANDIDATE_ID

    def test_source_version_is_set(self):
        summary = build_candidate_summary(CANDIDATE_ID, _FULL_CANDIDATE_JSON)
        assert summary.source_version == SUMMARY_SOURCE_VERSION
        assert summary.source_version

    def test_job_titles_extracted_most_recent_first(self):
        summary = build_candidate_summary(CANDIDATE_ID, _FULL_CANDIDATE_JSON)
        assert summary.job_titles == ["DevOps Engineer", "Junior Systems Administrator"]

    def test_years_of_experience_is_numeric_and_deterministic(self):
        summary = build_candidate_summary(CANDIDATE_ID, _FULL_CANDIDATE_JSON)
        # 2021-03..2023-06 (27 months) + 2019-09..2021-02 (17 months) = 44 months
        assert summary.years_of_experience == round(44 / 12, 1)
        assert isinstance(summary.years_of_experience, float)


class TestSkillFlatteningAndDedup:
    def test_skills_and_certifications_are_merged_and_deduped(self):
        summary = build_candidate_summary(CANDIDATE_ID, _FULL_CANDIDATE_JSON)
        # "Docker"/"docker" case-insensitive duplicate collapsed, first-seen casing kept
        assert summary.skills.count("Docker") == 1
        assert "docker" not in summary.skills
        # certifications folded into skills (CandidateSummary has no separate field)
        assert "AWS Solutions Architect" in summary.skills

    def test_no_taxonomy_or_synonym_collapsing(self):
        candidate_json = {**_FULL_CANDIDATE_JSON, "skills": ["JS", "Javascript"], "certifications": []}
        summary = build_candidate_summary(CANDIDATE_ID, candidate_json)
        assert "JS" in summary.skills
        assert "Javascript" in summary.skills


class TestPiiBoundary:
    def test_summary_never_contains_name_email_or_phone(self):
        summary = build_candidate_summary(CANDIDATE_ID, _FULL_CANDIDATE_JSON)
        dumped = summary.model_dump_json()
        for pii in ("Jean", "Dupont", "jean.dupont@example.com", "+33 6 12 34 56 78"):
            assert pii not in dumped

    def test_summary_never_contains_links_or_location(self):
        summary = build_candidate_summary(CANDIDATE_ID, _FULL_CANDIDATE_JSON)
        dumped = summary.model_dump_json()
        for pii in ("linkedin.com/in/jeandupont", "github.com/jeandupont", "Paris, France"):
            assert pii not in dumped

    def test_candidate_summary_schema_rejects_pii_fields_even_if_someone_tried(self):
        # Defense in depth: even a caller who ignored this builder entirely and tried
        # to pass PII directly gets rejected by the shared schema's extra="forbid".
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CandidateSummary(
                candidate_id=CANDIDATE_ID,
                raw_text="some text",
                email="leaked@example.com",  # type: ignore[call-arg]
            )

    def test_raw_text_contains_professional_content_but_no_identity_data(self):
        summary = build_candidate_summary(CANDIDATE_ID, _FULL_CANDIDATE_JSON)
        text = summary.raw_text
        # professional content present
        assert "DevOps Engineer" in text
        assert "Capgemini" in text
        assert "Docker" in text
        assert "INSA Lyon" in text
        # no identity/contact data
        for pii in (
            "Jean", "Dupont", "jean.dupont@example.com", "+33 6 12 34 56 78",
            "linkedin.com", "github.com", "Paris, France",
        ):
            assert pii not in text

    def test_builder_never_takes_the_original_extracted_resume_text(self):
        # build_candidate_summary's signature is (candidate_id, candidate_json) --
        # there is no parameter through which the original extracted resume text
        # could reach it, let alone Second Brain. raw_text is synthesized fresh
        # from candidate_json's allow-listed fields every time (verified by the
        # other PII-boundary tests in this class).
        import inspect

        params = list(inspect.signature(build_candidate_summary).parameters)
        assert params == ["candidate_id", "candidate_json"]


class TestMissingOptionalFields:
    def test_empty_candidate_json_produces_a_valid_summary(self):
        summary = build_candidate_summary(CANDIDATE_ID, {"basics": {"first_name": "X"}})
        assert summary.candidate_id == CANDIDATE_ID
        assert summary.skills == []
        assert summary.job_titles == []
        assert summary.years_of_experience == 0.0
        assert summary.raw_text  # min_length=1 -- never empty
        assert "X" not in summary.raw_text

    def test_missing_work_and_education_still_builds(self):
        candidate_json = {
            "basics": {}, "work": [], "education": [],
            "skills": ["Python"], "certifications": [], "spoken_languages": [],
        }
        summary = build_candidate_summary(CANDIDATE_ID, candidate_json)
        assert summary.job_titles == []
        assert "Python" in summary.skills
        assert "Python" in summary.raw_text

    def test_job_with_no_description_or_tech_stack_still_builds(self):
        candidate_json = {
            "basics": {},
            "work": [{"company": "Acme", "position": "Engineer", "start_date": None, "end_date": None}],
            "education": [], "skills": [], "certifications": [], "spoken_languages": [],
        }
        summary = build_candidate_summary(CANDIDATE_ID, candidate_json)
        assert "Engineer at Acme" in summary.raw_text
        assert summary.years_of_experience == 0.0
