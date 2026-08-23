"""
Unit tests for shared.llm (LLMProvider / GeminiProvider).

All tests use injected mock objects via the _client/_types test-support
parameters — no google-genai installation or network access required.
"""
import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import BaseModel


class _SampleSchema(BaseModel):
    name: str
    value: int


def _make_provider(response_text: str | None = "placeholder"):
    """Return a (GeminiProvider, mock_client) pair with injected mocks."""
    from shared.llm import GeminiProvider

    mock_response = MagicMock()
    mock_response.text = response_text

    mock_models = MagicMock()
    mock_models.generate_content = AsyncMock(return_value=mock_response)

    mock_aio = MagicMock()
    mock_aio.models = mock_models

    mock_client = MagicMock()
    mock_client.aio = mock_aio

    mock_types = MagicMock()
    mock_types.GenerateContentConfig = MagicMock(return_value=MagicMock())

    provider = GeminiProvider(
        api_key="test-key",
        _client=mock_client,
        _types=mock_types,
    )
    return provider, mock_client


class TestLLMProviderError(unittest.TestCase):
    def test_is_exception(self):
        from shared.llm import LLMProviderError
        e = LLMProviderError("boom")
        self.assertIsInstance(e, Exception)
        self.assertEqual(str(e), "boom")


class TestGeminiProviderInit(unittest.TestCase):
    def test_raises_without_api_key(self):
        from shared.llm import GeminiProvider, LLMProviderError
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(LLMProviderError) as ctx:
                GeminiProvider(api_key=None, _client=MagicMock(), _types=MagicMock())
        self.assertIn("GEMINI_API_KEY", str(ctx.exception))

    def test_env_var_provides_api_key(self):
        from shared.llm import GeminiProvider
        with patch.dict(os.environ, {"GEMINI_API_KEY": "env-key"}, clear=True):
            provider = GeminiProvider(_client=MagicMock(), _types=MagicMock())
        self.assertEqual(provider._default_model, GeminiProvider.DEFAULT_MODEL)

    def test_default_model_is_gemini_25_flash(self):
        from shared.llm import GeminiProvider
        provider, _ = _make_provider()
        self.assertEqual(provider._default_model, "gemini-2.5-flash")
        self.assertEqual(GeminiProvider.DEFAULT_MODEL, "gemini-2.5-flash")

    def test_custom_default_model(self):
        from shared.llm import GeminiProvider
        provider = GeminiProvider(
            api_key="k",
            default_model="gemini-1.5-pro",
            _client=MagicMock(),
            _types=MagicMock(),
        )
        self.assertEqual(provider._default_model, "gemini-1.5-pro")


class TestGenerateText(unittest.TestCase):
    def test_returns_text(self):
        provider, _ = _make_provider("Hello from Gemini")
        result = asyncio.run(provider.generate_text("Say hello"))
        self.assertEqual(result, "Hello from Gemini")

    def test_raises_on_empty_response(self):
        from shared.llm import LLMProviderError
        provider, _ = _make_provider(None)
        with self.assertRaises(LLMProviderError) as ctx:
            asyncio.run(provider.generate_text("Say hello"))
        self.assertIn("empty response", str(ctx.exception))

    def test_raises_on_empty_string_response(self):
        from shared.llm import LLMProviderError
        provider, _ = _make_provider("")
        with self.assertRaises(LLMProviderError):
            asyncio.run(provider.generate_text("Say hello"))

    def test_wraps_api_exception(self):
        from shared.llm import LLMProviderError
        provider, mock_client = _make_provider()
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=RuntimeError("503 Service Unavailable")
        )
        with self.assertRaises(LLMProviderError) as ctx:
            asyncio.run(provider.generate_text("test prompt"))
        self.assertIn("generate_text failed", str(ctx.exception))
        # Prompt text must not appear in the error message (PII guard)
        self.assertNotIn("test prompt", str(ctx.exception))

    def test_uses_custom_model(self):
        provider, mock_client = _make_provider("ok")
        asyncio.run(provider.generate_text("x", model="gemini-1.5-pro"))
        call_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gemini-1.5-pro")

    def test_uses_default_model_when_none_passed(self):
        from shared.llm import GeminiProvider
        provider, mock_client = _make_provider("ok")
        asyncio.run(provider.generate_text("x"))
        call_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
        self.assertEqual(call_kwargs["model"], GeminiProvider.DEFAULT_MODEL)


class TestGenerateStructured(unittest.TestCase):
    def test_returns_validated_model(self):
        payload = json.dumps({"name": "Alice", "value": 42})
        provider, _ = _make_provider(payload)
        result = asyncio.run(provider.generate_structured("extract", _SampleSchema))
        self.assertIsInstance(result, _SampleSchema)
        self.assertEqual(result.name, "Alice")
        self.assertEqual(result.value, 42)

    def test_strips_markdown_fences(self):
        payload = json.dumps({"name": "Bob", "value": 7})
        provider, _ = _make_provider(f"```json\n{payload}\n```")
        result = asyncio.run(provider.generate_structured("x", _SampleSchema))
        self.assertEqual(result.name, "Bob")
        self.assertEqual(result.value, 7)

    def test_raises_on_empty_response(self):
        from shared.llm import LLMProviderError
        provider, _ = _make_provider(None)
        with self.assertRaises(LLMProviderError) as ctx:
            asyncio.run(provider.generate_structured("x", _SampleSchema))
        self.assertIn("empty response", str(ctx.exception))

    def test_raises_on_invalid_json(self):
        from shared.llm import LLMProviderError
        provider, _ = _make_provider("not valid json{{{")
        with self.assertRaises(LLMProviderError) as ctx:
            asyncio.run(provider.generate_structured("x", _SampleSchema))
        self.assertIn("non-JSON", str(ctx.exception))

    def test_raises_on_schema_mismatch(self):
        from shared.llm import LLMProviderError
        # Missing required field 'value'
        provider, _ = _make_provider(json.dumps({"name": "Carol"}))
        with self.assertRaises(LLMProviderError) as ctx:
            asyncio.run(provider.generate_structured("x", _SampleSchema))
        self.assertIn("_SampleSchema", str(ctx.exception))

    def test_wraps_api_exception(self):
        from shared.llm import LLMProviderError
        provider, mock_client = _make_provider()
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=RuntimeError("quota exceeded")
        )
        with self.assertRaises(LLMProviderError) as ctx:
            asyncio.run(provider.generate_structured("x", _SampleSchema))
        self.assertIn("generate_structured failed", str(ctx.exception))

    def test_passes_json_schema_to_api(self):
        provider, mock_client = _make_provider(json.dumps({"name": "X", "value": 1}))
        asyncio.run(provider.generate_structured("x", _SampleSchema))
        call_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
        config_call_kwargs = provider._types.GenerateContentConfig.call_args.kwargs
        self.assertEqual(config_call_kwargs.get("response_mime_type"), "application/json")
        self.assertIn("properties", str(config_call_kwargs.get("response_json_schema", "")))


class TestEmbedText(unittest.TestCase):
    def _make_embed_provider(self, values: list[float] | None = None):
        """Return (GeminiProvider, mock_client) wired for embed_text."""
        from shared.llm import GeminiProvider

        if values is None:
            values = [0.1] * 768

        mock_embedding = MagicMock()
        mock_embedding.values = values

        mock_embed_response = MagicMock()
        mock_embed_response.embeddings = [mock_embedding]

        mock_client = MagicMock()
        mock_client.models.embed_content = MagicMock(return_value=mock_embed_response)

        mock_types = MagicMock()
        mock_types.EmbedContentConfig = MagicMock(return_value=MagicMock())

        provider = GeminiProvider(api_key="test-key", _client=mock_client, _types=mock_types)
        return provider, mock_client

    def test_returns_list_of_floats(self):
        provider, _ = self._make_embed_provider([0.5, 0.3, 0.2])
        result = asyncio.run(provider.embed_text("hello"))
        self.assertEqual(result, [0.5, 0.3, 0.2])
        self.assertIsInstance(result, list)

    def test_passes_model_to_api(self):
        provider, mock_client = self._make_embed_provider()
        asyncio.run(provider.embed_text("hello", model="gemini-embedding-001"))
        call_kwargs = mock_client.models.embed_content.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gemini-embedding-001")

    def test_passes_dimensions_via_config(self):
        provider, mock_client = self._make_embed_provider()
        asyncio.run(provider.embed_text("hello", dimensions=512))
        provider._types.EmbedContentConfig.assert_called_once_with(output_dimensionality=512)

    def test_wraps_api_exception(self):
        from shared.llm import LLMProviderError
        provider, mock_client = self._make_embed_provider()
        mock_client.models.embed_content = MagicMock(side_effect=RuntimeError("quota exceeded"))
        with self.assertRaises(LLMProviderError) as ctx:
            asyncio.run(provider.embed_text("hello"))
        self.assertIn("embed_text failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
