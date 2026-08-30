"""
FastAPI application for Resume Intelligence.

Endpoints:
  GET  /health
  POST /v1/resumes                          — service-key auth, ingest a resume file, returns candidate_id
  GET  /v1/candidates/{candidate_id}/contact — service-key auth, returns PII contact data

Both non-health endpoints use the same X-Api-Key service-key mechanism -- there is no
separate auth system here, and this service never validates a recruiter JWT (that
happens once, upstream, in RWS). The current caller of POST /v1/resumes is RWS's own
recruiter-facing upload endpoint (POST /v1/resumes on RWS, JWT-protected there); the
same service-key mechanism is already what a future trusted caller (e.g. an
email-ingestion workflow) would use too -- no new auth surface would be needed for that.
"""
from __future__ import annotations

import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.orm import Session

from db import Candidate, get_db, store_candidate

# shared/ is available at /app/shared/ in Docker (build context = project root).
# In local dev / tests, the project root must be on sys.path (see tests/conftest.py).
from shared.schemas import ContactLookupResponse
from shared.observability import CorrelationIdMiddleware, configure_json_logging
from shared.llm import LLMProvider, LLMProviderError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_api_key: str = "changeme-resume-intelligence-key"
    log_level: str = "INFO"

    # Gemini is the sole AI provider for this project (see CLAUDE.md).
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"

    # Second Brain: where the PII-safe CandidateSummary is pushed after a resume
    # is successfully stored (see pipeline/candidate_summary.py).
    second_brain_url: str = "http://second-brain:8000"
    second_brain_api_key: str = "changeme-second-brain-key"


settings = Settings()
configure_json_logging("resume-intelligence", settings.log_level)


# ---------------------------------------------------------------------------
# LLM provider (lazy singleton -- only instantiated on first resume upload)
# ---------------------------------------------------------------------------

_llm_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    global _llm_provider
    if _llm_provider is None:
        from shared.llm import GeminiProvider
        try:
            _llm_provider = GeminiProvider(api_key=settings.gemini_api_key or None)
        except LLMProviderError as exc:
            # Safe to include verbatim: this is a startup/config error (missing
            # GEMINI_API_KEY or the google-genai package), never candidate data.
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    return _llm_provider

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Resume Intelligence")
app.add_middleware(CorrelationIdMiddleware)

# .doc (legacy pre-2007 binary Word format) is deliberately not supported -- it
# would require the antiword CLI tool, which this service's Docker image does
# not install (see pipeline/text_extractor.py's module docstring).
_ALLOWED_SUFFIXES = {".pdf", ".docx", ".jpg", ".jpeg", ".png"}


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def require_service_key(
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    if x_api_key != settings.service_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing service API key",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "service": "resume-intelligence"}


@app.post("/v1/resumes", status_code=status.HTTP_201_CREATED)
async def ingest_resume(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm_provider),
    _auth: None = Depends(require_service_key),
):
    """
    Accept a resume file, run the extraction + parsing pipeline, store the candidate
    with normalized-email dedup, and return the candidate_id.

    Requires X-Api-Key service key. This service never validates a recruiter JWT --
    RWS is the only current caller, and its own recruiter-facing upload endpoint is
    what checks the JWT, before forwarding here with the service key (never exposed
    to the recruiter's browser).

    Idempotent: submitting the same candidate email again returns the existing id
    (is_new=false) without modifying any data.

    If text extraction succeeds but Gemini structured extraction fails completely
    (after its own bounded retries), no candidate is stored -- this returns 502
    rather than a fake success with a placeholder candidate.

    After the candidate is stored, a PII-safe CandidateSummary is pushed to
    Second Brain. If that push fails, the already-stored Resume Intelligence
    candidate is NOT deleted or rolled back -- this returns 502 so the caller
    knows ingestion is incomplete; retrying the same upload is safe (it dedups
    to the same candidate_id in Resume Intelligence, and Second Brain upserts
    by candidate_id).
    """
    suffix = Path(file.filename or "resume").suffix.lower() or ".bin"
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported file type '{suffix}'. Allowed: {sorted(_ALLOWED_SUFFIXES)}",
        )

    # Save upload to a temp file so the pipeline can read it by path
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        # Imported here (rather than at module level) so a missing optional
        # extraction dependency in a test environment can't break app startup.
        from pipeline.text_extractor import extract_text
        from pipeline.parsing_agent import parse_resume_text

        extraction = extract_text(tmp_path)
        parse_result = await parse_resume_text(
            llm, extraction.raw_text, extraction.file_meta, model=settings.gemini_model
        )

        if not parse_result.success:
            # Every bounded retry attempt failed -- do not store a placeholder
            # "Unknown" candidate. The detail is deliberately generic: a validation
            # failure's underlying exception message can embed extracted PII.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Structured extraction failed",
            )

        candidate_json = parse_result.data
        candidate_id, is_new = store_candidate(db, candidate_json, extraction.raw_text)

        # The candidate is now durably committed in Resume Intelligence (see
        # db.store_candidate). Everything from here on is a separate, later step
        # whose failure must never undo that.
        from pipeline.candidate_summary import build_candidate_summary
        from second_brain_client import SecondBrainClientError, send_candidate_summary

        try:
            summary = build_candidate_summary(candidate_id, candidate_json)
            await send_candidate_summary(
                settings.second_brain_url, settings.second_brain_api_key, summary
            )
        except SecondBrainClientError as exc:
            # Only the exception type and the (non-PII) candidate_id are logged.
            logger.error(
                "second_brain_ingestion_failed",
                extra={"error_type": type(exc).__name__, "candidate_id": str(candidate_id)},
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"Resume stored (candidate_id={candidate_id}) but Second Brain "
                    "ingestion failed. Retrying this upload is safe."
                ),
            )

    except HTTPException:
        raise
    except Exception as exc:
        # Only the exception type is logged -- the pipeline's exception messages can
        # embed extracted candidate data (name/email/phone), so the message itself
        # must never be logged, only reported generically to the caller below.
        logger.error("resume_ingest_failed", extra={"error_type": type(exc).__name__})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process resume",
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return {"candidate_id": str(candidate_id), "is_new": is_new}


@app.get(
    "/v1/candidates/{candidate_id}/contact",
    response_model=ContactLookupResponse,
)
def get_contact(
    candidate_id: uuid.UUID,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_service_key),
):
    """
    Return PII contact data for a candidate.  Requires X-Api-Key service key.
    This endpoint must never be called without a valid key — callers (RWS) must
    not persist the response; they use it transiently to call VoiceHR.

    candidate_id is typed as uuid.UUID (not str) so a malformed id is rejected
    with a clean 422 by FastAPI's own path-parameter validation, before ever
    reaching the database -- a bare string type would instead reach Postgres as
    an invalid uuid literal and surface as an unhandled 500.
    """
    candidate = (
        db.query(Candidate).filter(Candidate.id == candidate_id).first()
    )
    if not candidate:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")

    return ContactLookupResponse(
        candidate_id=candidate.id,
        name=candidate.name,
        email=candidate.email,
        phone=candidate.phone,
    )
