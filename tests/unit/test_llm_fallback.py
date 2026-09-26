"""
Unit tests for the local-LLM fallback (shared.llm).

These pin the *contract* of FallbackLLMProvider rather than any particular model:
which failures are allowed to reach the fallback, which must not, what a caller sees
when both providers fail, and the fact that embeddings never fall back at all. No
Ollama installation or network access is required -- both providers are stubs.
"""
import asyncio
import unittest

from pydantic import BaseModel


class _Sample(BaseModel):
    name: str


class _StubProvider:
    """Minimal LLMProvider stand-in that records calls and can be told to fail."""

    def __init__(self, *, error: Exception | None = None, label: str = "stub"):
        self.error = error
        self.label = label
        self.calls: list[str] = []

    async def generate_text(self, prompt, *, model=None):
        self.calls.append("generate_text")
        if self.error:
            raise self.error
        return f"{self.label}-text"

    async def generate_structured(self, prompt, schema, *, model=None):
        self.calls.append("generate_structured")
        if self.error:
            raise self.error
        return schema(name=self.label)

    async def embed_text(self, text, *, model=None, dimensions=768):
        self.calls.append("embed_text")
        if self.error:
            raise self.error
        return [0.5] * dimensions


def _unavailable(message="429 quota exceeded"):
    from shared.llm import LLMProviderUnavailableError

    return LLMProviderUnavailableError(message)


class TestErrorClassification(unittest.TestCase):
    """is_provider_unavailable decides what may reach the fallback at all."""

    def test_availability_failures_are_eligible(self):
        from shared.llm import is_provider_unavailable

        class _Status(Exception):
            def __init__(self, code):
                super().__init__(f"http {code}")
                self.status_code = code

        for exc in (_Status(429), _Status(503), _Status(500), _Status(504),
                    TimeoutError("deadline exceeded"),
                    RuntimeError("RESOURCE_EXHAUSTED: quota"),
                    RuntimeError("503 Service Unavailable")):
            with self.subTest(exc=exc):
                self.assertTrue(is_provider_unavailable(exc))

    def test_application_bugs_are_not_eligible(self):
        """A bug must surface, not be laundered through a second provider."""
        from shared.llm import is_provider_unavailable

        for exc in (ValueError("schema does not match"),
                    KeyError("basics"),
                    TypeError("str expected"),
                    AttributeError("no attribute 'text'")):
            with self.subTest(exc=exc):
                self.assertFalse(is_provider_unavailable(exc))

    def test_client_errors_that_are_not_capacity_are_not_eligible(self):
        """400/401/403/404 mean the request or key is wrong -- retrying elsewhere
        would just produce the same bad request against a second provider."""
        from shared.llm import is_provider_unavailable

        class _Status(Exception):
            def __init__(self, code):
                super().__init__(f"http {code}")
                self.status_code = code

        for code in (400, 401, 403, 404):
            with self.subTest(code=code):
                self.assertFalse(is_provider_unavailable(_Status(code)))


class TestFallbackContract(unittest.TestCase):
    def _pair(self, primary_error=None, fallback_error=None):
        from shared.llm import FallbackLLMProvider

        primary = _StubProvider(error=primary_error, label="gemini")
        fallback = _StubProvider(error=fallback_error, label="local")
        return FallbackLLMProvider(primary, fallback), primary, fallback

    def test_primary_success_never_touches_fallback(self):
        provider, primary, fallback = self._pair()
        self.assertEqual(asyncio.run(provider.generate_text("p")), "gemini-text")
        self.assertEqual(primary.calls, ["generate_text"])
        self.assertEqual(fallback.calls, [], "fallback must not be called when primary works")

    def test_unavailable_primary_falls_back(self):
        provider, primary, fallback = self._pair(primary_error=_unavailable())
        self.assertEqual(asyncio.run(provider.generate_text("p")), "local-text")
        self.assertEqual(fallback.calls, ["generate_text"])

    def test_unavailable_primary_falls_back_for_structured_output(self):
        provider, _, fallback = self._pair(primary_error=_unavailable())
        result = asyncio.run(provider.generate_structured("p", _Sample))
        self.assertEqual(result.name, "local")
        self.assertEqual(fallback.calls, ["generate_structured"])

    def test_non_availability_error_is_reraised_without_fallback(self):
        """The explicit requirement: arbitrary application bugs are not silently
        rerouted to the fallback."""
        boom = ValueError("genuine bug in prompt assembly")
        provider, _, fallback = self._pair(primary_error=boom)
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(provider.generate_text("p"))
        self.assertIs(ctx.exception, boom)
        self.assertEqual(fallback.calls, [], "a bug must not reach the fallback")

    def test_both_failing_raises_the_primary_error(self):
        """Callers already handle Gemini's failure; a total outage must keep looking
        exactly like it did before a fallback existed."""
        primary_exc = _unavailable("503 upstream down")
        provider, _, fallback = self._pair(
            primary_error=primary_exc, fallback_error=RuntimeError("ollama not running")
        )
        with self.assertRaises(Exception) as ctx:
            asyncio.run(provider.generate_text("p"))
        self.assertIs(ctx.exception, primary_exc)
        self.assertEqual(fallback.calls, ["generate_text"], "fallback should have been tried")

    def test_no_fallback_configured_reraises(self):
        from shared.llm import FallbackLLMProvider, LLMProviderUnavailableError

        primary = _StubProvider(error=_unavailable())
        provider = FallbackLLMProvider(primary, None)
        self.assertFalse(provider.fallback_enabled)
        with self.assertRaises(LLMProviderUnavailableError):
            asyncio.run(provider.generate_text("p"))

    def test_unavailable_error_is_still_an_llm_provider_error(self):
        """Subclassing matters: existing `except LLMProviderError` handlers in the
        services must keep catching these unchanged."""
        from shared.llm import LLMProviderError, LLMProviderUnavailableError

        self.assertTrue(issubclass(LLMProviderUnavailableError, LLMProviderError))


class TestEmbeddingsNeverFallBack(unittest.TestCase):
    """The correctness boundary: a local vector in a Gemini-built pgvector space
    would still rank candidates, plausibly and meaninglessly."""

    def test_embed_text_does_not_fall_back_even_when_primary_is_unavailable(self):
        from shared.llm import FallbackLLMProvider, LLMProviderUnavailableError

        primary = _StubProvider(error=_unavailable("429 quota"))
        fallback = _StubProvider(label="local")
        provider = FallbackLLMProvider(primary, fallback)

        with self.assertRaises(LLMProviderUnavailableError):
            asyncio.run(provider.embed_text("some candidate summary"))
        self.assertEqual(fallback.calls, [], "embeddings must never use the local model")

    def test_embed_text_still_works_through_the_primary(self):
        from shared.llm import FallbackLLMProvider

        primary = _StubProvider(label="gemini")
        provider = FallbackLLMProvider(primary, _StubProvider(label="local"))
        vector = asyncio.run(provider.embed_text("x", dimensions=768))
        self.assertEqual(len(vector), 768)
        self.assertEqual(primary.calls, ["embed_text"])


class TestOllamaSchemaCompatibility(unittest.TestCase):
    """Both rewrites were forced by a live Ollama rejecting this project's schemas."""

    def test_refs_are_inlined(self):
        from shared.llm import ollama_compatible_schema

        class Inner(BaseModel):
            x: str

        class Outer(BaseModel):
            inner: Inner

        out = ollama_compatible_schema(Outer.model_json_schema())
        self.assertNotIn("$defs", out)
        self.assertNotIn("$ref", str(out))
        self.assertEqual(out["properties"]["inner"]["properties"]["x"]["type"], "string")

    def test_oversized_maxlength_is_dropped_but_small_ones_kept(self):
        from shared.llm import ollama_compatible_schema

        schema = {
            "type": "object",
            "properties": {
                "big": {"type": "string", "maxLength": 4000},
                "small": {"type": "string", "maxLength": 100},
            },
        }
        out = ollama_compatible_schema(schema)
        self.assertNotIn("maxLength", out["properties"]["big"])
        self.assertEqual(out["properties"]["small"]["maxLength"], 100)

    def test_all_keys_are_promoted_to_required(self):
        """Without this, an all-defaults Pydantic model yields a schema with no
        `required` at all and a small model simply omits whole objects."""
        from shared.llm import ollama_compatible_schema

        class Basics(BaseModel):
            email: str | None = None

        class Doc(BaseModel):
            basics: Basics = Basics()
            skills: list[str] = []

        raw = Doc.model_json_schema()
        self.assertIsNone(raw.get("required"), "precondition: Pydantic emits no required")

        out = ollama_compatible_schema(raw)
        self.assertEqual(out["required"], ["basics", "skills"])
        self.assertIn("email", out["properties"]["basics"]["required"])

    def test_promotion_can_be_disabled(self):
        from shared.llm import ollama_compatible_schema

        class Doc(BaseModel):
            a: str | None = None

        out = ollama_compatible_schema(Doc.model_json_schema(), require_all_keys=False)
        self.assertIsNone(out.get("required"))


class TestThinkingModelHandling(unittest.TestCase):
    """Reasoning models return the answer in `thinking` and leave `response` empty."""

    def test_response_is_preferred(self):
        from shared.llm import _ollama_text

        self.assertEqual(_ollama_text({"response": "A", "thinking": "B"}), "A")

    def test_thinking_is_used_when_response_is_empty(self):
        from shared.llm import _ollama_text

        self.assertEqual(_ollama_text({"response": "  ", "thinking": "B"}), "B")

    def test_both_missing_yields_empty_string(self):
        from shared.llm import _ollama_text

        self.assertEqual(_ollama_text({}), "")


class TestWiringHelper(unittest.TestCase):
    def test_disabled_returns_primary_untouched(self):
        from shared.llm import wrap_with_local_fallback

        primary = _StubProvider()
        self.assertIs(
            wrap_with_local_fallback(primary, enabled=False, base_url=None, text_model="m"),
            primary,
        )

    def test_enabled_without_model_returns_primary_untouched(self):
        from shared.llm import wrap_with_local_fallback

        primary = _StubProvider()
        self.assertIs(
            wrap_with_local_fallback(primary, enabled=True, base_url=None, text_model=""),
            primary,
        )

    def test_enabled_wraps(self):
        from shared.llm import FallbackLLMProvider, wrap_with_local_fallback

        wrapped = wrap_with_local_fallback(
            _StubProvider(), enabled=True, base_url=None, text_model="qwen2.5:3b"
        )
        self.assertIsInstance(wrapped, FallbackLLMProvider)
        self.assertTrue(wrapped.fallback_enabled)


if __name__ == "__main__":
    unittest.main()
