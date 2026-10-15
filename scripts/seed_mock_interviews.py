"""
scripts/seed_mock_interviews.py
--------------------------------
Gives qualified candidates a VoiceHR interview and a real, detailed assessment report.

WHAT IS REAL HERE AND WHAT IS MOCKED
Only the *conducted interview* is mocked. Everything downstream of it runs for real:

    mocked    the audio session. No WebSocket, no Gemini Live, no recording. A synthetic
              transcript is supplied instead, at a target proficiency level.
    real      backend.services.processing.process_interview -- so the deterministic
              sufficiency gate, the Gemini (or local-fallback) evaluator, the weighted
              CEFR derivation, the report row, and the signed webhook to RWS all execute
              exactly as they do after a live interview.

That split is the point: the scores in these reports are produced by the real evaluator
reading the real transcript, not written in by this script. Run it twice on the same
candidate and the evaluator may disagree with itself, which is a property of the system,
not a bug in the seeder.

TWO CONSTRAINTS THAT SHAPE IT

1. **Only candidates with a qualified match can be interviewed.** An interview hangs off
   an RWS application, and an application is created by selecting a *qualified*
   MatchResult. Of 70 seeded candidates, 20 currently qualify somewhere. Interviewing the
   other 50 would mean fabricating applications for candidates the matching engine
   rejected -- internally inconsistent, and visible to anyone who clicks from the report
   back to the match. To widen the set, add more job briefs (seed_demo_dataset.py
   --synthesise) so more candidates legitimately qualify, then re-run this.

2. **The invite endpoint cannot be used.** POST /api/invite only succeeds if the
   invitation email is genuinely delivered -- deliver_invitation_email runs synchronously
   and raises on failure -- and Resend rejects every @example.com address with a 422. So
   the interview row is created directly in VoiceHR's own SQLite, which skips the
   invite/email leg. That leg is already proven separately (milestone 43); this script is
   not trying to re-prove it.

Run:
    python scripts/seed_mock_interviews.py                 # dry run, writes nothing
    python scripts/seed_mock_interviews.py --apply
    python scripts/seed_mock_interviews.py --apply --limit 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RWS = "csb-recruitment-workflow"
VOICEHR = "csb-voicehr"
PG = "csb-postgres"


# ---------------------------------------------------------------------------------------
# Synthetic transcripts.
#
# Four proficiency levels, written as plausible non-native English with the error patterns
# each level actually shows: A2 short and tense-inconsistent, B1 functional with recurring
# agreement slips, B2 fluent with occasional register problems, C1 precise and structured.
#
# The evaluator decides the CEFR from these, so the *target* below is an intent, not an
# assertion -- the report records whatever the real evaluator concludes. Each transcript
# carries well over the sufficiency floor (30 candidate words, 3 substantive turns, 8
# distinct words) and over the 150-word comfortable threshold, so these produce graded
# reports rather than withheld ones. One deliberately thin transcript is included to
# exercise the withheld path on demand.
# ---------------------------------------------------------------------------------------

QUESTIONS = [
    "Thanks for joining. Could you introduce yourself and your background?",
    "Tell me about a project you are proud of.",
    "What was the hardest problem you faced on it?",
    "How do you usually work with the business stakeholders?",
    "How do you keep your technical skills current?",
    "Where would you like your career to go next?",
]

ANSWERS = {
    "A2": [
        "Yes hello. I am data consultant. I work four year in company for reporting and "
        "dashboard. I like this work.",
        "I make one dashboard for the sales team. They was very happy because before they "
        "use Excel file and it is slow.",
        "The hard thing is the data is not clean. Many duplicate. I must clean it by hand "
        "first, it take long time.",
        "I speak with them in meeting. Sometimes is difficult because they not explain "
        "exactly what they want.",
        "I watch video on internet and I read documentation. My colleague also help me "
        "when I not understand.",
        "I want to be more senior and learn cloud. Maybe Azure because my company use it.",
    ],
    "B1": [
        "Hello, thank you. I'm a BI consultant with about six years of experience, mostly "
        "building reporting platforms for industrial clients in France.",
        "I'm quite proud of a migration project where we moved a reporting stack from an "
        "old on-premise server to the cloud, and the users didn't notice any downtime.",
        "The hardest part was the data quality. The source systems had different rules for "
        "the same customer, so we had to agree on one definition with four different teams.",
        "I try to meet them regularly, every two weeks usually. I show them a prototype "
        "early because it's easier for them to react to something they can see.",
        "I follow a few newsletters and I do certifications when my company pays for them. "
        "Last year I passed the Azure data fundamentals one.",
        "I would like to move towards a lead role, where I still do technical work but also "
        "help junior consultants on their missions.",
    ],
    "B2": [
        "Good morning, and thanks for the time. I've spent roughly eight years in data "
        "engineering, split between consultancy work and two longer in-house roles, mainly "
        "around building pipelines and the governance around them.",
        "The project I'd single out is a near-real-time ingestion layer we built for a "
        "logistics client. They were making decisions on data that was a day old, and we "
        "brought that down to under five minutes.",
        "Honestly the technical side wasn't the difficulty. The challenge was that two "
        "departments disagreed about which system was authoritative, so until that was "
        "settled we were effectively automating a dispute rather than a process.",
        "I prefer to sit with them for the first few weeks rather than take a written spec. "
        "Written requirements tend to describe the solution they imagine instead of the "
        "problem they actually have.",
        "I read quite widely, and I rebuild things I've only read about in a small side "
        "project, because I find I don't really understand a tool until I've broken it once.",
        "I'd like to take more architectural responsibility, ideally staying close enough "
        "to the implementation that my designs remain realistic.",
    ],
    "C1": [
        "Thank you for making the time. I've worked in data and analytics for a little over "
        "a decade now, initially as a developer and increasingly as the person who sets the "
        "technical direction on large client programmes.",
        "The engagement I'd point to was consolidating eleven separate reporting systems "
        "into a single platform after a merger. The interesting constraint was political "
        "rather than technical: every system had an owner who had good reasons to keep it.",
        "The genuinely hard problem was reconciling two definitions of revenue that had both "
        "been audited and were both defensible. We ended up publishing both, clearly "
        "labelled, rather than pretending one was correct, which turned out to be what the "
        "finance team had quietly wanted all along.",
        "I treat stakeholder work as part of the engineering rather than a layer on top of "
        "it. If I can't explain a model to the person who owns the process, that usually "
        "tells me the model is wrong, not that they aren't technical enough.",
        "I keep a deliberately small set of things I follow properly rather than skimming "
        "everything, and I mentor two junior engineers, which is the fastest way I know to "
        "discover the gaps in my own understanding.",
        "I'm interested in roles where the architecture and the commercial reality are the "
        "same conversation, because separating them is how organisations end up with "
        "elegant platforms nobody uses.",
    ],
    # Deliberately below the sufficiency floor, to demonstrate a withheld verdict.
    "THIN": [
        "Yes hello.",
        "Not really.",
        "I don't know.",
        "It's fine.",
        "No.",
        "Okay.",
    ],
}

# Interview duration per level, in seconds. All comfortably above the 60s pathological
# floor; the higher ones cross the 480s mark that earns "High" confidence when the
# language sample is also substantial.
DURATIONS = {"A2": 420.0, "B1": 540.0, "B2": 600.0, "C1": 660.0, "THIN": 95.0}

# How the pool is spread across levels. Weighted toward the middle because that is what a
# real candidate pool looks like, with one thin interview so the demo can show a withheld
# verdict alongside the graded ones.
LEVEL_WEIGHTS = ["C1", "B2", "B2", "B1", "B1", "B1", "A2", "A2", "B2", "THIN"]


def transcript_for(level: str) -> list[dict]:
    answers = ANSWERS[level]
    turns: list[dict] = []
    stamp = 0
    for question, answer in zip(QUESTIONS, answers):
        turns.append({"role": "assistant", "text": question, "timestamp": stamp})
        stamp += 12
        turns.append({"role": "user", "text": answer, "timestamp": stamp})
        stamp += 48
    return turns


def assign_levels(targets: list[dict]) -> None:
    """Assigns a target level to each candidate by RANK, not by chance.

    A hash-driven choice was tried first and rejected: on 15 candidates it produced four
    thin interviews out of fifteen against an intended rate of about one in fourteen.
    Deterministic-random is lumpy at this sample size, and a demo needs a predictable
    spread across the CEFR range -- that is the whole point of showing the reports.

    Rank-based gives that, and stays coherent: a stronger CV usually comes with stronger
    spoken English. Exactly one candidate is given a thin interview, chosen from the
    middle of the ranking rather than the bottom, so the demo can show the interview
    contradicting a good CV -- which is the only reason to hold the interview at all.
    """
    ordered = sorted(targets, key=lambda t: -t["final_score"])
    total = len(ordered)
    if not total:
        return
    thin_index = total // 3 if total >= 3 else None
    for position, target in enumerate(ordered):
        if position == thin_index:
            target["level"] = "THIN"
            continue
        fraction = position / max(1, total - 1)
        if fraction <= 0.20:
            target["level"] = "C1"
        elif fraction <= 0.50:
            target["level"] = "B2"
        elif fraction <= 0.80:
            target["level"] = "B1"
        else:
            target["level"] = "A2"


def psql(sql: str) -> list[list[str]]:
    out = subprocess.run(
        ["docker", "exec", PG, "psql", "-U", "csb_admin", "-d", "company_second_brain",
         "-tAF", "|", "-c", sql],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr[:400]}")
    return [line.split("|") for line in out.stdout.splitlines() if line.strip()]


def env_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def interviewable() -> list[dict]:
    """One row per candidate who can legitimately be interviewed: their best qualified
    match, the job it belongs to, and their contact details.

    Contact details come from Resume Intelligence, which is the only service that owns
    them. They are passed to VoiceHR and nowhere else -- VoiceHR persisting
    candidate_name/candidate_email is this project's one documented PII exception, because
    it owns sending the candidate their link and greeting them by name.
    """
    # Mock-titled jobs are excluded deliberately, and not just for tidiness: an application
    # against a smoke-test job would pin that job into the demo data and the graph, where
    # it could no longer be removed without also deleting application history.
    rows = psql("""
        SELECT DISTINCT ON (m.candidate_id)
               m.candidate_id::text, m.id::text, j.id::text, j.title,
               c.name, c.email,
               COALESCE((SELECT a.id::text FROM recruitment.applications a
                          WHERE a.match_result_id = m.id LIMIT 1), ''),
               m.final_score::text
          FROM recruitment.match_results m
          JOIN recruitment.jobs j ON j.id = m.job_id
          JOIN resume_intel.candidates c ON c.id = m.candidate_id
         WHERE m.qualified
           -- Test-artifact candidates are excluded as well as test-artifact jobs. They
           -- hold the top five scores on 'Email Delivery Smoke Test Engineer' from old
           -- smoke tests, so without this the demo's best reports belong to
           -- "Smoketest Cleanup Candidate" and the genuinely strongest seeded candidate
           -- gets pushed down the ranking into the thin interview.
           AND c.name NOT LIKE 'Smoketest%'
           AND c.name NOT LIKE 'Queue Test%'
           AND j.title NOT LIKE 'Smoketest%'
           AND j.title NOT LIKE 'Batch Fetch Verification%'
           AND j.title NOT LIKE 'Section E Verification%'
           AND j.title NOT LIKE 'Embedding Space Gate%'
           AND j.title <> 'Backend Engineer (Demo)'
         ORDER BY m.candidate_id, m.final_score DESC
    """)
    out = []
    for row in rows:
        if len(row) < 7:
            continue
        if len(row) < 8:
            continue
        (candidate_id, match_id, job_id, job_title, name, email,
         application_id, score) = row[:8]
        if not (name and email):
            continue
        try:
            final_score = float(score)
        except ValueError:
            final_score = 0.0
        out.append({
            "candidate_id": candidate_id, "match_id": match_id, "job_id": job_id,
            "job_title": job_title, "name": name, "email": email,
            "application_id": application_id or None,
            "final_score": final_score,
        })
    assign_levels(out)
    out.sort(key=lambda r: (-r["final_score"], r["name"]))
    return out


def select_candidates(targets: list[dict], email: str, password: str) -> dict[str, str]:
    """Creates the missing applications through the real recruiter endpoint."""
    needed = [t for t in targets if not t["application_id"]]
    if not needed:
        return {}
    script = f'''
import asyncio, json, httpx
MATCHES = {json.dumps([t["match_id"] for t in needed])}
async def main():
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=300) as c:
        r = await c.post("/v1/auth/login", json={{"email": {email!r}, "password": {password!r}}})
        r.raise_for_status()
        head = {{"Authorization": "Bearer " + r.json()["access_token"]}}
        out = {{}}
        for match_id in MATCHES:
            resp = await c.post(f"/v1/matches/{{match_id}}/select", headers=head)
            # The field is application_id, not id (schemas.ApplicationResponse). Reading
            # "id" returned None, which serialises to null and then defeated the caller's
            # .get(key, "") default -- an AttributeError on None.startswith.
            if resp.status_code in (200, 201):
                body = resp.json()
                out[match_id] = body.get("application_id") or body.get("id") or ""
            else:
                out[match_id] = f"ERR {{resp.status_code}}: {{resp.text[:90]}}"
        print("RESULT:" + json.dumps(out))
asyncio.run(main())
'''
    out = subprocess.run(["docker", "exec", "-i", RWS, "python", "-c", script],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=1800)
    if out.returncode != 0:
        raise SystemExit(f"selection failed:\n{(out.stderr or '')[-700:]}")
    for line in out.stdout.splitlines():
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    raise SystemExit(f"selection returned nothing usable:\n{out.stdout[-400:]}")


def run_interviews(payload: list[dict]) -> list[dict]:
    """Creates each interview row and runs the REAL processing pipeline over the
    synthetic transcript, inside the VoiceHR container."""
    script = '''
import asyncio, json, sqlite3, sys
from datetime import datetime, timedelta
import backend.database as d
from backend.services.processing import process_interview

jobs = json.loads(sys.stdin.read())
results = []

async def main():
    for job in jobs:
        conn = sqlite3.connect(d.DB_PATH)
        try:
            now = datetime.utcnow()
            cur = conn.execute(
                "INSERT INTO interviews (token, candidate_name, candidate_email, language,"
                " status, created_at, expires_at, started_at, external_reference_id, job_title)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job["token"], job["name"], job["email"], "en", "active",
                 now.isoformat(), (now + timedelta(days=7)).isoformat(), now.isoformat(),
                 job["application_id"], job["job_title"]),
            )
            conn.commit()
            interview_id = cur.lastrowid
        finally:
            conn.close()
        try:
            # The real pipeline: sufficiency gate, evaluator, report row, RWS webhook.
            await process_interview(job["token"], job["transcript"], None,
                                    job["duration"], "completed")
            conn = sqlite3.connect(d.DB_PATH)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT cefr_level, overall_score, confidence, evaluation_status,"
                    " sufficiency_reason FROM reports WHERE interview_id = ?",
                    (interview_id,)).fetchone()
            finally:
                conn.close()
            results.append({
                "name": job["name"], "target": job["level"],
                "cefr": row["cefr_level"] if row else None,
                "score": row["overall_score"] if row else None,
                "confidence": row["confidence"] if row else None,
                "status": row["evaluation_status"] if row else "NO REPORT",
                "reason": row["sufficiency_reason"] if row else None,
            })
        except Exception as exc:
            results.append({"name": job["name"], "target": job["level"],
                            "status": f"ERROR {type(exc).__name__}: {str(exc)[:80]}"})
    print("RESULT:" + json.dumps(results))

asyncio.run(main())
'''
    out = subprocess.run(["docker", "exec", "-i", VOICEHR, "python", "-c", script],
                         input=json.dumps(payload), capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=7200)
    if out.returncode != 0:
        raise SystemExit(f"interview run failed:\n{(out.stderr or '')[-900:]}")
    for line in out.stdout.splitlines():
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    raise SystemExit(f"interview run returned nothing usable:\n{out.stdout[-600:]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually create interviews and reports (default: dry run)")
    ap.add_argument("--limit", type=int, default=0, help="cap how many candidates")
    args = ap.parse_args()

    targets = interviewable()
    if args.limit:
        targets = targets[:args.limit]

    spread: dict[str, int] = {}
    for target in targets:
        spread[target["level"]] = spread.get(target["level"], 0) + 1

    mode = "APPLY -- interviews and reports will be created" if args.apply else "DRY RUN"
    print(f"\nSeed mock VoiceHR interviews  [{mode}]\n")
    print(f"  interviewable candidates : {len(targets)}  (qualified on at least one job)")
    print(f"  target level spread      : {dict(sorted(spread.items()))}")
    print(f"  already have an application: "
          f"{sum(1 for t in targets if t['application_id'])}")
    print(f"  need selecting first     : {sum(1 for t in targets if not t['application_id'])}")
    print("\n  mocked: the audio session only. The sufficiency gate, the evaluator, the")
    print("  report and the RWS webhook all run for real on the synthetic transcript.")

    print(f"\n  {'candidate':<26}{'target':<8}{'job':<24}words")
    for target in targets[:40]:
        words = sum(len(t["text"].split()) for t in transcript_for(target["level"])
                    if t["role"] == "user")
        print(f"  {target['name'][:25]:<26}{target['level']:<8}"
              f"{target['job_title'][:23]:<24}{words}")

    if not args.apply:
        print("\n  Nothing was created. Re-run with --apply.")
        return 0

    email = env_value(ROOT / "services" / "recruitment-workflow" / ".env", "SEED_RECRUITER_EMAIL")
    password = env_value(ROOT / "services" / "recruitment-workflow" / ".env", "SEED_RECRUITER_PASSWORD")
    if not (email and password):
        raise SystemExit("SEED_RECRUITER_EMAIL/PASSWORD not found in RWS .env")

    created = select_candidates(targets, email, password)
    for target in targets:
        if not target["application_id"]:
            # Defensive: a missing key and an explicit null both have to read as "no id".
            result = created.get(target["match_id"]) or ""
            if result.startswith("ERR"):
                print(f"  !! select failed for {target['name']}: {result}")
            else:
                target["application_id"] = result
    ready = [t for t in targets if t["application_id"]]
    print(f"\n  applications ready: {len(ready)}/{len(targets)}")

    payload = [{
        "token": secrets.token_urlsafe(24),
        "name": t["name"], "email": t["email"], "job_title": t["job_title"],
        "application_id": t["application_id"], "level": t["level"],
        "transcript": transcript_for(t["level"]), "duration": DURATIONS[t["level"]],
    } for t in ready]

    print(f"  running {len(payload)} evaluations through the real pipeline "
          f"(this is the slow part) ...", flush=True)
    results = run_interviews(payload)

    print(f"\n  {'candidate':<26}{'target':<8}{'CEFR':<6}{'score':<7}{'conf':<9}status")
    for result in results:
        print(f"  {result['name'][:25]:<26}{result.get('target',''):<8}"
              f"{str(result.get('cefr') or '-'):<6}{str(result.get('score') or '-'):<7}"
              f"{str(result.get('confidence') or '-'):<9}{result.get('status')}"
              + (f"  [{result['reason']}]" if result.get("reason") else ""))

    graded = sum(1 for r in results if r.get("cefr"))
    print(f"\n  reports with a CEFR verdict : {graded}/{len(results)}")
    print(f"  withheld (no verdict)       : {len(results) - graded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
