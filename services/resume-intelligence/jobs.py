"""
services/resume-intelligence/jobs.py
-------------------------------------
The ARQ worker that runs resume ingestion off the request path.

Why this exists (see CLAUDE.md's amended constraint and
SCALING_AND_FUTURE_IMPROVEMENTS.md): `POST /v1/resumes` `await`ed the whole extraction
pipeline -- a ~10s Gemini call -- *inside the HTTP request*, holding one worker per CV.
Against the stated target of 100,000 CVs/day (~3.5/s over a working day) that ceiling is
reached in the low hundreds per day.

Deliberate properties, each of which is a correctness decision rather than a style one:

* **Redis holds no PII.** The enqueued payload is a job id and nothing else. The uploaded
  file lives on the `resume_uploads` volume, mounted only by this service and its worker,
  so "candidate PII lives only in Resume Intelligence" is unchanged.
* **Postgres is the source of truth.** `resume_intel.ingest_jobs` is written before the
  job is enqueued and at every transition. A flushed Redis costs throughput, never a
  record.
* **The pipeline logic is imported, not duplicated.** `run_ingestion` is the same code
  path the synchronous endpoint uses, so the two cannot drift.
* **Errors are generic.** The pipeline's exception messages can embed extracted candidate
  data (name/email/phone), so only the exception *type* is ever logged and only a fixed
  reason string is persisted -- the same rule api.py already follows.

Run:
    arq jobs.WorkerSettings
"""
from __future__ import annotations

import logging
import os
import uuid

from api import settings  # one Settings object, one .env -- never a second config
from ingestion import IngestionError, run_ingestion

logger = logging.getLogger(__name__)

# How long one ingestion may take before ARQ kills it. Generous: a slow Gemini call plus
# OCR on a scanned PDF is legitimately slow, and killing a job that would have succeeded
# just burns the work again on retry.
JOB_TIMEOUT_SECONDS = 600

# ARQ retries a job that raises -- and this is deliberately 1, not 3.
#
# parse_resume_text already runs its own bounded retry loop (MAX_RETRIES = 3 in
# pipeline/parsing_agent.py). Layering an ARQ retry on top multiplies rather than adds:
# 3 x 3 = up to **9 full Gemini extractions** for one CV, each also re-paying for text
# extraction. That is exactly the nested-retry-budget defect audit finding M4 found in
# VoiceHR's evaluator ("3 x 2 meant one interview could burn six full-transcript
# evaluation calls"), reappearing in a new place -- and it was visible in a real run
# here, where a single upload spent 258s failing.
#
# So the pipeline owns retry, and the queue does not second-guess it. A genuinely
# transient infrastructure failure (worker killed mid-job) leaves the row as `running`
# and is recoverable by re-enqueueing from ingest_jobs, which is what that table is for.
MAX_TRIES = 1


def _redis_settings():
    from arq.connections import RedisSettings

    return RedisSettings.from_dsn(settings.redis_url)


async def ingest_resume_job(ctx: dict, job_id: str) -> dict:
    """
    One queued ingestion. Takes only an opaque job id -- everything else is looked up
    from Postgres, so nothing about the candidate ever passes through Redis.
    """
    from db import (
        get_ingest_job,
        mark_ingest_job_failed,
        mark_ingest_job_running,
        mark_ingest_job_succeeded,
    )
    from db import _get_session_factory

    job_uuid = uuid.UUID(job_id)
    session = _get_session_factory()()
    try:
        job = get_ingest_job(session, job_uuid)
        if job is None:
            # Nothing to do and nothing to retry -- the row is the source of truth, so if
            # it is gone the work is not ours to invent.
            logger.error("ingest_job_missing", extra={"job_id": job_id})
            return {"status": "missing"}
        if job.status == "succeeded":
            # ARQ delivered the same job twice (at-least-once). Ingestion is idempotent
            # downstream anyway (email dedup, upsert by candidate_id), but returning
            # early avoids re-paying for a Gemini call.
            return {"status": "already_succeeded", "candidate_id": str(job.candidate_id)}

        upload_path = job.upload_path
        mark_ingest_job_running(session, job_uuid)

        try:
            candidate_id, is_new = await run_ingestion(upload_path, settings)
        except IngestionError as exc:
            # exc.reason is a fixed, PII-free string chosen by the pipeline wrapper.
            mark_ingest_job_failed(session, job_uuid, exc.reason)
            logger.error(
                "ingest_job_failed",
                extra={"job_id": job_id, "error_type": type(exc).__name__, "reason": exc.reason},
            )
            # Raise so ARQ retries within MAX_TRIES for transient causes (Gemini 503,
            # Second Brain unreachable). A deterministic failure simply exhausts them.
            raise
        except Exception as exc:
            mark_ingest_job_failed(session, job_uuid, "Unexpected ingestion error")
            logger.error(
                "ingest_job_failed",
                extra={"job_id": job_id, "error_type": type(exc).__name__},
            )
            raise

        mark_ingest_job_succeeded(session, job_uuid, candidate_id, is_new)
        logger.info(
            "ingest_job_succeeded",
            extra={"job_id": job_id, "candidate_id": str(candidate_id), "is_new": is_new},
        )
        # The raw upload is PII and has served its purpose. Remove it on success; a
        # failed job keeps the file so the work can be retried without re-uploading.
        try:
            os.unlink(upload_path)
        except OSError:
            pass
        return {"status": "succeeded", "candidate_id": str(candidate_id), "is_new": is_new}
    finally:
        session.close()


class WorkerSettings:
    """ARQ entry point: `arq jobs.WorkerSettings`."""

    functions = [ingest_resume_job]
    job_timeout = JOB_TIMEOUT_SECONDS
    max_tries = MAX_TRIES
    # Set from measurement, and the measurement contradicted the obvious guess.
    #
    # The reasoning was: an ingestion is dominated by a network wait on Gemini, so more
    # in-flight jobs should mean more CVs/hour. Raising this 4 -> 12 and re-running the
    # same 24-CV load test made throughput slightly *worse* (908.8 -> 822.7 CV/h, per-job
    # median 48.2s -> 53.7s).
    #
    # Why: in that run 23 of 24 extractions were served by the **local Ollama fallback**
    # (Gemini was quota-exhausted), and Ollama serialises on one laptop GPU. Concurrency
    # against a saturated resource only adds queueing. So 4 is the measured-better value
    # *in fallback mode*, which is the mode this machine is usually in.
    #
    # This is therefore still untuned for the healthy-Gemini path, where the original
    # reasoning probably does hold. Re-run benchmarks/load/ingest_load_test.py with quota
    # available before raising it, and check the provider mix in the worker log first.
    max_jobs = 4

    # An attribute, not a method: arq reads `settings.host` straight off this object
    # (arq/connections.py: create_pool), so a staticmethod here fails at worker startup
    # with "'staticmethod' object has no attribute 'host'".
    redis_settings = _redis_settings()

    @staticmethod
    async def on_startup(ctx: dict) -> None:
        from shared.observability import configure_json_logging

        configure_json_logging("resume-intelligence-worker", settings.log_level)
        logger.info("ingest_worker_started", extra={"max_jobs": WorkerSettings.max_jobs})

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:
        logger.info("ingest_worker_stopped")
