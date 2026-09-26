"""
benchmarks/local_llm/verify_fallback.py
-----------------------------------------
Live end-to-end proof of the fallback, using the REAL OllamaProvider, the REAL
FallbackLLMProvider, and this project's REAL prompts and Pydantic schemas.

The primary is a stub that raises the exact error types Gemini's wrapper produces,
because the point under test is the routing decision, not Gemini itself (and burning
the project's 20/day quota to prove a 429 would be self-defeating).
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from shared.llm import (  # noqa: E402
    FallbackLLMProvider,
    LLMProviderError,
    LLMProviderUnavailableError,
    OllamaProvider,
)

# Real RWS schema, loaded by explicit path (RWS and RI both ship a `schemas` module).
spec = importlib.util.spec_from_file_location(
    "rws_schemas", REPO / "services" / "recruitment-workflow" / "schemas.py"
)
rws_schemas = importlib.util.module_from_spec(spec)
# Register before exec so Pydantic can resolve this module's forward references.
sys.modules["rws_schemas"] = rws_schemas
spec.loader.exec_module(rws_schemas)
StructuredJobDescription = rws_schemas.StructuredJobDescription

# The real JD-structuring prompt, taken from the same helper the benchmark uses (a
# verbatim copy of main.py's structure_job_description). An earlier version of this
# script paraphrased the prompt instead, and the local model then returned skills as
# verbose phrases ("strong Python experience") rather than atomic tokens ("Python") --
# which this project's deterministic matcher would not match, since skill-synonym
# normalization is deliberately not implemented. Using the production prompt is
# therefore part of what is under test, not an incidental detail.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tasks  # noqa: E402

JD = tasks.JD_CASES[0]["raw"]
PROMPT = tasks._jd_prompt(JD)


class StubPrimary:
    """Stands in for GeminiProvider, failing exactly how its wrapper would."""

    def __init__(self, error=None):
        self.error = error
        self.used = False

    async def generate_text(self, prompt, *, model=None):
        self.used = True
        if self.error:
            raise self.error
        return "PRIMARY-ANSWER"

    async def generate_structured(self, prompt, schema, *, model=None):
        self.used = True
        if self.error:
            raise self.error
        return schema(required_skills=["FromPrimary"], preferred_skills=[],
                      min_years_experience=3, titles=["Backend Engineer"])

    async def embed_text(self, text, *, model=None, dimensions=768):
        self.used = True
        if self.error:
            raise self.error
        return [0.1] * dimensions


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    return bool(condition)


def main() -> int:
    ollama = OllamaProvider(
        "http://localhost:11434", text_model="llama3.2:3b", structured_model="granite4.1:3b"
    )
    results = []

    print("\n1. Gemini available -> Gemini used, local model never touched")
    primary = StubPrimary()
    provider = FallbackLLMProvider(primary, ollama)
    out = asyncio.run(provider.generate_structured(PROMPT, StructuredJobDescription))
    results.append(check("primary result returned", out.required_skills == ["FromPrimary"],
                         f"required_skills={out.required_skills}"))

    print("\n2. Gemini unavailable (429 quota) -> real local model completes the request")
    primary = StubPrimary(LLMProviderUnavailableError("429 RESOURCE_EXHAUSTED: quota"))
    provider = FallbackLLMProvider(primary, ollama)
    out = asyncio.run(provider.generate_structured(PROMPT, StructuredJobDescription))
    skills = {s.lower() for s in out.required_skills}
    results.append(check("valid StructuredJobDescription returned",
                         isinstance(out, StructuredJobDescription)))
    results.append(check("extraction is actually correct",
                         {"python", "fastapi"} <= skills,
                         f"required_skills={out.required_skills}, years={out.min_years_experience}"))

    print("\n3. Gemini unavailable -> local model for free-text generation")
    primary = StubPrimary(LLMProviderUnavailableError("503 Service Unavailable"))
    provider = FallbackLLMProvider(primary, ollama)
    text = asyncio.run(provider.generate_text(
        "In one short sentence, say that a candidate met the required skills."))
    results.append(check("non-empty text from local model", bool(text.strip()),
                         repr(text[:70])))

    print("\n4. Application bug -> NOT sent to the fallback")
    bug = ValueError("genuine bug in prompt assembly")
    primary = StubPrimary(bug)
    provider = FallbackLLMProvider(primary, ollama)
    try:
        asyncio.run(provider.generate_structured(PROMPT, StructuredJobDescription))
        results.append(check("bug re-raised", False, "no exception raised"))
    except ValueError as exc:
        results.append(check("bug re-raised unchanged", exc is bug))
    except Exception as exc:
        results.append(check("bug re-raised unchanged", False, f"got {type(exc).__name__}"))

    print("\n5. Local runtime unreachable -> graceful failure with the PRIMARY's error")
    dead = OllamaProvider("http://localhost:1", text_model="llama3.2:3b")
    primary_exc = LLMProviderUnavailableError("429 quota exhausted")
    provider = FallbackLLMProvider(StubPrimary(primary_exc), dead)
    try:
        asyncio.run(provider.generate_structured(PROMPT, StructuredJobDescription))
        results.append(check("raises when both fail", False, "no exception raised"))
    except Exception as exc:
        results.append(check("original primary error surfaces", exc is primary_exc,
                             f"{type(exc).__name__}: {exc}"))
        results.append(check("still an LLMProviderError for existing handlers",
                             isinstance(exc, LLMProviderError)))

    print("\n6. Embeddings never fall back, even when the primary is unavailable")
    primary = StubPrimary(LLMProviderUnavailableError("429 quota"))
    spy = OllamaProvider("http://localhost:11434", text_model="x", embedding_model="nomic-embed-text")
    called = {"embed": False}
    original = spy.embed_text

    async def watched(*a, **kw):
        called["embed"] = True
        return await original(*a, **kw)

    spy.embed_text = watched
    provider = FallbackLLMProvider(primary, spy)
    try:
        asyncio.run(provider.embed_text("a candidate summary"))
        results.append(check("embed_text raised", False, "it returned a vector"))
    except LLMProviderUnavailableError:
        results.append(check("embed_text raised instead of falling back", True))
    results.append(check("local embedding model was never called", not called["embed"]))

    print(f"\n{'=' * 62}\n{sum(results)}/{len(results)} checks passed\n{'=' * 62}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
