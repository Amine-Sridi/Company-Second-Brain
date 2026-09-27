"""
services/resume-intelligence/llm_provider.py
---------------------------------------------
One place that builds this service's LLM provider.

Extracted from `api.py`'s `get_llm_provider` when ingestion moved onto a queue: the ARQ
worker runs outside FastAPI and cannot use `Depends`, but it must use **exactly** the
same provider configuration as the HTTP path -- same Gemini model, same local fallback,
same measured winner (`granite4.1:3b`). Two constructions would mean two behaviours, and
the one that ran unattended in the worker would be the one nobody noticed drifting.

`api.py` keeps its own `get_llm_provider` dependency (so tests can still override it and
so a construction failure still becomes a clean 503); it now delegates here.
"""
from __future__ import annotations

from shared.llm import LLMProvider

_provider: LLMProvider | None = None


def build_llm_provider(settings) -> LLMProvider:
    """Lazy singleton, per process. Raises LLMProviderError if unconfigured."""
    global _provider
    if _provider is None:
        from shared.llm import GeminiProvider, wrap_with_local_fallback

        _provider = wrap_with_local_fallback(
            GeminiProvider(api_key=settings.gemini_api_key or None),
            enabled=settings.local_llm_enabled,
            base_url=settings.local_llm_base_url,
            text_model=settings.local_llm_text_model,
            structured_model=settings.local_llm_structured_model,
        )
    return _provider


def reset_llm_provider() -> None:
    """Test hook: drop the cached provider so a fresh settings object takes effect."""
    global _provider
    _provider = None
