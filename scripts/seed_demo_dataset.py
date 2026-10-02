"""
scripts/seed_demo_dataset.py
-----------------------------
Seeds demo candidates and jobs from the real ESN lead-list spreadsheets, through the real
pipeline.

WHY THIS EXISTS
The demo database was previously a pile of test artifacts -- candidates called
"Smoketest Cleanup Candidate", jobs called "Smoketest JD Repair Pass 2" -- plus 68
"Load Test Candidate" rows the throughput benchmarks created. A jury sees the Candidates
and Jobs lists before anything else, and both are ordered newest-first.

The lead lists in storage/*.xlsx are real consultant histories: one row per
candidate-per-client-mission, with genuine French ESN names and genuine technical stacks
("Fabric (ADF, ADLGEN2, Power BI Service), Postgresql, Power BI Premium"). Grouping them
by candidate reconstructs real multi-mission careers, which is far better matching
material than anything synthetic -- and far better than a public dataset, because the
skills, employers and titles actually co-occur the way they do in practice.

PRIVACY -- THE ONE NON-NEGOTIABLE
The spreadsheets identify real people (Candidats / Email / Tel / Linkedin). Those four
fields are **never** carried through. Each candidate gets a pseudonym derived
deterministically from a hash of their real email, so:

  * the same source row always produces the same demo identity, which means re-seeding
    does not churn the demo data or create duplicates;
  * no mapping table is written anywhere. A real-email -> pseudonym file would be a
    re-identification key, which is precisely the artifact this is avoiding;
  * everything that makes the data valuable -- skills, employers, titles, mission counts,
    years of experience, the ESN flag -- is preserved exactly.

Employer names are kept as-is: an ESN is a company, not a person, and employer history is
ordinary CV content.

THE ESN FLAG
`ESN (Y/N)` becomes part of the CV prose ("Consultant MSBI chez <Client> (ESN)"). That is
deliberate: it reaches semantic matching through the extracted text and its embedding,
with no new entity in Second Brain and no change to the deterministic scorer. Second Brain
has no company entity (only CandidateKnowledge and CandidateFeedback), and adding one
would be new bounded-context work pointing away from this product's workflow.

HOW IT RUNS
Through the real HTTP endpoints, not INSERTs: `POST /v1/resumes/jobs` for each CV (so
extraction, embedding and the Second Brain write all genuinely happen) and `POST /v1/jobs`
for each job description. Both services publish no host port (milestone 45), so the calls
are driven from inside the containers.

    python scripts/seed_demo_dataset.py                      # dry run, writes nothing
    python scripts/seed_demo_dataset.py --apply
    python scripts/seed_demo_dataset.py --apply --limit 20    # fewer candidates
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHEETS = str(ROOT / "storage" / "*.xlsx")
CV_DIR = ROOT / "storage" / "generated_cvs"      # under storage/, so gitignored
JOB_DATASET = ROOT / "storage" / "job_dataset.csv"

RI = "csb-resume-intelligence"
RWS = "csb-recruitment-workflow"

# The four columns that identify a real person. Read to group and to derive a stable
# pseudonym, never written out.
IDENTITY_COLUMNS = ("Candidats", "Email", "Tel", "Linkedin")

# Deliberately small, ordinary French name pools: 48 x 44 = 2112 combinations, which is
# ample for ~61 candidates, and a collision is resolved by appending a digit rather than
# by reaching for a dependency like Faker.
FIRST_NAMES = [
    "Camille", "Lucas", "Manon", "Hugo", "Léa", "Nathan", "Chloé", "Louis", "Inès",
    "Gabriel", "Sarah", "Adam", "Jade", "Raphaël", "Louise", "Arthur", "Alice", "Paul",
    "Emma", "Antoine", "Zoé", "Mehdi", "Nour", "Yanis", "Lina", "Samir", "Amina",
    "Karim", "Salma", "Youssef", "Rania", "Omar", "Maya", "Bilal", "Sofia", "Walid",
    "Claire", "Julien", "Marine", "Thomas", "Élise", "Maxime", "Aurélie", "Victor",
    "Nadia", "Pierre", "Sonia", "Damien",
]
LAST_NAMES = [
    "Martin", "Bernard", "Dubois", "Thomas", "Robert", "Richard", "Petit", "Durand",
    "Leroy", "Moreau", "Simon", "Laurent", "Lefebvre", "Michel", "Garcia", "David",
    "Bertrand", "Roux", "Vincent", "Fournier", "Morel", "Girard", "Andre", "Mercier",
    "Blanc", "Guerin", "Boyer", "Rousseau", "Benali", "Haddad", "Chaabane", "Trabelsi",
    "Ben Salah", "Khelifi", "Bouazizi", "Mansouri", "Gharbi", "Jendoubi", "Ayari",
    "Sassi", "Zouari", "Nasri", "Hamdi", "Ferjani",
]


# `Profil` is the spreadsheet's sheet name, so it arrives as "BI", "BIG DATA",
# "CHEF DE PROJET". Passing those through .title() produced "Bi" and "Consultant Chef De
# Projet" on every CV -- and since Gemini extracts this line as a job title, the mangled
# form would have been what the recruiter UI displayed.
DOMAIN_LABELS = {
    "BI": "BI",
    "BIG DATA": "Big Data",
    "CHEF DE PROJET": "Chef de Projet",
}


def domain_label(domain: str) -> str:
    return DOMAIN_LABELS.get(domain.upper(), domain.title())


def role_title(domain: str) -> str:
    """The candidate's headline role. A project manager is not a "Consultant Chef de
    Projet"; the other two domains are consulting roles."""
    label = domain_label(domain)
    return label if domain.upper() == "CHEF DE PROJET" else f"Consultant {label}"


def norm(value) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in ("none", "nan", "n/a", "-") else text


@dataclass
class Mission:
    client: str
    is_esn: str          # "Y" | "N" | "Unknown"
    metier: str
    stack: str
    # `Experience` is a PER-MISSION duration, not the candidate's total career. Verified
    # against the data: one candidate's rows carry 0.2, 0.5, 0.6, 0.7, 0.9, 1.8, 2.1 and
    # 2.2 "ans" across their missions. Reading it as a career total produced an obvious
    # absurdity -- 14 missions in 2.2 years -- which is what caught the mistake.
    years: float | None = None


@dataclass
class Candidate:
    key: str                     # the real identifier, used only in memory for grouping
    domain: str                  # Profil: BI | BIG DATA | CHEF DE PROJET
    experience_years: float | None
    missions: list[Mission] = field(default_factory=list)
    # Assigned by pseudonymise(), the only identity that ever leaves this process.
    alias_first: str = ""
    alias_last: str = ""
    alias_email: str = ""
    alias_phone: str = ""

    @property
    def alias_name(self) -> str:
        return f"{self.alias_first} {self.alias_last}"


def parse_experience(value) -> float | None:
    """`Experience` arrives as '>3.3ans', '>1.0ans', occasionally blank."""
    text = norm(value)
    if not text:
        return None
    match = re.search(r"(\d+(?:[.,]\d+)?)", text.replace(",", "."))
    return float(match.group(1)) if match else None


def load_candidates() -> list[Candidate]:
    """Groups every sheet of every workbook into one record per real person."""
    import openpyxl

    grouped: dict[str, list[dict]] = defaultdict(list)
    files = sorted(glob.glob(SHEETS))
    if not files:
        raise SystemExit(f"no spreadsheets found at {SHEETS}")

    for path in files:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sheet_name in workbook.sheetnames:
            rows = list(workbook[sheet_name].iter_rows(values_only=True))
            if not rows:
                continue
            header = [norm(h) for h in rows[0]]
            for raw in rows[1:]:
                if not any(norm(c) for c in raw):
                    continue
                row = dict(zip(header, raw))
                # Email is the identity where present; a name-only row still groups, but
                # 8 rows carry neither and cannot be attributed to anyone, so they go.
                email = norm(row.get("Email")).lower()
                name = norm(row.get("Candidats")).lower()
                key = email or (f"name:{name}" if name else "")
                if not key:
                    continue
                grouped[key].append(row)

    candidates: list[Candidate] = []
    for key, rows in grouped.items():
        domains = {norm(r.get("Profil")).upper() for r in rows if norm(r.get("Profil"))}
        candidate = Candidate(key=key, domain=sorted(domains)[0] if domains else "AUTRE",
                              experience_years=None)
        seen: set[tuple] = set()
        for row in rows:
            client = norm(row.get("Clients"))
            metier = norm(row.get("Metier"))
            stack = norm(row.get("Environnement technique"))
            if not (client or metier or stack):
                continue
            fingerprint = (client.lower(), metier.lower())
            if fingerprint in seen:
                continue          # same mission repeated across workbooks
            seen.add(fingerprint)
            candidate.missions.append(Mission(
                client=client or "Client confidentiel",
                is_esn=norm(row.get("ESN (Y/N)")) or "Unknown",
                metier=metier or role_title(candidate.domain),
                stack=stack,
                years=parse_experience(row.get("Experience")),
            ))
        # Summed over DEDUPLICATED missions. Summing over raw rows would inflate anyone
        # whose missions repeat across workbooks -- one candidate has 26 rows for 14
        # distinct clients, so that alone would nearly double their career.
        durations = [m.years for m in candidate.missions if m.years is not None]
        candidate.experience_years = round(sum(durations), 1) if durations else None
        if candidate.missions:
            candidates.append(candidate)

    # Stable order: most missions first, then by a hash of the key so it never depends on
    # filesystem or dict ordering.
    candidates.sort(key=lambda c: (-len(c.missions), hashlib.sha256(c.key.encode()).hexdigest()))
    return candidates


def pseudonymise(candidates: list[Candidate], verified_email: str | None,
                 verified_name: str | None = None) -> None:
    """Assigns each candidate a stable fake identity derived from their real one.

    Derived rather than stored: the mapping is reproducible from the source spreadsheet
    plus this code, so no re-identification table exists on disk.
    """
    used_names: set[tuple[str, str]] = set()
    used_emails: set[str] = set()

    for index, candidate in enumerate(candidates):
        digest = hashlib.sha256(("csb-demo:" + candidate.key).encode("utf-8")).digest()
        first = FIRST_NAMES[digest[0] % len(FIRST_NAMES)]
        last = LAST_NAMES[digest[1] % len(LAST_NAMES)]
        suffix = 0
        while (first, last) in used_names:
            suffix += 1
            last = LAST_NAMES[(digest[1] + suffix) % len(LAST_NAMES)]
            if suffix > len(LAST_NAMES):
                last = f"{last}-{digest[2] % 90 + 10}"
                break
        used_names.add((first, last))

        slug_last = re.sub(r"[^a-z]+", "", last.lower()) or "candidat"
        email = f"{first.lower()}.{slug_last}@example.com"
        n = 1
        while email in used_emails:
            n += 1
            email = f"{first.lower()}.{slug_last}{n}@example.com"
        used_emails.add(email)

        candidate.alias_first = first
        candidate.alias_last = last
        candidate.alias_email = email
        # Deliberately in the French reserved-for-fiction 06 99 range shape; never a real
        # subscriber number.
        candidate.alias_phone = f"+33 6 99 {digest[3] % 90 + 10} {digest[4] % 90 + 10}"

    # One candidate must be reachable for the demo's live "Invite" click: the Resend
    # account has no verified custom domain, so every @example.com address gets a clean
    # 422 from Resend itself. The strongest profile (most missions) gets it.
    #
    # Its NAME is overridden to match, not left as a pseudonym. This is the one identity
    # in the set that is deliberately real -- the project owner's own address, used so the
    # invite is deliverable -- and pairing a generated name with it
    # ("Aurélie Blanc <aminesridi58@gmail.com>") reads as a data-integrity bug on screen,
    # which is the opposite of what a demo should show.
    if verified_email and candidates:
        candidates[0].alias_email = verified_email
        if verified_name:
            parts = verified_name.split()
            candidates[0].alias_first = parts[0]
            candidates[0].alias_last = " ".join(parts[1:]) or ""


def build_cv_text(candidate: Candidate) -> list[str]:
    """The CV as lines of French prose. This text is what Gemini extracts from, so it
    carries the ESN flag in words rather than as a field."""
    role = role_title(candidate.domain)
    lines = [
        candidate.alias_name,
        role,
        f"Email : {candidate.alias_email}",
        f"Téléphone : {candidate.alias_phone}",
        "",
    ]
    if candidate.experience_years is not None:
        lines.append(
            f"Profil : {role} avec {candidate.experience_years:.1f} ans d'expérience, "
            f"réparties sur {len(candidate.missions)} missions en clientèle."
        )
    else:
        lines.append(
            f"Profil : {role}, {len(candidate.missions)} missions en clientèle."
        )
    lines += ["", "Expériences professionnelles :"]
    for mission in candidate.missions:
        kind = {"Y": " (ESN)", "N": " (client final)"}.get(mission.is_esn, "")
        duration = f" - {mission.years:.1f} ans" if mission.years is not None else ""
        lines.append(f"- {mission.metier} chez {mission.client}{kind}{duration}.")
        if mission.stack:
            lines.append(f"  Environnement technique : {mission.stack}")

    # A flat, de-duplicated skill list. The per-mission stacks above already carry this,
    # but a consolidated list is what a real CV has and it gives the deterministic
    # skill matcher clean tokens to work with.
    skills: list[str] = []
    for mission in candidate.missions:
        for token in re.split(r"[,\n;]+", mission.stack):
            token = token.strip()
            if token and token.lower() not in {s.lower() for s in skills}:
                skills.append(token)
    if skills:
        lines += ["", "Compétences : " + ", ".join(skills[:40])]
    return lines


def write_cv_docx(candidate: Candidate, out_dir: Path) -> Path:
    from docx import Document

    document = Document()
    for line in build_cv_text(candidate):
        document.add_paragraph(line)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", candidate.alias_name).strip("_")
    path = out_dir / f"{slug}.docx"
    document.save(str(path))
    return path


def env_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def ingest_cvs(paths: list[Path], api_key: str) -> dict:
    """Uploads each CV through the real queued endpoint, from inside the RI container."""
    for path in paths:
        subprocess.run(["docker", "cp", str(path), f"{RI}:/tmp/{path.name}"],
                       check=True, capture_output=True, timeout=180)
    script = f'''
import asyncio, json, httpx
names = {json.dumps([p.name for p in paths])}
KEY = {api_key!r}
sem = asyncio.Semaphore(3)
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

async def one(name):
    async with sem:
        with open("/tmp/" + name, "rb") as handle:
            data = handle.read()
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post("http://localhost:8000/v1/resumes/jobs",
                                  files={{"file": (name, data, DOCX)}},
                                  headers={{"X-Api-Key": KEY}})
        return {{"name": name, "status": r.status_code,
                 "body": r.json() if r.status_code == 202 else r.text[:120]}}

async def main():
    print("RESULT:" + json.dumps(await asyncio.gather(*[one(n) for n in names])))

asyncio.run(main())
'''
    out = subprocess.run(["docker", "exec", "-i", RI, "python", "-c", script],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=3600)
    if out.returncode != 0:
        raise SystemExit(f"ingest failed:\n{(out.stderr or '')[-800:]}")
    for line in out.stdout.splitlines():
        if line.startswith("RESULT:"):
            results = json.loads(line[len("RESULT:"):])
            accepted = [r for r in results if r["status"] == 202]
            return {"accepted": len(accepted), "rejected": len(results) - len(accepted),
                    "errors": [r for r in results if r["status"] != 202][:3]}
    raise SystemExit(f"ingest returned nothing usable:\n{out.stdout[-500:]}")


def skill_vocabulary(candidates: list[Candidate]) -> set[str]:
    """Every technology token the candidate pool actually mentions, lowercased."""
    vocabulary: set[str] = set()
    for candidate in candidates:
        for mission in candidate.missions:
            for token in re.split(r"[,\n;()/]+", mission.stack):
                token = token.strip().lower()
                if len(token) > 2:
                    vocabulary.add(token)
    return vocabulary


def pick_jobs(limit: int, vocabulary: set[str]) -> list[dict]:
    """Chooses job descriptions by how much their required skills overlap the pool's
    actual vocabulary, rather than by matching words in the title.

    This matters more than it looks. Every candidate in these spreadsheets is a BI, Big
    Data or project-management profile, so a JD outside that space retrieves nobody and
    the demo's ranked-matches screen is empty. A title regex is not good enough at
    deciding this: a first attempt accepted `\\banalyst\\b` and duly selected
    "Cybersecurity Analyst" and "Junior Security Analyst" -- titles that look adjacent
    and share no skills at all with a Power BI consultant. Scoring on the skills the
    candidates demonstrably have cannot make that mistake.
    """
    import csv
    import math

    if not JOB_DATASET.exists() or not vocabulary:
        return []

    rows: list[tuple[str, dict, set[str]]] = []
    seen_titles: set[str] = set()
    document_frequency: dict[str, int] = defaultdict(int)
    with open(JOB_DATASET, encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            title = norm(row.get("Title"))
            if not title or title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())
            tokens = {
                t.strip().lower()
                for field_name in ("Skills", "Keywords")
                for t in re.split(r"[,\n;()/]+", norm(row.get(field_name)))
                if len(t.strip()) > 2
            }
            rows.append((title, row, tokens))
            for token in tokens:
                document_frequency[token] += 1

    # Weight each shared skill by how rare it is across the whole job catalogue, because a
    # raw count is dominated by tokens nearly every IT job lists. Unweighted, "Backend
    # Developer" scored 16 on `agile, django, docker, git` -- it shares generic tooling
    # with a Power BI consultant and nothing else, so it ranked alongside genuine data
    # roles. Rare tokens ("power bi", "ssis", "talend", "informatica") are the ones that
    # actually indicate a domain fit, and this makes them carry the decision.
    total = max(1, len(rows))
    scored: list[tuple[float, str, dict]] = []
    for title, row, tokens in rows:
        shared = tokens & vocabulary
        if len(shared) < 3:       # below this the overlap is incidental
            continue
        weight = sum(math.log(total / (1 + document_frequency[t])) for t in shared)
        scored.append((weight, title, row))
    # Heaviest first; title breaks ties so the selection is reproducible run to run.
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [row for _, _, row in scored[:limit]]


def pool_skills(minimum: int = 8) -> list[tuple[str, int]]:
    """The skill vocabulary as it exists in Second Brain, most frequent first.

    Queried from the database rather than recomputed from the spreadsheets, because what
    matching compares against is Gemini's *normalised* extraction ("SQL Server" and
    "SQL Developer 4.2" both become "SQL"), not the raw stack strings.
    """
    sql = (
        "SELECT s.skill, count(*) FROM second_brain.candidate_knowledge k, "
        "jsonb_array_elements_text(k.skills) AS s(skill) GROUP BY s.skill "
        f"HAVING count(*) >= {minimum} ORDER BY count(*) DESC, s.skill"
    )
    out = subprocess.run(
        ["docker", "exec", "csb-postgres", "psql", "-U", "csb_admin",
         "-d", "company_second_brain", "-tAF", "|", "-c", sql],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    if out.returncode != 0:
        return []
    skills: list[tuple[str, int]] = []
    for line in out.stdout.splitlines():
        if "|" in line:
            name, _, count = line.rpartition("|")
            if name.strip() and count.strip().isdigit():
                skills.append((name.strip(), int(count.strip())))
    return skills


# Demo briefs, written the way a recruiter actually writes one: a short list of genuine
# must-haves and a longer list of nice-to-haves.
#
# This exists because the Kaggle job dataset cannot produce a qualified candidate at all.
# `check_required_skills_gate` demands that EVERY required skill be present -- by design,
# so "a high semantic score can never paper over a missing required skill" -- and those
# rows lump ~18 technologies into one `Skills` column, which Gemini then structures as 18
# required skills. The best real candidate matched 4 of 18, so the gate failed for all 20
# matches on all four jobs and the demo could never reach Select -> Invite.
#
# Each brief's must-haves are filled from the pool's own top vocabulary at runtime, so the
# gate is satisfiable. Being explicit about what that does and does not show: it
# demonstrates the pipeline end to end, and it is NOT evidence about matching accuracy on
# an arbitrary unseen JD.
SYNTHETIC_BRIEFS = [
    {
        "title": "Consultant BI Senior",
        "must": 3,
        "wants": ["Power BI", "SQL", "SQL Server", "SSIS", "Excel", "Oracle"],
        "intro": "Nous recherchons un consultant Business Intelligence senior pour "
                 "accompagner nos clients grands comptes sur leurs plateformes "
                 "décisionnelles.",
        "duties": "Concevoir et maintenir les flux d'alimentation, modéliser les entrepôts "
                  "de données, construire les rapports et accompagner les utilisateurs "
                  "métier.",
        "years": 5,
    },
    {
        "title": "Data Engineer",
        "must": 3,
        "wants": ["Python", "SQL", "Airflow", "PostgreSQL", "Spark", "Docker"],
        "intro": "Nous recrutons un Data Engineer pour industrialiser les traitements de "
                 "données de nos plateformes analytiques.",
        "duties": "Construire des pipelines de données fiables, automatiser les "
                  "déploiements, et garantir la qualité et la fraîcheur des données "
                  "livrées aux équipes métier.",
        "years": 4,
    },
    {
        "title": "Consultant Big Data",
        "must": 3,
        "wants": ["Spark", "Python", "Kafka", "BigQuery", "Java", "GCP"],
        "intro": "Nous cherchons un consultant Big Data pour des missions de traitement "
                 "distribué chez nos clients.",
        "duties": "Mettre en oeuvre des traitements distribués, optimiser les temps de "
                  "calcul, et intégrer les sources de données hétérogènes.",
        "years": 4,
    },
]


def synthesise_jobs(limit: int, minimum_frequency: int = 8) -> list[dict]:
    """Builds demo briefs whose must-have skills the candidate pool genuinely has."""
    vocabulary = pool_skills(minimum_frequency)
    if not vocabulary:
        return []
    names = [name for name, _ in vocabulary]
    jobs: list[dict] = []
    for index, brief in enumerate(SYNTHETIC_BRIEFS[:limit]):
        # Domain-coherent must-haves, intersected with what the pool actually has. A
        # purely frequency-driven pick produced incoherent briefs -- a "Consultant BI
        # Senior" requiring Python/SQL/PostgreSQL and not Power BI, and a "Consultant Big
        # Data" requiring Power BI -- which would read as nonsense to a jury even though
        # the gate passed. Filtering a curated per-brief list by pool presence keeps both
        # properties: the brief makes sense AND it is satisfiable.
        must = [w for w in brief["wants"] if w in names][: brief["must"]]
        if len(must) < brief["must"]:
            must += [n for n in names if n not in must][: brief["must"] - len(must)]
        preferred = [n for n in names[:14] if n not in must][:8]
        jobs.append({
            "Title": brief["title"],
            "_required": must,
            "_preferred": preferred,
            "_intro": brief["intro"],
            "_duties": brief["duties"],
            "YearsOfExperience": str(brief["years"]),
        })
    return jobs


def jobs_by_title(titles: list[str]) -> list[dict]:
    """Exact-title lookup, for when the demo needs a specific job on screen rather than
    whatever the overlap scoring ranks highest."""
    import csv

    if not JOB_DATASET.exists():
        return []
    wanted = {t.lower() for t in titles}
    found: dict[str, dict] = {}
    with open(JOB_DATASET, encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            title = norm(row.get("Title")).lower()
            if title in wanted and title not in found:
                found[title] = row
    # Returned in the order the caller asked for, not the order the file happens to be in.
    return [found[t] for t in titles if t in found]


def compose_job_description(row: dict) -> str:
    """The dataset is already tabular; the endpoint wants prose, and prose is also what
    gives Gemini's JD structuring something real to do."""
    # A synthesised brief carries its own prose and, crucially, states required and
    # preferred separately so Gemini's structuring puts only the short list behind the
    # hard gate.
    if row.get("_required"):
        return " ".join([
            row["_intro"],
            f"Missions : {row['_duties']}",
            "Compétences obligatoires : " + ", ".join(row["_required"]) + ".",
            "Compétences appréciées : " + ", ".join(row["_preferred"]) + ".",
            f"Expérience souhaitée : {norm(row.get('YearsOfExperience'))} ans.",
        ])[:19_000]

    title = norm(row.get("Title"))
    level = norm(row.get("ExperienceLevel"))
    years = norm(row.get("YearsOfExperience"))
    skills = norm(row.get("Skills"))
    responsibilities = norm(row.get("Responsibilities"))
    keywords = norm(row.get("Keywords"))

    parts = [f"We are recruiting a {title}."]
    if level or years:
        parts.append(
            f"This is a {level.lower()} position" + (f" requiring {years} years of experience." if years else ".")
        )
    if responsibilities:
        parts.append("Responsibilities: " + responsibilities.replace("\n", "; ") + ".")
    if skills:
        parts.append("Required skills: " + skills.replace("\n", ", ") + ".")
    if keywords:
        parts.append("Relevant areas: " + keywords.replace("\n", ", ") + ".")
    return " ".join(parts)[:19_000]


def submit_jobs(jobs: list[dict], email: str, password: str, top_k: int) -> dict:
    payloads = [{"title": norm(j.get("Title")), "raw_description": compose_job_description(j),
                 "threshold": 0.6, "top_k": top_k} for j in jobs]
    script = f'''
import asyncio, json, httpx
PAYLOADS = {json.dumps(payloads)}
async def main():
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=900) as c:
        r = await c.post("/v1/auth/login", json={{"email": {email!r}, "password": {password!r}}})
        r.raise_for_status()
        head = {{"Authorization": "Bearer " + r.json()["access_token"]}}
        out = []
        for p in PAYLOADS:
            resp = await c.post("/v1/jobs", headers=head, json=p)
            out.append({{"title": p["title"], "status": resp.status_code,
                         "detail": (resp.json() if resp.status_code == 200 else resp.text[:120])}})
        print("RESULT:" + json.dumps(out))
asyncio.run(main())
'''
    out = subprocess.run(["docker", "exec", "-i", RWS, "python", "-c", script],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=3600)
    if out.returncode != 0:
        raise SystemExit(f"job submission failed:\n{(out.stderr or '')[-800:]}")
    for line in out.stdout.splitlines():
        if line.startswith("RESULT:"):
            results = json.loads(line[len("RESULT:"):])
            ok = [r for r in results if r["status"] == 200]
            return {"created": len(ok), "failed": len(results) - len(ok),
                    "errors": [r for r in results if r["status"] != 200][:3]}
    raise SystemExit(f"job submission returned nothing usable:\n{out.stdout[-500:]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually ingest; without this nothing is created (default)")
    ap.add_argument("--limit", type=int, default=0, help="cap the number of candidates")
    ap.add_argument("--jobs-only", action="store_true",
                    help="submit job descriptions and ingest NO candidates. Extraction is "
                         "asynchronous, so the two halves belong in separate steps: jobs "
                         "submitted before the candidates reach Second Brain match against "
                         "an empty pool and produce empty ranked lists")
    ap.add_argument("--jobs", type=int, default=6, help="how many job descriptions to submit")
    ap.add_argument("--synthesise", type=int, default=0, metavar="N",
                    help="compose N demo briefs from the candidate pool's own top skills "
                         "instead of using job_dataset.csv. Needed because the Kaggle rows "
                         "yield ~18 required skills and the hard gate demands every one of "
                         "them, so no candidate can ever qualify")
    ap.add_argument("--job-titles", default="",
                    help="comma-separated exact titles from job_dataset.csv to use instead "
                         "of the automatic skill-overlap selection; useful when the demo "
                         "needs a specific, legible job on screen")
    ap.add_argument("--top-k", type=int, default=20, help="top_k per job submission")
    ap.add_argument("--verified-email", default="aminesridi58@gmail.com",
                    help="given to the strongest candidate so the live Invite works; "
                         "pass '' to skip")
    ap.add_argument("--verified-name", default="Amine Sridi",
                    help="the name paired with --verified-email, so the one deliberately "
                         "real identity is internally consistent on screen")
    ap.add_argument("--show", type=int, default=2,
                    help="print this many generated CVs in full (pseudonymised)")
    args = ap.parse_args()

    candidates = load_candidates()
    pseudonymise(candidates, args.verified_email or None, args.verified_name or None)
    if args.limit:
        candidates = candidates[:args.limit]
    vocabulary = skill_vocabulary(candidates)
    if args.synthesise:
        jobs = synthesise_jobs(args.synthesise)
        if not jobs:
            print("\n  !! could not read the pool's skill vocabulary (is the stack up?)")
    elif args.job_titles.strip():
        wanted = [t.strip().lower() for t in args.job_titles.split(",") if t.strip()]
        jobs = jobs_by_title(wanted)
        missing = [t for t in wanted if not any(norm(j.get("Title")).lower() == t for j in jobs)]
        if missing:
            print(f"\n  !! not found in job_dataset.csv: {missing}")
    else:
        jobs = pick_jobs(args.jobs, vocabulary)

    by_domain: dict[str, int] = defaultdict(int)
    for candidate in candidates:
        by_domain[candidate.domain] += 1

    mode = "APPLY -- data will be created" if args.apply else "DRY RUN -- nothing created"
    print(f"\nSeed demo dataset  [{mode}]\n")
    print(f"  candidates           : {len(candidates)}")
    print(f"  by domain            : {dict(sorted(by_domain.items(), key=lambda kv: -kv[1]))}")
    print(f"  missions total       : {sum(len(c.missions) for c in candidates)}")
    print(f"  job descriptions     : {len(jobs)} (top_k={args.top_k})")
    print(f"  identity             : pseudonymised; {', '.join(IDENTITY_COLUMNS)} never written")
    if args.verified_email:
        print(f"  live-invite candidate: {candidates[0].alias_name} <{args.verified_email}>"
              if candidates else "  live-invite candidate: (none)")

    if jobs:
        # The overlap is printed, not just the title, so the selection is auditable --
        # a job picked for the wrong reason is visible here before anything is created.
        print("\n  jobs to submit:")
        for job in jobs:
            if job.get("_required"):
                # A synthesised brief states its own must-haves, which is the whole point:
                # these are the skills the hard gate will demand, so they must be visible
                # here before anything is created.
                print(f"    - {norm(job.get('Title'))}")
                print(f"        required  : {', '.join(job['_required'])}")
                print(f"        preferred : {', '.join(job['_preferred'][:6])}...")
            else:
                tokens = {t.strip().lower() for f in ("Skills", "Keywords")
                          for t in re.split(r"[,\n;()/]+", norm(job.get(f)))
                          if len(t.strip()) > 2}
                shared = sorted(tokens & vocabulary)
                more = "..." if len(shared) > 6 else ""
                print(f"    - {norm(job.get('Title'))}  [{len(shared)} shared with the "
                      f"pool: {', '.join(shared[:6])}{more}]")

    for candidate in candidates[:max(0, args.show)]:
        print(f"\n  --- generated CV for {candidate.alias_name} "
              f"({candidate.domain}, {len(candidate.missions)} missions) ---")
        for line in build_cv_text(candidate)[:18]:
            print(f"    {line}")
        print("    ...")

    if not args.apply:
        print("\n  Nothing was created. Re-run with --apply to seed.")
        print("  Run the cleanup first if the benchmark rows are still present:")
        print("    scripts/sql/clean_benchmark_rows.sql")
        return 0

    api_key = env_value(ROOT / "services" / "resume-intelligence" / ".env", "SERVICE_API_KEY")
    recruiter = env_value(ROOT / "services" / "recruitment-workflow" / ".env", "SEED_RECRUITER_EMAIL")
    password = env_value(ROOT / "services" / "recruitment-workflow" / ".env", "SEED_RECRUITER_PASSWORD")
    if not api_key:
        raise SystemExit("SERVICE_API_KEY not found in services/resume-intelligence/.env")

    if args.jobs_only:
        print("\n  --jobs-only: no candidates will be ingested.")
    else:
        if CV_DIR.exists():
            shutil.rmtree(CV_DIR)
        CV_DIR.mkdir(parents=True, exist_ok=True)
        paths = [write_cv_docx(c, CV_DIR) for c in candidates]
        print(f"\n  wrote {len(paths)} pseudonymised CVs to {CV_DIR} (gitignored)")

        print("  ingesting through POST /v1/resumes/jobs ...", flush=True)
        report = ingest_cvs(paths, api_key)
        print(f"    accepted {report['accepted']}, rejected {report['rejected']}")
        for err in report["errors"]:
            print(f"    !! {err}")
        print("  (extraction runs on the queue -- watch with: "
              "docker logs -f csb-resume-intelligence-worker)")

    if jobs and recruiter and password:
        print("\n  submitting job descriptions through POST /v1/jobs ...", flush=True)
        job_report = submit_jobs(jobs, recruiter, password, args.top_k)
        print(f"    created {job_report['created']}, failed {job_report['failed']}")
        for err in job_report["errors"]:
            print(f"    !! {err}")
    elif jobs:
        print("\n  skipped job submission: SEED_RECRUITER_EMAIL/PASSWORD not found")

    if args.jobs_only:
        print("\n  Done.")
    else:
        print("\n  Done ingesting. Extraction is asynchronous -- wait for the worker to")
        print("  drain, THEN submit the jobs as a second step:")
        print("    docker logs -f csb-resume-intelligence-worker")
        print("    python scripts/seed_demo_dataset.py --apply --jobs-only --job-titles \"...\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
