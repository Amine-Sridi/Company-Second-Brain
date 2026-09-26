"""
Thin LLM provider abstraction. GeminiProvider is the only implementation.

The interface exists so that swapping providers is a config change, not a rewrite.
API key comes exclusively from the GEMINI_API_KEY environment variable.
Prompt text is intentionally excluded from error messages and from logging to avoid
PII leakage -- every call is logged (model, operation, duration, outcome) via
shared.observability.log_external_call, never the prompt or the response content.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

from shared.observability import Timer, log_external_call

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMProviderError(Exception):
    """Raised when the LLM provider fails or returns an unexpected response."""


class LLMProviderUnavailableError(LLMProviderError):
    """
    The provider could not be reached or refused to serve: quota/429, a 5xx, a
    timeout, or a transport failure. This is the ONLY condition that makes a request
    eligible for the local fallback (see FallbackLLMProvider).

    Deliberately a subclass of LLMProviderError so every existing
    `except LLMProviderError` handler in the services keeps behaving exactly as it
    did -- this adds a distinction for new code without changing old code's control
    flow.

    What is deliberately NOT this error: an empty response, output that failed schema
    validation, or a malformed JSON body. Those mean the provider answered and the
    answer was unusable -- a quality problem, not an availability one. Sending them to
    a *smaller local model* would be the wrong reflex, and blanket-catching them would
    quietly turn our own bugs into fallback traffic.
    """


# Substrings that identify a genuine availability failure in a provider exception.
# Matched case-insensitively against the exception text as a fallback for when the
# SDK's own typed errors aren't recognisable.
_UNAVAILABLE_MARKERS = (
    "429", "quota", "rate limit", "resource_exhausted", "resource exhausted",
    "500", "502", "503", "504", "internal error", "unavailable", "overloaded",
    "timeout", "timed out", "deadline", "connection", "connect", "network",
    "temporarily",
)

_UNAVAILABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


def is_provider_unavailable(exc: BaseException) -> bool:
    """
    Decide whether `exc` is an availability failure (eligible for fallback) rather
    than a bad answer or a bug in our own code.

    Checked in order of trustworthiness: the SDK's own status code, then the
    exception's type name, then its message. The message check is last because it is
    the weakest signal -- a schema-validation error mentioning the number 500 should
    not be mistaken for an HTTP 500.
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int) and status in _UNAVAILABLE_STATUS_CODES:
        return True

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    try:
        import asyncio

        if isinstance(exc, asyncio.TimeoutError):
            return True
    except Exception:  # pragma: no cover - asyncio is always importable in practice
        pass

    type_name = type(exc).__name__.lower()
    if any(m in type_name for m in (
        "servererror", "serviceunavailable", "deadlineexceeded", "resourceexhausted",
        "toomanyrequests", "timeout", "connect",
    )):
        return True

    message = str(exc).lower()
    return any(marker in message for marker in _UNAVAILABLE_MARKERS)


def _provider_error(target: str, operation: str, exc: Exception) -> LLMProviderError:
    """Wraps a raw provider exception in the right class for fallback eligibility."""
    detail = f"Gemini ({target}) {operation} failed: {type(exc).__name__}: {exc}"
    if is_provider_unavailable(exc):
        return LLMProviderUnavailableError(detail)
    return LLMProviderError(detail)


class LLMProvider(ABC):
    @abstractmethod
    async def generate_text(self, prompt: str, *, model: str | None = None) -> str:
        """Return a plain-text completion for prompt."""

    @abstractmethod
    async def generate_structured(
        self, prompt: str, schema: type[T], *, model: str | None = None
    ) -> T:
        """Return a validated Pydantic model instance from the model's JSON output."""

    @abstractmethod
    async def embed_text(
        self, text: str, *, model: str | None = None, dimensions: int = 768
    ) -> list[float]:
        """Return an embedding vector for text."""


class GeminiProvider(LLMProvider):
    """
    Gemini implementation using google-genai 2.18.0 (same SDK version as VoiceHR).

    API key is read from GEMINI_API_KEY. Default model: DEFAULT_MODEL below
    (callers normally pass their own `model=` from settings anyway).
    _client and _types are for unit testing only and are not part of the public API.
    """

    DEFAULT_MODEL = "gemini-3.6-flash"

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
        with Timer() as timer:
            try:
                response = await self._client.aio.models.generate_content(
                    model=target,
                    contents=prompt,
                    config=self._types.GenerateContentConfig(temperature=0.2),
                )
                text = getattr(response, "text", None)
                if not text:
                    raise LLMProviderError(f"Gemini ({target}) returned an empty response")
            except Exception as exc:
                log_external_call(
                    logger, target="gemini", operation=f"generate_text:{target}",
                    duration_ms=timer.duration_ms, outcome="error", error_type=type(exc).__name__,
                )
                if isinstance(exc, LLMProviderError):
                    raise
                raise _provider_error(target, "generate_text", exc) from exc
        log_external_call(
            logger, target="gemini", operation=f"generate_text:{target}",
            duration_ms=timer.duration_ms, outcome="success",
        )
        return text

    async def generate_structured(
        self, prompt: str, schema: type[T], *, model: str | None = None
    ) -> T:
        target = model or self._default_model
        with Timer() as timer:
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
                    result = schema.model_validate(data)
                except Exception as exc:
                    raise LLMProviderError(
                        f"Gemini ({target}) response did not match {schema.__name__}: {exc}"
                    ) from exc
            except Exception as exc:
                log_external_call(
                    logger, target="gemini", operation=f"generate_structured:{target}",
                    duration_ms=timer.duration_ms, outcome="error", error_type=type(exc).__name__,
                )
                if isinstance(exc, LLMProviderError):
                    raise
                raise _provider_error(target, "generate_structured", exc) from exc
        log_external_call(
            logger, target="gemini", operation=f"generate_structured:{target}",
            duration_ms=timer.duration_ms, outcome="success",
        )
        return result

    async def embed_text(
        self, text: str, *, model: str | None = None, dimensions: int = 768
    ) -> list[float]:
        target = model or "gemini-embedding-001"
        with Timer() as timer:
            try:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: self._client.models.embed_content(
                        model=target,
                        contents=text,
                        config=self._types.EmbedContentConfig(output_dimensionality=dimensions),
                    ),
                )
                values = list(result.embeddings[0].values)
            except Exception as exc:
                log_external_call(
                    logger, target="gemini", operation=f"embed_text:{target}",
                    duration_ms=timer.duration_ms, outcome="error", error_type=type(exc).__name__,
                )
                if isinstance(exc, LLMProviderError):
                    raise
                raise _provider_error(target, "embed_text", exc) from exc
        log_external_call(
            logger, target="gemini", operation=f"embed_text:{target}",
            duration_ms=timer.duration_ms, outcome="success",
        )
        return values


# ---------------------------------------------------------------------------
# JSON Schema -> Ollama grammar compatibility
# ---------------------------------------------------------------------------

# Ollama compiles the `format` schema into a sampling grammar, and that compiler is
# stricter than the JSON Schema Gemini accepts. Two incompatibilities were found by
# bisecting this project's own schemas against a live Ollama 0.32.1 (see
# LOCAL_LLM_BENCHMARK_AND_FALLBACK.md):
#
#   1. `$ref`/`$defs` are rejected outright ("failed to parse grammar"). Every Pydantic
#      model built from nested models emits these -- CandidateExtraction does.
#   2. A large `maxLength` is rejected. Measured threshold on this build: <=1000 compiles,
#      >=2000 fails. CandidateExtraction's work[].description carries maxLength 4000.
#
# Both are fixed by rewriting the schema rather than weakening the contract: refs are
# inlined, and oversized length bounds are dropped. Dropping `maxLength` costs nothing
# in correctness because the caller validates the result against the real Pydantic
# model immediately afterwards -- the grammar constrains *shape*, Pydantic still
# enforces *bounds*. A too-long string is then a clean validation error, exactly as it
# would be from Gemini.
_OLLAMA_MAX_SAFE_MAXLENGTH = 1000


def ollama_compatible_schema(schema: dict, *, require_all_keys: bool = True) -> dict:
    """
    Rewrites a Pydantic JSON Schema into the subset Ollama's grammar compiler accepts.

    require_all_keys additionally marks every property of every object as `required`.
    That is not cosmetic -- it fixes a measured, silent data-loss bug. These models
    give every field a default (CandidateExtraction.basics has default_factory), so
    Pydantic emits a schema with **no `required` list at all**. Ollama's grammar then
    permits omitting any key, and a small model takes the cheapest path: qwen2.5:3b
    dropped the entire `basics` object -- every candidate name, email and phone --
    while still producing output that validated cleanly, because an absent `basics`
    is exactly what `default_factory` fills in. Validity was True; the extraction was
    useless. Requiring the keys raised resume-parsing quality 0.60 -> 0.85 and
    recovered both email and phone (see LOCAL_LLM_BENCHMARK_AND_FALLBACK.md).

    It is safe by construction rather than by luck: every field in these models is
    Optional, so requiring the *key* still allows the *value* to be null. The grammar
    only forces the model to address each field instead of skipping it -- it cannot
    force it to invent one. Checked against the adversarial `jd_sparse_vague` case
    (a deliberately vague JD where the correct answer is an empty extraction), which
    scored identically with and without promotion, so this does not buy completeness
    at the cost of fabrication.
    """
    import copy

    defs = schema.get("$defs", {})

    def walk(node, depth=0):
        if depth > 60:  # cyclic model; leave the rest of the branch untouched
            return node
        if isinstance(node, list):
            return [walk(n, depth + 1) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            ref = node["$ref"]
            if ref.startswith("#/$defs/"):
                target = defs.get(ref.rsplit("/", 1)[-1], {})
                overrides = {k: v for k, v in node.items() if k != "$ref"}
                return walk({**copy.deepcopy(target), **overrides}, depth + 1)
        out = {}
        for key, value in node.items():
            if key == "$defs":
                continue
            if key == "maxLength" and isinstance(value, int) and value > _OLLAMA_MAX_SAFE_MAXLENGTH:
                continue
            out[key] = walk(value, depth + 1)
        return out

    rewritten = walk(copy.deepcopy(schema))
    rewritten.pop("$defs", None)

    if require_all_keys:
        def promote(node):
            if isinstance(node, dict):
                if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                    node["required"] = sorted(node["properties"].keys())
                for value in node.values():
                    promote(value)
            elif isinstance(node, list):
                for value in node:
                    promote(value)

        promote(rewritten)

    return rewritten


def _ollama_text(body: dict) -> str:
    """
    Reads the generated text out of an Ollama response.

    Reasoning models (qwen3.5, for one) put their answer in a separate `thinking`
    field and leave `response` empty, so a provider that reads only `response` sees
    a correct answer as an empty one. Requests set `think: False` to suppress that --
    these are extraction tasks with nothing to reason about -- and this reads
    `thinking` as a fallback in case a model honours the flag only partially.
    """
    text = (body.get("response") or "").strip()
    if not text:
        text = (body.get("thinking") or "").strip()
    return text


class OllamaProvider(LLMProvider):
    """
    Local open-weight fallback, served by Ollama over HTTP.

    Chosen as the runtime because it was already installed on the target machine and
    needs no new service in docker-compose: it runs on the host, and containers reach
    it through the host gateway. That satisfies CLAUDE.md's "what concrete problem
    does this solve" test without adding infrastructure.

    generate_structured passes the Pydantic model's JSON Schema to Ollama's `format`,
    which constrains decoding to that grammar -- the same guarantee GeminiProvider
    gets from response_json_schema, rather than asking a small model to "please
    return JSON" and hoping. Validation still runs afterwards: a grammar can enforce
    shape but not that the values are sane.

    Deliberately no retry loop here. This provider is only ever reached because the
    primary already failed; retrying a local model that is down would just add
    latency to a request that is about to fail anyway.
    """

    DEFAULT_BASE_URL = "http://localhost:11434"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        text_model: str,
        structured_model: str | None = None,
        embedding_model: str | None = None,
        timeout_seconds: float = 120.0,
    ):
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._text_model = text_model
        self._structured_model = structured_model or text_model
        self._embedding_model = embedding_model
        self._timeout = timeout_seconds

    async def _post(self, path: str, payload: dict, operation: str) -> dict:
        import httpx

        target = payload.get("model", "?")
        with Timer() as timer:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(f"{self._base_url}{path}", json=payload)
                response.raise_for_status()
                body = response.json()
            except Exception as exc:
                log_external_call(
                    logger, target="ollama", operation=f"{operation}:{target}",
                    duration_ms=timer.duration_ms, outcome="error",
                    error_type=type(exc).__name__,
                )
                # Every failure of a *local* provider is an availability failure: it
                # is either running or it is not.
                raise LLMProviderUnavailableError(
                    f"Ollama ({target}) {operation} failed: {type(exc).__name__}"
                ) from exc
        log_external_call(
            logger, target="ollama", operation=f"{operation}:{target}",
            duration_ms=timer.duration_ms, outcome="success",
        )
        return body

    async def generate_text(self, prompt: str, *, model: str | None = None) -> str:
        body = await self._post(
            "/api/generate",
            {
                "model": model or self._text_model,
                "prompt": prompt,
                "stream": False,
                "think": False,  # see _ollama_text
                "options": {"temperature": 0.2},
            },
            "generate_text",
        )
        text = _ollama_text(body)
        if not text:
            raise LLMProviderError("Ollama returned an empty response")
        return text

    async def generate_structured(
        self, prompt: str, schema: type[T], *, model: str | None = None
    ) -> T:
        target = model or self._structured_model
        body = await self._post(
            "/api/generate",
            {
                "model": target,
                "prompt": prompt,
                "stream": False,
                "think": False,  # see _ollama_text
                "format": ollama_compatible_schema(schema.model_json_schema()),
                "options": {"temperature": 0.2},
            },
            "generate_structured",
        )
        text = _ollama_text(body)
        if not text:
            raise LLMProviderError(f"Ollama ({target}) returned an empty response")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(f"Ollama ({target}) returned non-JSON output") from exc
        try:
            return schema.model_validate(data)
        except Exception as exc:
            raise LLMProviderError(
                f"Ollama ({target}) response did not match {schema.__name__}: {exc}"
            ) from exc

    async def embed_text(
        self, text: str, *, model: str | None = None, dimensions: int = 768
    ) -> list[float]:
        """
        Implemented for completeness and for the re-embedding tool a future migration
        would need -- but NOT wired into the fallback path. See
        FallbackLLMProvider.embed_text for why mixing embedding models is unsafe.
        """
        target = model or self._embedding_model
        if not target:
            raise LLMProviderError("No local embedding model configured")
        body = await self._post(
            "/api/embed", {"model": target, "input": text}, "embed_text"
        )
        vectors = body.get("embeddings") or []
        if not vectors or not vectors[0]:
            raise LLMProviderError(f"Ollama ({target}) returned no embedding")
        return list(vectors[0])


class FallbackLLMProvider(LLMProvider):
    """
    Primary provider (Gemini) with a local fallback for availability failures only.

    Contract:
        primary succeeds                      -> primary's result
        primary raises LLMProviderUnavailable -> try fallback
          fallback succeeds                   -> fallback's result
          fallback fails                      -> raise the ORIGINAL primary error
        primary raises any other error        -> re-raise immediately, no fallback

    Re-raising the *primary's* error when both fail is deliberate: to every caller
    already written against this interface, a total outage then looks exactly like it
    did before a fallback existed, so their own error handling (submit_job's 502, the
    resume pipeline's bounded retry, the advisory-explanation path's None) keeps its
    current meaning. The fallback can only ever turn a failure into a success; it can
    never change what a failure looks like.
    """

    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None):
        self._primary = primary
        self._fallback = fallback

    @property
    def fallback_enabled(self) -> bool:
        return self._fallback is not None

    async def _with_fallback(self, operation: str, call):
        try:
            return await call(self._primary)
        except LLMProviderUnavailableError as primary_exc:
            if self._fallback is None:
                raise
            logger.warning(
                "llm_primary_unavailable_using_fallback",
                extra={"operation": operation, "error_type": type(primary_exc).__name__},
            )
            try:
                result = await call(self._fallback)
            except Exception:
                logger.error("llm_fallback_also_failed", extra={"operation": operation})
                raise primary_exc from None
            logger.info("llm_fallback_succeeded", extra={"operation": operation})
            return result

    async def generate_text(self, prompt: str, *, model: str | None = None) -> str:
        # `model` is the *primary's* model name and is meaningless to the fallback,
        # which is configured with its own; only forward it to the primary.
        async def call(provider: LLMProvider):
            if provider is self._primary:
                return await provider.generate_text(prompt, model=model)
            return await provider.generate_text(prompt)

        return await self._with_fallback("generate_text", call)

    async def generate_structured(
        self, prompt: str, schema: type[T], *, model: str | None = None
    ) -> T:
        async def call(provider: LLMProvider):
            if provider is self._primary:
                return await provider.generate_structured(prompt, schema, model=model)
            return await provider.generate_structured(prompt, schema)

        return await self._with_fallback("generate_structured", call)

    async def embed_text(
        self, text: str, *, model: str | None = None, dimensions: int = 768
    ) -> list[float]:
        """
        NO FALLBACK, on purpose -- this is a correctness boundary, not an oversight.

        Second Brain's pgvector column holds vectors produced by one specific Gemini
        embedding model, and similarity is only meaningful *within* one embedding
        space. A locally-produced vector would be a different space: cosine distance
        against the stored vectors would still return numbers, still rank candidates,
        and still look entirely plausible -- while being semantically meaningless.
        That is a silent corruption of retrieval, and retrieval feeds semantic_score,
        which is half of final_score by default.

        Falling back here would therefore trade a loud, correct failure (a 502 the
        recruiter sees) for quiet nonsense in an auditable MatchResult. A local
        embedding model is only usable after re-embedding every stored candidate into
        its space and versioning that space -- see LOCAL_LLM_BENCHMARK_AND_FALLBACK.md.
        """
        return await self._primary.embed_text(text, model=model, dimensions=dimensions)


def wrap_with_local_fallback(
    primary: LLMProvider,
    *,
    enabled: bool,
    base_url: str | None,
    text_model: str,
    structured_model: str | None = None,
) -> LLMProvider:
    """
    Returns `primary` unchanged unless a local fallback is explicitly enabled.

    Opt-in by default (LOCAL_LLM_ENABLED=false) so a deployment that never installed
    Ollama behaves exactly as it did before this existed -- the fallback is a
    resilience feature, not a new dependency. Both services that generate text or
    structured output call this; Second Brain deliberately does not, because it only
    ever calls embed_text (see FallbackLLMProvider.embed_text).
    """
    if not enabled:
        return primary
    if not text_model:
        logger.warning("local_llm_enabled_but_no_model_configured; fallback disabled")
        return primary
    return FallbackLLMProvider(
        primary,
        OllamaProvider(
            base_url,
            text_model=text_model,
            structured_model=structured_model or text_model,
        ),
    )
