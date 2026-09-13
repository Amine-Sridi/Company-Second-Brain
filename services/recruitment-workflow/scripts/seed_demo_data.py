#!/usr/bin/env python3
"""
================================================================================
 DEMO / DEV TOOLING ONLY -- NOT PART OF THE APPLICATION, NEVER RUN IN PRODUCTION
================================================================================

Recreates just enough RWS demo data (Job, MatchResults, Applications) for the
Dashboard / Jobs / ranked-matches / Applications / Knowledge Graph views to have
something real to show, after every recruitment.jobs/applications/match_results row
was accidentally wiped by an integration-test fixture pointed at the wrong database
(see handoff.md's Known Limitations). This script is the deliberate, explicit,
reviewable replacement for "just re-run whatever created the data before" -- it does
nothing this file doesn't say plainly.

What this script does, in order:
  1. Logs in as the one seeded recruiter (recruiter@example.com) -- already exists,
     never created here.
  2. Ensures ONE demo job exists ("Backend Engineer (Demo)"). If it already exists
     (checked by title), it is reused and NOT recreated -- this is the only step
     that can call Gemini (JD structuring + one embedding call), and only the first
     time this script is ever run against a given database.
  3. Selects a handful of the job's already-scored, already-qualified candidates
     into Applications, via the real POST /v1/matches/{id}/select endpoint (this
     calls no AI provider and sends no email). Skips any candidate already
     selected for this job.
  4. Reconnects ONE application to VoiceHR's own surviving interview history, if
     a real one exists for a candidate this script selected: relinks VoiceHR's
     interview row to the new application's id (a plain UPDATE, no email sent, no
     interview content touched), then replays RWS's own real webhook endpoint
     with VoiceHR's actual currently-stored report data (not fabricated) so the
     application reaches 'interviewed' through the exact same code path a real
     webhook delivery would.

What this script deliberately never does:
  - Never fabricates a candidate, a resume, or a VoiceHR assessment. Every
    candidate_id it uses is looked up from Resume Intelligence's real, existing
    /v1/candidates list; every score comes from RWS's own real scoring.py; every
    interview_assessment comes from VoiceHR's own real, already-persisted report.
  - Never calls POST /v1/applications/{id}/invite -- that endpoint now emails the
    candidate for real (see handoff.md milestone 27), and this script must never
    send a real email just to seed demo data.
  - Never runs unless invoked with --yes on a real TTY-driven command line. There
    is no code path anywhere else in this service that imports or calls this
    script -- it cannot run "automatically" as a side effect of anything.

Requires: the full docker compose stack running locally (RWS reachable at
--rws-url, and `docker exec` access to csb-postgres and csb-voicehr for the one
reconnect step in requirement 4 above -- both use the exact same commands you
would type by hand, just made repeatable and reviewable).

Usage:
    python services/recruitment-workflow/scripts/seed_demo_data.py --yes
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

import httpx

DEMO_JOB_TITLE = "Backend Engineer"
# Deliberately short and unambiguous, not a more "realistic-sounding" longer JD --
# Gemini's own JD-structuring extracts required_skills verbatim from whatever
# phrasing it's given (e.g. "building and deploying REST APIs" extracted as two
# separate required skills, "REST APIs" and "API Deployment", in an earlier attempt
# at seeding this exact job), and this project's existing candidate pool's own
# Second Brain skill lists only ever contain simple, explicit skill names like
# "Python"/"PostgreSQL". A longer, more natural-sounding description produced
# required_skills nothing in the candidate pool could satisfy, and every candidate
# came back disqualified -- this exact text is what the very first live-verified
# job description in this project's history used, and it reliably qualifies most of
# the existing candidate pool.
DEMO_JOB_DESCRIPTION = "Backend engineer role requiring Python and PostgreSQL experience."
# How many of the job's already-qualified candidates to select into demo Applications.
# Small and fixed -- this is a demo backbone, not a load test.
DEMO_APPLICATION_COUNT = 3

RECRUITER_EMAIL = "recruiter@example.com"
RECRUITER_PASSWORD = "ChangeMe123!"

POSTGRES_CONTAINER = "csb-postgres"
POSTGRES_USER = "csb_admin"
POSTGRES_DB = "company_second_brain"
VOICEHR_CONTAINER = "csb-voicehr"


def _log(message: str) -> None:
    print(f"[seed_demo_data] {message}")


def _run_docker(*args: str) -> str:
    """Runs a docker command and returns its stdout, raising on any non-zero exit --
    this script never silently swallows a failed Docker call, since a "succeeded"
    seed run must actually mean the data is there."""
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout


def _psql(sql: str) -> str:
    return _run_docker(
        "exec", POSTGRES_CONTAINER, "psql", "-U", POSTGRES_USER, "-d", POSTGRES_DB,
        "-t", "-A", "-c", sql,
    )


def _voicehr_python(code: str) -> str:
    return _run_docker("exec", VOICEHR_CONTAINER, "python", "-c", code)


class RwsClient:
    def __init__(self, base_url: str):
        self._client = httpx.Client(base_url=base_url, timeout=30.0)
        self._token: str | None = None

    def login(self) -> None:
        resp = self._client.post(
            "/v1/auth/login",
            json={"email": RECRUITER_EMAIL, "password": RECRUITER_PASSWORD},
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        _log(f"logged in as {RECRUITER_EMAIL}")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    def get_jobs(self) -> list[dict]:
        resp = self._client.get("/v1/jobs", headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def create_job(self, title: str, raw_description: str) -> dict:
        resp = self._client.post(
            "/v1/jobs",
            json={"title": title, "raw_description": raw_description},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def get_matches(self, job_id: str) -> list[dict]:
        resp = self._client.get(f"/v1/jobs/{job_id}/matches", headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def get_applications(self) -> list[dict]:
        resp = self._client.get("/v1/applications", headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def select(self, match_id: str) -> dict | None:
        """Returns the new Application, or None if this candidate was already
        selected for this job (RWS's own 409 -- not an error for this script's
        purposes, since idempotent re-runs must not fail on it)."""
        resp = self._client.post(f"/v1/matches/{match_id}/select", headers=self._headers())
        if resp.status_code == 409:
            return None
        resp.raise_for_status()
        return resp.json()

    def get_candidates(self) -> list[dict]:
        resp = self._client.get("/v1/candidates", headers=self._headers())
        resp.raise_for_status()
        return resp.json()


def ensure_demo_job(client: RwsClient, job_title: str) -> dict:
    """
    job_title defaults to DEMO_JOB_TITLE but is caller-overridable (--job-title) --
    specifically so that if Gemini's free-tier daily quota is already exhausted (a
    real, previously-documented constraint -- see handoff.md), an operator can point
    this at any already-existing job with usable qualified matches (e.g. one left
    over from a prior session's live testing) instead of this script failing on the
    one step that needs a fresh Gemini call.
    """
    for job in client.get_jobs():
        if job["title"] == job_title:
            _log(f"job {job_title!r} already exists ({job['job_id']}) -- reusing, no Gemini call made")
            return job
    _log(f"job {job_title!r} not found -- creating it now (one Gemini JD-structuring + one embedding call)")
    created = client.create_job(job_title, DEMO_JOB_DESCRIPTION)
    return {"job_id": created["job_id"], "title": created["title"]}


def ensure_demo_applications(
    client: RwsClient, job_id: str, priority_candidate_ids: set[str]
) -> list[dict]:
    """
    Selects up to DEMO_APPLICATION_COUNT of the job's qualified candidates.
    priority_candidate_ids (candidates with a surviving VoiceHR interview, see
    find_candidates_with_voicehr_history) are always included first, regardless of
    score rank -- otherwise a genuinely reconnectable report could rank below the
    cutoff and never get selected at all, silently defeating the one part of this
    script that recovers real history instead of just producing fresh matches.
    """
    matches = client.get_matches(job_id)
    qualified = [m for m in matches if m["qualified"]]
    qualified.sort(key=lambda m: (m["candidate_id"] not in priority_candidate_ids, -m["final_score"]))
    chosen = qualified[:DEMO_APPLICATION_COUNT]

    existing_by_match = {
        a["match_result_id"]: a for a in client.get_applications() if a["job_id"] == job_id
    }

    applications = []
    for match in chosen:
        existing = existing_by_match.get(match["match_id"])
        if existing:
            _log(f"candidate {match['candidate_id']} already selected for this job -- reusing")
            applications.append(existing)
            continue
        created = client.select(match["match_id"])
        if created is None:
            _log(f"candidate {match['candidate_id']} select returned 409 -- already exists, refetching")
            existing_by_match = {
                a["match_result_id"]: a for a in client.get_applications() if a["job_id"] == job_id
            }
            applications.append(existing_by_match[match["match_id"]])
            continue
        _log(f"selected candidate {match['candidate_id']} -> application {created['application_id']}")
        applications.append(created)
    return applications


def find_voicehr_interview_for_email(candidate_email: str) -> dict | None:
    """Looks up VoiceHR's own SQLite for a completed interview belonging to this
    candidate email, without touching or modifying anything yet -- read-only."""
    output = _voicehr_python(
        "import sqlite3, json; "
        "conn = sqlite3.connect('storage/voicehr.db'); conn.row_factory = sqlite3.Row; "
        "rows = conn.execute("
        "    \"SELECT i.id, i.token, i.external_reference_id, r.overall_score "
        "     FROM interviews i JOIN reports r ON r.interview_id = i.id "
        "     WHERE i.candidate_email = ? ORDER BY i.id DESC LIMIT 1\", "
        f"    ({candidate_email!r},)"
        ").fetchall(); "
        "print(json.dumps([dict(row) for row in rows]))"
    )
    rows = json.loads(output)
    return rows[0] if rows else None


def find_candidates_with_voicehr_history(candidates_by_id: dict) -> set[str]:
    """Returns the candidate_ids that have a surviving, completed VoiceHR interview
    -- these must be prioritized into demo Applications (see
    ensure_demo_applications) specifically so reconnect_voicehr_report has something
    real to reconnect. One cheap docker-exec lookup per candidate; this script's
    candidate pool is small (a handful of existing rows), not a scale concern."""
    found = set()
    for candidate_id, candidate in candidates_by_id.items():
        email = candidate.get("email")
        if email and find_voicehr_interview_for_email(email):
            found.add(candidate_id)
    return found


def reconnect_voicehr_report(client: RwsClient, base_url: str, application: dict, candidate_email: str) -> bool:
    """
    Relinks one already-completed VoiceHR interview to a freshly-created Application,
    then replays RWS's real webhook with VoiceHR's own real, already-stored report
    data -- see this module's own docstring for exactly why each step exists.
    Returns True if a reconnection happened, False if there was nothing to reconnect
    (no surviving VoiceHR interview for this candidate) or it was already done.
    """
    if application.get("interview_status") is not None:
        _log(f"application {application['application_id']} already has an interview result -- nothing to reconnect")
        return False

    interview = find_voicehr_interview_for_email(candidate_email)
    if interview is None:
        _log(f"no surviving VoiceHR interview found for {candidate_email} -- skipping reconnection")
        return False

    application_id = application["application_id"]
    _log(
        f"found surviving VoiceHR interview id={interview['id']} for {candidate_email} "
        f"(real overall_score={interview['overall_score']}) -- reconnecting to application {application_id}"
    )

    # Step 1: relink VoiceHR's own row to the new application id. No email, no
    # content change -- purely repointing which application_id this interview
    # reports back to.
    _voicehr_python(
        "import sqlite3; conn = sqlite3.connect('storage/voicehr.db'); "
        f"conn.execute(\"UPDATE interviews SET external_reference_id = '{application_id}' WHERE id = {interview['id']}\"); "
        "conn.commit(); print('relinked')"
    )

    # Step 2: advance the application to 'invited' via a direct, explicit UPDATE --
    # NOT via POST /v1/applications/{id}/invite, which would attempt to send a real
    # email (see this module's docstring). This is the one place this script writes
    # to Postgres directly rather than through the HTTP API, and it writes only
    # workflow state (an invite did, historically, really happen for this
    # candidate/interview pair in an earlier, now-deleted application row).
    now = datetime.now(timezone.utc).isoformat()
    _psql(
        f"UPDATE recruitment.applications SET status = 'invited', "
        f"invite_attempt_number = 1, invited_at = '{now}', updated_at = '{now}' "
        f"WHERE id = '{application_id}' AND status = 'selected';"
    )

    # Step 3: fetch VoiceHR's OWN real, already-persisted report for this interview
    # (via the same RWS-facing endpoint the real proxy uses) and replay it through
    # RWS's real, already-tested webhook endpoint -- not a hand-built fake payload.
    report_resp = httpx.get(
        f"{base_url}/v1/interviews/{application_id}/report",
        headers=client._headers(),
        timeout=30.0,
    )
    report_resp.raise_for_status()
    report = report_resp.json()

    import hashlib
    import hmac
    import uuid

    # Sign with RWS's own real secret (read from its running container's env) so
    # this replayed webhook is authenticated exactly like a genuine one -- never
    # hardcoded here, and never logged.
    secret = _run_docker(
        "exec", "csb-recruitment-workflow", "printenv", "WEBHOOK_HMAC_SECRET"
    ).strip()

    assessment = {
        "overall_score": report.get("overall_score"),
        "language": report.get("language"),
        "cefr_level": report.get("cefr_level"),
        "fluency_score": report.get("dimensions", {}).get("fluency"),
        "vocabulary_score": report.get("dimensions", {}).get("vocabulary"),
        "grammar_score": report.get("dimensions", {}).get("grammar"),
        "pronunciation_score": report.get("dimensions", {}).get("pronunciation"),
        "comprehension_score": report.get("dimensions", {}).get("comprehension"),
        "inconclusive": bool(report.get("inconclusive")),
        "evaluation_status": report.get("evaluation_status", "completed"),
    }
    payload = {
        "delivery_id": str(uuid.uuid4()),
        "external_reference_id": application_id,
        "interview_status": (
            "failed" if assessment["evaluation_status"] == "failed"
            else "inconclusive" if assessment["inconclusive"]
            else "completed"
        ),
        "assessment": assessment,
        "completed_at": now,
    }
    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    webhook_resp = httpx.post(
        f"{base_url}/v1/webhooks/voicehr",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": signature,
            "X-Delivery-Id": payload["delivery_id"],
        },
        timeout=30.0,
    )
    webhook_resp.raise_for_status()
    _log(f"replayed real webhook for application {application_id}: {webhook_resp.json()}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rws-url", default="http://localhost:8003", help="RWS base URL (default: %(default)s)")
    parser.add_argument(
        "--job-title", default=DEMO_JOB_TITLE,
        help=(
            "Title of the job to ensure/reuse (default: %(default)r). Point this at "
            "an already-existing job with usable qualified matches to skip the one "
            "Gemini-calling step entirely, e.g. if the daily quota is exhausted."
        ),
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Required. Confirms you intend to write demo data to whatever --rws-url points at.",
    )
    args = parser.parse_args()

    if not args.yes:
        print(__doc__)
        print(
            "Refusing to run without --yes. This is demo/dev tooling only -- pass "
            "--yes once you've confirmed --rws-url points at your local demo stack, "
            "not anything you care about keeping in its current state.",
            file=sys.stderr,
        )
        return 1

    client = RwsClient(args.rws_url)
    client.login()

    job = ensure_demo_job(client, args.job_title)

    candidates_by_id = {c["candidate_id"]: c for c in client.get_candidates()}
    priority_candidate_ids = find_candidates_with_voicehr_history(candidates_by_id)
    if priority_candidate_ids:
        _log(f"{len(priority_candidate_ids)} candidate(s) have a surviving VoiceHR interview -- prioritizing them")

    applications = ensure_demo_applications(client, job["job_id"], priority_candidate_ids)

    reconnected_any = False
    for application in applications:
        candidate = candidates_by_id.get(application["candidate_id"])
        if not candidate or not candidate.get("email"):
            continue
        if reconnect_voicehr_report(client, args.rws_url, application, candidate["email"]):
            reconnected_any = True

    _log("done.")
    _log(f"demo job: {job['title']} ({job['job_id']})")
    _log(f"applications ensured: {len(applications)}")
    _log(f"VoiceHR report reconnected: {reconnected_any}")
    _log("No VoiceHR invitation email was sent by this script at any point.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
