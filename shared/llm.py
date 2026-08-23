"""
Thin LLM provider abstraction. GeminiProvider is the only implementation.

The interface exists so that swapping providers is a config change, not a rewrite.
API key comes exclusively from the GEMINI_API_KEY environment variable.
Prompt text is intentionally excluded from error messages to avoid PII leakage.
"""
from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMProviderError(Exception):
    """Raised when the LLM provider fails or returns an unexpected response."""


class LLMProvider(ABC):
    @abstractmethod
    async def generate_text(self, prompt: str, *, model: str | None = None) -> str:
        """Return a plain-text completion for prompt."""

    @abstractmethod
    async def generate_structured(
        self, prompt: str, schema: type[T], *, model: str | None = None
    ) -> T:
        """Return a validated Pydantic model instance from the model's JSON output."""


class GeminiProvider(LLMProvider):
    """
    Gemini implementation using google-genai 2.18.0 (same SDK version as VoiceHR).

    API key is read from GEMINI_API_KEY. Default model: gemini-2.5-flash.
    _client and _types are for unit testing only and are not part of the public API.
    """

    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str | None = None,
        _client=None,
        _types=None,
    ):
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise LLMProviderError(
                "GEMINI_API_KEY environment variable is not set. "
                "Copy .env.example to .env and fill it in."
            )
        self._default_model = default_model or self.DEFAULT_MODEL

        if _client is not None:
            self._client = _client
        else:
            try:
                from google import genai
            except ImportError as exc:
                raise LLMProviderError(
                    "google-genai is not installed. "
                    "Add google-genai==2.18.0 to this service's requirements.txt."
                ) from exc
            self._client = genai.Client(api_key=key)

        if _types is not None:
            self._types = _types
        else:
            try:
                from google.genai import types
                self._types = types
            except ImportError as exc:
                raise LLMProviderError(
                    "google-genai is not installed. "
                    "Add google-genai==2.18.0 to this service's requirements.txt."
                ) from exc

    async def generate_text(self, prompt: str, *, model: str | None = None) -> str:
        target = model or self._default_model
        try:
            response = await self._client.aio.models.generate_content(
                model=target,
                contents=prompt,
                config=self._types.GenerateContentConfig(temperature=0.2),
            )
            text = getattr(response, "text", None)
            if not text:
                raise LLMProviderError(f"Gemini ({target}) returned an empty response")
            return text
        except LLMProviderError:
            raise
        except Exception as exc:
            raise LLMProviderError(
                f"Gemini ({target}) generate_text failed: {type(exc).__name__}: {exc}"
            ) from exc

    async def generate_structured(
        self, prompt: str, schema: type[T], *, model: str | None = None
    ) -> T:
        target = model or self._default_model
        try:
            response = await self._client.aio.models.generate_content(
                model=target,
                contents=prompt,
                config=self._types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=schema.model_json_schema(),
                    temperature=0.2,
                ),
            )
            text = getattr(response, "text", None)
            if not text:
                raise LLMProviderError(f"Gemini ({target}) returned an empty response")
            raw = text.strip().removeprefix("```json").removesuffix("```").strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise LLMProviderError(
                    f"Gemini ({target}) returned non-JSON output"
                ) from exc
            try:
                return schema.model_validate(data)
            except Exception as exc:
                raise LLMProviderError(
                    f"Gemini ({target}) response did not match {schema.__name__}: {exc}"
                ) from exc
        except LLMProviderError:
            raise
        except Exception as exc:
            raise LLMProviderError(
                f"Gemini ({target}) generate_structured failed: {type(exc).__name__}: {exc}"
            ) from exc
