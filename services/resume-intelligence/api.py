"""
FastAPI application for Resume Intelligence.

Endpoints:
  GET  /health
  POST /v1/resumes                          — ingest a resume file, returns candidate_id
  GET  /v1/candidates/{candidate_id}/contact — service-key auth, returns PII contact data
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.orm import Session

from db import Candidate, get_db, store_candidate

# shared/ is available at /app/shared/ in Docker (build context = project root).
# In local dev / tests, the project root must be on sys.path (see tests/integration/conftest.py).
from shared.schemas import ContactLookupResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_api_key: str = "changeme-resume-intelligence-key"
    log_level: str = "INFO"


settings = Settings()
logging.basicConfig(level=settings.log_level)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Resume Intelligence")

_ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png"}


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
):
    """
    Accept a resume file, run the extraction + parsing pipeline, store the candidate
    with normalized-email dedup, and return the candidate_id.

    Idempotent: submitting the same candidate email again returns the existing id
    (is_new=false) without modifying any data.
    """
    suffix = Path(file.filename or "resume").suffix.lower() or ".bin"
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file type '{suffix}'. Allowed: {sorted(_ALLOWED_SUFFIXES)}",
        )

    # Save upload to a temp file so the pipeline can read it by path
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        # Import here so the module-level Ollama/LangGraph imports don't execute
        # at startup (they may fail if optional deps are absent in test environments).
        from pipeline.text_extractor import extract_text
        from pipeline.parsing_agent import parse_resume_text

        extraction = extract_text(tmp_path)
        parse_result = parse_resume_text(extraction.raw_text, extraction.file_meta)
        candidate_json = parse_result.data

        candidate_id, is_new = store_candidate(db, candidate_json, extraction.raw_text)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Resume ingest failed: %s", exc)
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
    candidate_id: str,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_service_key),
):
    """
    Return PII contact data for a candidate.  Requires X-Api-Key service key.
    This endpoint must never be called without a valid key — callers (RWS) must
    not persist the response; they use it transiently to call VoiceHR.
    """
    candidate = (
        db.query(Candidate).filter(Candidate.id == candidate_id).first()
    )
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    return ContactLookupResponse(
        candidate_id=candidate.id,
        name=candidate.name,
        email=candidate.email,
        phone=candidate.phone,
    )
