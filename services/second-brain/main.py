"""
Second Brain — FastAPI service.

Stores PII-free candidate knowledge (skills, titles, embeddings) and exposes
semantic retrieval for RWS. All data is keyed by opaque candidate_id UUID;
this service must never receive, store, or log name/email/phone/address.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.orm import Session

from db import EMBEDDING_DIMENSIONS, get_db, search_candidates, upsert_candidate_knowledge
from shared.schemas import (
    CandidateMatch,
    CandidateSummary,
    RetrieveCandidatesRequest,
    RetrieveCandidatesResponse,
)
from shared.llm import LLMProvider, LLMProviderError

logging.basicConfig(level="INFO")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_api_key: str = "changeme-second-brain-key"
    gemini_api_key: str = ""
    gemini_embedding_model: str = "gemini-embedding-001"
    log_level: str = "INFO"


settings = Settings()

app = FastAPI(title="Second Brain")


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def require_service_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if x_api_key != settings.service_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing service API key")


_llm_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    """Lazy singleton — only instantiated on first embedding request."""
    global _llm_provider
    if _llm_provider is None:
        from shared.llm import GeminiProvider
        try:
            _llm_provider = GeminiProvider(api_key=settings.gemini_api_key or None)
        except LLMProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
    return _llm_provider


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "service": "second-brain"}


@app.post("/v1/knowledge/candidates", status_code=200)
async def ingest_candidate_knowledge(
    payload: CandidateSummary,
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm_provider),
    _auth: None = Depends(require_service_key),
):
    """
    Store or update PII-free candidate knowledge.

    CandidateSummary has extra="forbid", so any attempt to pass PII-shaped
    fields (name, email, phone) is rejected at validation time with 422.
    """
    try:
        embedding = await llm.embed_text(
            payload.raw_text,
            model=settings.gemini_embedding_model,
            dimensions=EMBEDDING_DIMENSIONS,
        )
    except LLMProviderError as exc:
        raise HTTPException(status_code=502, detail=f"Embedding failed: {exc}")

    upsert_candidate_knowledge(
        db,
        candidate_id=payload.candidate_id,
        skills=payload.skills,
        years_of_experience=payload.years_of_experience,
        job_titles=payload.job_titles,
        raw_text=payload.raw_text,
        source_version=payload.source_version,
        embedding=embedding,
    )
    return {"candidate_id": str(payload.candidate_id), "status": "ok"}


@app.post("/v1/retrieve/candidates", response_model=RetrieveCandidatesResponse)
async def retrieve_candidates(
    payload: RetrieveCandidatesRequest,
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm_provider),
    _auth: None = Depends(require_service_key),
):
    """
    Semantic recall: embed query_text and return top-k candidates by cosine similarity.

    Responsibility is recall only — no qualification or scoring. Caller (RWS) applies
    the hybrid scoring and threshold logic on top of these results.
    RetrieveCandidatesRequest has extra="forbid", so PII fields are rejected at 422.
    """
    try:
        query_embedding = await llm.embed_text(
            payload.query_text,
            model=settings.gemini_embedding_model,
            dimensions=EMBEDDING_DIMENSIONS,
        )
    except LLMProviderError as exc:
        raise HTTPException(status_code=502, detail=f"Embedding failed: {exc}")

    matches = search_candidates(
        db,
        query_embedding=query_embedding,
        top_k=payload.top_k,
        candidate_ids=payload.candidate_ids,
    )

    return RetrieveCandidatesResponse(
        matches=[
            CandidateMatch(candidate_id=cid, similarity_score=score)
            for cid, score in matches
        ]
    )
