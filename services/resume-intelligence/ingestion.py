"""
services/resume-intelligence/ingestion.py
------------------------------------------
The resume ingestion pipeline, in one place.

Extracted from `api.py`'s `POST /v1/resumes` when ingestion moved onto a queue, so the
synchronous endpoint and the ARQ worker run **the same code** rather than two copies that
drift. The endpoint keeps its HTTP semantics by mapping `IngestionError.reason` to a
status code; the worker persists the same reason to `ingest_jobs.error`.

PII discipline, unchanged from the original endpoint and the reason errors are modelled
this way: the pipeline's own exception messages can embed extracted candidate data
(name/email/phone). So callers get a **fixed, caller-chosen reason string**, never
`str(exc)`, and logs carry only the exception *type*.
"""
from __future__ import annotations

import logging
import uuid
from typing import Tuple

logger = logging.getLogger(__name__)


class IngestionError(Exception):
    """A failure with a PII-free reason safe to log, persist and return to a caller.

    `status_code` exists so the HTTP endpoint keeps the exact statuses it returned
    before this refactor (502 for a failed extraction or a failed Second Brain push,
    500 for anything unexpected).
    """

    def __init__(self, reason: str, status_code: int = 500):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


async def run_ingestion(upload_path: str, settings) -> Tuple[uuid.UUID, bool]:
    """
    Extract → parse → store → push to Second Brain.

    Returns (candidate_id, is_new). Raises IngestionError with a generic reason.

    Safe to run more than once for the same file: `store_candidate` dedups on normalized
    email and Second Brain upserts by candidate_id, which is what makes the queue's
    at-least-once delivery acceptable.
    """
    # Imported lazily, exactly as api.py did: a missing optional extraction dependency
    # in a test environment must not break app (or worker) startup.
    from db import _get_session_factory, store_candidate
    from pipeline.parsing_agent import parse_resume_text
    from pipeline.text_extractor import extract_text

    from llm_provider import build_llm_provider

    try:
        extraction = extract_text(upload_path)
    except Exception as exc:
        logger.error("text_extraction_failed", extra={"error_type": type(exc).__name__})
        raise IngestionError("Text extraction failed", status_code=502) from exc

    llm = build_llm_provider(settings)
    try:
        parse_result = await parse_resume_text(
            llm, extraction.raw_text, extraction.file_meta, model=settings.gemini_model
        )
    except Exception as exc:
        logger.error("structured_extraction_failed", extra={"error_type": type(exc).__name__})
        raise IngestionError("Structured extraction failed", status_code=502) from exc

    if not parse_result.success:
        # Every bounded retry failed. Do not store a placeholder "Unknown" candidate --
        # a fake success is worse than a visible failure.
        raise IngestionError("Structured extraction failed", status_code=502)

    candidate_json = parse_result.data

    session = _get_session_factory()()
    try:
        candidate_id, is_new = store_candidate(session, candidate_json, extraction.raw_text)
    except Exception as exc:
        logger.error("candidate_store_failed", extra={"error_type": type(exc).__name__})
        raise IngestionError("Failed to store candidate", status_code=500) from exc
    finally:
        session.close()

    # The candidate is now durably committed. Everything below is a separate, later step
    # whose failure must never undo that -- hence a distinct error rather than a rollback.
    from pipeline.candidate_summary import build_candidate_summary
    from second_brain_client import SecondBrainClientError, send_candidate_summary

    try:
        summary = build_candidate_summary(candidate_id, candidate_json)
        await send_candidate_summary(
            settings.second_brain_url, settings.second_brain_api_key, summary
        )
    except SecondBrainClientError as exc:
        logger.error(
            "second_brain_ingestion_failed",
            extra={"error_type": type(exc).__name__, "candidate_id": str(candidate_id)},
        )
        raise IngestionError(
            f"Resume stored (candidate_id={candidate_id}) but Second Brain ingestion "
            "failed. Retrying this upload is safe.",
            status_code=502,
        ) from exc

    return candidate_id, is_new
