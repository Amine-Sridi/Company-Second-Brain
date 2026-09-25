"""
benchmarks/local_llm/tasks.py
------------------------------
The four Gemini-dependent *text* tasks in this project, expressed as benchmarkable
cases with graders.

Design rule: grade with the project's OWN logic wherever it exists, not with a
generic "does this look like good JSON" heuristic.

  * JD structuring is graded by `jd_validation.validate_and_normalize` -- the exact
    function `submit_job` runs on Gemini's output to decide whether an extraction is
    `clean` or `flagged`. A local model "wins" here by producing an extraction this
    project would itself accept, not by matching some external benchmark.
  * Interview evaluation is graded by VoiceHR's real `normalize_result` pipeline and
    the `expect_cefr_in` ground truth already committed in
    services/voicehr/tests/golden_cases.py.
  * Resume parsing is graded on field-level recall against a resume whose true
    contents we control.
  * Match explanation has no schema, so it is graded on the properties the project
    actually cares about: grounded in the supplied facts, and leaking no PII.

Prompts are imported from (or reproduced verbatim from) the real services so the
benchmark measures the same thing production asks for.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _p in (
    _REPO,
    _REPO / "services" / "recruitment-workflow",
    _REPO / "services" / "resume-intelligence",
    _REPO / "services" / "voicehr",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ---------------------------------------------------------------------------
# Task 1 -- JD structuring (RWS: main.py structure_job_description)
# ---------------------------------------------------------------------------

def _jd_prompt(raw_description: str) -> str:
    """Verbatim copy of main.py's structure_job_description prompt."""
    return (
        "Extract structured hiring requirements from this job description. "
        "required_skills: must-have technical/soft skills, short strings. "
        "preferred_skills: nice-to-have skills, short strings. "
        "min_years_experience: minimum years of relevant experience required, "
        "or null if not specified. "
        "titles: job title variants this role matches (e.g. ['Backend Engineer', "
        "'Software Engineer']).\n\n"
        f"Job description:\n{raw_description}"
    )


# Each case pairs a realistic JD with the atomic skill names a correct extraction
# must contain. The "hard" cases are the ones this project has actually been burned
# by: prose that invites a model to emit responsibility phrases ("building and
# deploying REST APIs") which can never match a candidate under the exact-match hard
# gate (see jd_validation.py's own module docstring).
JD_CASES = [
    {
        "id": "jd_explicit_list",
        "raw": (
            "We are hiring a Backend Engineer. Required skills: Python, FastAPI, "
            "PostgreSQL, Docker. Preferred skills: Git, Linux. Minimum 3 years of "
            "experience required."
        ),
        "expect_required": {"python", "fastapi", "postgresql", "docker"},
        "expect_min_years": 3,
    },
    {
        "id": "jd_prose_no_bullet_list",
        "raw": (
            "Join our platform team as a Senior Backend Engineer. You will spend your "
            "days building and deploying REST APIs in Python, looking after our "
            "PostgreSQL databases, and shipping services on Kubernetes. We expect at "
            "least five years in a similar role. Familiarity with Redis is a plus."
        ),
        "expect_required": {"python", "postgresql", "kubernetes"},
        "expect_min_years": 5,
    },
    {
        "id": "jd_mixed_seniority_titles",
        "raw": (
            "Data Engineer / Analytics Engineer wanted. The successful candidate will "
            "write SQL every day, orchestrate pipelines with Airflow, and model data "
            "in dbt. Spark exposure is welcome but not required. 4+ years experience."
        ),
        "expect_required": {"sql", "airflow", "dbt"},
        "expect_min_years": 4,
    },
    {
        "id": "jd_sparse_vague",
        "raw": (
            "We're looking for a talented engineer to join a fast-moving team and make "
            "an impact on our products. You should be a self-starter who thrives in "
            "ambiguity and loves shipping."
        ),
        # Deliberately nothing concrete: a good extraction is an *empty* one. A model
        # that invents requirements here is hallucinating, which jd_validation would
        # not catch (it only flags phrase-like or wholly-empty extractions).
        "expect_required": set(),
        "expect_min_years": None,
    },
]


def grade_jd(output: dict, case: dict) -> dict:
    """Graded with the project's own validator plus recall/hallucination against the
    case's known-correct skill set."""
    from jd_validation import validate_and_normalize

    required = [str(s) for s in (output.get("required_skills") or [])]
    preferred = [str(s) for s in (output.get("preferred_skills") or [])]
    titles = [str(t) for t in (output.get("titles") or [])]
    min_years = output.get("min_years_experience")

    validation = validate_and_normalize(
        required_skills=required,
        preferred_skills=preferred,
        min_years_experience=min_years if isinstance(min_years, (int, float)) else None,
        titles=titles,
    )

    got = {s.strip().lower() for s in validation.normalized.required_skills}
    want = case["expect_required"]

    if want:
        hits = len(got & want)
        recall = hits / len(want)
        precision = hits / len(got) if got else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    else:
        # Nothing should have been extracted; inventing requirements is the failure.
        recall = 1.0
        precision = 1.0 if not got else 0.0
        f1 = precision

    years_ok = (
        case["expect_min_years"] is None and min_years in (None, 0)
    ) or (
        case["expect_min_years"] is not None
        and isinstance(min_years, (int, float))
        and abs(float(min_years) - float(case["expect_min_years"])) < 0.01
    )

    # The project's own bar: would submit_job have accepted this extraction as clean?
    accepted_clean = validation.status == "clean"

    quality = 0.70 * f1 + 0.15 * (1.0 if years_ok else 0.0) + 0.15 * (1.0 if accepted_clean else 0.0)
    return {
        "quality": round(quality, 4),
        "f1": round(f1, 4),
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "years_ok": years_ok,
        "project_accepts_as_clean": accepted_clean,
        "extraction_status": validation.status,
        "extracted_required": sorted(got),
    }


# ---------------------------------------------------------------------------
# Task 2 -- Resume parsing (Resume Intelligence: pipeline/parsing_agent.py)
# ---------------------------------------------------------------------------

_RESUME_TEXT = """\
Amine Verified Demo Candidate
Email: candidate.benchmark@example.com
Phone: +216 20 000 000

Summary: Backend engineer with 5 years of experience building REST APIs,
deploying services, and working with Python, FastAPI, PostgreSQL and Docker.

Experience:
Backend Engineer at Example Corp, 2019-01 to 2024-06.
Built and maintained REST APIs, deployed services with Docker, worked with
PostgreSQL databases. Tech stack: Python, FastAPI, PostgreSQL, Docker.

Junior Developer at Startup SARL, 2017-09 to 2018-12.
Maintained internal tooling in Python and Django.

Education:
Engineering Degree in Software Engineering, ENIT, 2017.

Skills: Python, FastAPI, PostgreSQL, Docker, REST APIs, Git, Linux

Languages: French (native), English (professional)
"""


def resume_prompt() -> str:
    """Uses Resume Intelligence's own committed prompt files, so the benchmark asks
    exactly what production asks."""
    prompts = _REPO / "services" / "resume-intelligence" / "prompts"
    system = (prompts / "parsing_system.txt").read_text(encoding="utf-8")
    user = (prompts / "parsing_user.txt").read_text(encoding="utf-8")
    return f"{system}\n\n{user.replace('{RESUME_TEXT}', _RESUME_TEXT)}"


RESUME_CASES = [{"id": "resume_backend_engineer", "prompt_fn": resume_prompt}]


def grade_resume(output: dict, case: dict) -> dict:
    basics = output.get("basics") or {}
    skills = {str(s).strip().lower() for s in (output.get("skills") or [])}
    work = output.get("work") or []

    email_ok = (basics.get("email") or "").strip().lower() == "candidate.benchmark@example.com"
    name_ok = "amine" in f"{basics.get('first_name') or ''}".strip().lower()
    want_skills = {"python", "fastapi", "postgresql", "docker"}
    skill_recall = len(skills & want_skills) / len(want_skills)
    work_ok = len(work) >= 2
    company_ok = any("example corp" in str(j.get("company", "")).lower() for j in work)

    quality = (
        0.25 * (1.0 if email_ok else 0.0)
        + 0.15 * (1.0 if name_ok else 0.0)
        + 0.35 * skill_recall
        + 0.15 * (1.0 if work_ok else 0.0)
        + 0.10 * (1.0 if company_ok else 0.0)
    )
    return {
        "quality": round(quality, 4),
        "email_ok": email_ok,
        "name_ok": name_ok,
        "skill_recall": round(skill_recall, 4),
        "work_entries": len(work),
        "company_ok": company_ok,
    }


# ---------------------------------------------------------------------------
# Task 3 -- Match explanation (RWS: explanation.py)
# ---------------------------------------------------------------------------

_EXPLANATION_FACTS = {
    "skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "Git"],
    "years_of_experience": 5.0,
    "job_titles": ["Backend Engineer"],
    "matched": ["Python", "FastAPI", "PostgreSQL"],
    "missing": ["Kubernetes"],
}


def explanation_prompt() -> str:
    """Mirrors explanation.build_prompt's shape and its explicit no-PII instruction."""
    f = _EXPLANATION_FACTS
    return (
        "You are assisting a recruiter by explaining an automated candidate match "
        "result. Write a concise explanation (3-5 sentences) covering: which "
        "requirements were matched, which were missing, a brief breakdown of the "
        "scores below, and why the candidate did or did not qualify. Base your answer "
        "only on the facts given below -- do not invent anything beyond them. Do not "
        "include or guess at any name, contact detail, or other personal identifying "
        "information; none is provided to you and none should appear in your answer.\n\n"
        f"Candidate skills: {f['skills']}\n"
        f"Candidate years of experience: {f['years_of_experience']}\n"
        f"Candidate job titles: {f['job_titles']}\n"
        f"Matched requirements: {f['matched']}\n"
        f"Missing requirements: {f['missing']}\n"
        "Semantic score: 0.780\nSkill score: 0.750\nExperience score: 1.000\n"
        "Title score: 1.000\nDeterministic score: 0.850\nFinal score: 0.815\n"
        "Qualification threshold: 0.600\nAll required skills present: False\n"
        "Qualified: False\n"
    )


EXPLANATION_CASES = [{"id": "explanation_disqualified_candidate", "prompt_fn": explanation_prompt}]

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d ().-]{7,}\d")


def grade_explanation(text: str, case: dict) -> dict:
    """No schema here, so grade what the project actually requires of this text:
    grounded in the supplied facts, and free of invented PII."""
    lowered = (text or "").lower()
    mentions_missing = "kubernetes" in lowered
    mentions_matched = sum(s.lower() in lowered for s in _EXPLANATION_FACTS["matched"])
    says_not_qualified = any(p in lowered for p in ("not qualif", "did not qualif", "does not qualif", "disqualif"))
    invented_pii = bool(_EMAIL_RE.search(text or "")) or bool(_PHONE_RE.search(text or ""))
    words = len((text or "").split())
    length_ok = 25 <= words <= 260

    quality = (
        0.30 * (1.0 if mentions_missing else 0.0)
        + 0.25 * (mentions_matched / len(_EXPLANATION_FACTS["matched"]))
        + 0.25 * (1.0 if says_not_qualified else 0.0)
        + 0.20 * (1.0 if length_ok else 0.0)
    )
    if invented_pii:
        quality = 0.0  # a hard fail for this project, not a deduction
    return {
        "quality": round(quality, 4),
        "mentions_missing_requirement": mentions_missing,
        "matched_requirements_mentioned": mentions_matched,
        "states_not_qualified": says_not_qualified,
        "invented_pii": invented_pii,
        "word_count": words,
    }


# ---------------------------------------------------------------------------
# Task 4 -- Interview evaluation (VoiceHR: services/evaluator.py)
# ---------------------------------------------------------------------------

def evaluation_prompt(transcript: list[dict], language: str = "en", inconclusive: bool = False) -> str:
    """Uses VoiceHR's real EVALUATION_PROMPT and real transcript formatter."""
    from backend.services.evaluator import EVALUATION_PROMPT, format_transcript

    note = "Note: This interview was incomplete. Set sufficient_evidence: false." if inconclusive else ""
    return EVALUATION_PROMPT.format(
        inconclusive_note=note,
        transcript=f"Language: {language}\n{format_transcript(transcript)}",
    )


def evaluation_cases() -> list[dict]:
    """The committed golden cases, reused for their transcripts and their
    already-agreed expected CEFR bands. Their `mocked_response` is ignored here --
    we want the *model's* real answer graded against the same expectation."""
    from tests.golden_cases import GOLDEN_CASES

    # The three insufficient-evidence cases carry no `expect_cefr_in` on purpose:
    # the correct outcome there is *no* CEFR verdict at all, which grade_evaluation
    # checks via expect_sufficient instead.
    return [
        {
            "id": c["id"],
            "transcript": c["transcript"],
            "duration_seconds": c["duration_seconds"],
            "pre_check_inconclusive": c["pre_check_inconclusive"],
            "expect_cefr_in": c.get("expect_cefr_in", set()),
            "expect_sufficient": c["expect_sufficient"],
        }
        for c in GOLDEN_CASES
    ]


def grade_evaluation(output: dict, case: dict) -> dict:
    """Runs the model's raw scores through VoiceHR's real normalization/CEFR pipeline
    and checks the outcome against the golden case's expectation."""
    from backend.services.evaluator import normalize_result

    insufficient = case["pre_check_inconclusive"] or output.get("sufficient_evidence") is False
    result = normalize_result(
        output,
        case["transcript"],
        duration_seconds=case["duration_seconds"],
        inconclusive=case["pre_check_inconclusive"],
        insufficient=insufficient,
    )

    sufficiency_ok = (not insufficient) == bool(case["expect_sufficient"])
    if case["expect_sufficient"]:
        cefr_ok = result["cefr_level"] in case["expect_cefr_in"]
    else:
        # For an insufficient case the correct behaviour is withholding a verdict.
        cefr_ok = result["cefr_level"] is None
    all_dims = all(result["dimensions"].get(d) is not None for d in result["dimensions"])

    quality = 0.5 * (1.0 if cefr_ok else 0.0) + 0.35 * (1.0 if sufficiency_ok else 0.0) + 0.15 * (1.0 if all_dims else 0.0)
    return {
        "quality": round(quality, 4),
        "cefr_ok": cefr_ok,
        "sufficiency_ok": sufficiency_ok,
        "all_dimensions_present": all_dims,
        "cefr_level": result["cefr_level"],
        "expected_cefr": sorted(case["expect_cefr_in"]),
        "overall_score": result["overall_score"],
    }


# ---------------------------------------------------------------------------
# JSON schemas handed to Ollama's constrained decoding (`format`), mirroring what
# GeminiProvider passes as response_json_schema.
# ---------------------------------------------------------------------------

# RWS and Resume Intelligence both ship a top-level module named `schemas` (a .py in
# one, a package in the other), so a plain `import schemas` resolves to whichever is
# earlier on sys.path. Load the RWS one explicitly by file path instead of reordering
# sys.path, which would just move the collision around.
def _rws_schemas():
    import importlib.util

    if "rws_schemas" in sys.modules:
        return sys.modules["rws_schemas"]
    path = _REPO / "services" / "recruitment-workflow" / "schemas.py"
    spec = importlib.util.spec_from_file_location("rws_schemas", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["rws_schemas"] = module
    spec.loader.exec_module(module)
    return module


def jd_schema() -> dict:
    return _rws_schemas().StructuredJobDescription.model_json_schema()


def jd_model():
    return _rws_schemas().StructuredJobDescription


def resume_schema() -> dict:
    from schemas.candidate_extraction import CandidateExtraction

    return CandidateExtraction.model_json_schema()


def evaluation_schema() -> dict:
    from backend.services.evaluator import EVALUATION_SCHEMA

    return EVALUATION_SCHEMA
