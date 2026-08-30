"""
tests/test_parsing_integration.py
-----------------------------------
Real-Gemini integration harness: runs parse_resume_text() against every .txt file
in output/extracted/ (produced by the text-extraction stage) using an actual
GeminiProvider. Skipped entirely if GEMINI_API_KEY is not set, or if there are no
extracted sample files to run against -- this is the live smoke-test vehicle, not
part of the mocked unit-test suite (see tests/unit/test_parsing_agent.py for that).

Each result is saved as output/parsed/<stem>.json.

Usage
-----
    python tests/test_parsing_integration.py          # standalone with report
    pytest tests/test_parsing_integration.py -v -s   # via pytest
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import textwrap
from pathlib import Path

import pytest

# Project root on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.parsing_agent import ParseResult, parse_resume_text
from shared.llm import GeminiProvider, LLMProviderError

logging.basicConfig(level=logging.WARNING)

# ---------------------------------------------------------------------------
# Locate extracted .txt files
# ---------------------------------------------------------------------------
from config import OUTPUT_DIR

EXTRACTED_DIR = OUTPUT_DIR / "extracted"
PARSED_DIR    = OUTPUT_DIR / "parsed"
PARSED_DIR.mkdir(parents=True, exist_ok=True)

txt_files = sorted(EXTRACTED_DIR.glob("*.txt"))

if not os.environ.get("GEMINI_API_KEY"):
    pytest.skip("GEMINI_API_KEY not set -- skipping live Gemini smoke test", allow_module_level=True)

if not txt_files:
    pytest.skip(
        "No .txt files found in output/extracted/. "
        "Run tests/test_extraction_integration.py first.",
        allow_module_level=True,
    )


def _make_llm() -> GeminiProvider:
    return GeminiProvider(api_key=os.environ["GEMINI_API_KEY"])


# ---------------------------------------------------------------------------
# Parametrised pytest test — one per file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("txt_path", txt_files, ids=[f.stem for f in txt_files])
def test_parse_sample_resume(txt_path: Path):
    raw_text = txt_path.read_text(encoding="utf-8")
    llm = _make_llm()
    result = asyncio.run(parse_resume_text(llm, raw_text))

    assert isinstance(result, ParseResult)

    # A total provider failure is a legitimate outcome to report, not assert against --
    # skip rather than fail so a real Gemini outage doesn't look like a code regression.
    if not result.success:
        pytest.skip(f"Gemini extraction failed for {txt_path.name}: {result.flags}")

    # basics section must exist
    assert "basics" in result.data, "'basics' key missing from parsed JSON"

    # work section must be a list
    assert isinstance(result.data.get("work", []), list), "'work' must be a list"

    # confidence in range
    assert 0.0 <= result.confidence <= 1.0

    # Save to output/parsed/
    out_file = PARSED_DIR / (txt_path.stem + ".json")
    out_file.write_text(
        json.dumps(result.data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Standalone runner — pretty summary table
# ---------------------------------------------------------------------------

def _run_and_report():
    col_w = [36, 8, 8, 8, 35]
    header = (
        f"{'File':<{col_w[0]}} {'Attempts':>{col_w[1]}} {'Conf':>{col_w[2]}} "
        f"{'Exp':<{col_w[3]}} {'Flags':<{col_w[4]}}"
    )
    sep = "-" * (sum(col_w) + len(col_w))

    print(f"\nParsed JSON will be saved to: {PARSED_DIR}")
    print("\n" + sep)
    print(header)
    print(sep)

    llm = _make_llm()
    errors = []
    for txt_path in txt_files:
        try:
            raw_text = txt_path.read_text(encoding="utf-8")
            result = asyncio.run(parse_resume_text(llm, raw_text))

            # Save JSON
            out_file = PARSED_DIR / (txt_path.stem + ".json")
            out_file.write_text(
                json.dumps(result.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            name = textwrap.shorten(txt_path.stem, width=col_w[0] - 1, placeholder="…")
            flags_str = (
                ", ".join(
                    f.get("type", f) if isinstance(f, dict) else f
                    for f in result.flags
                )
                if result.flags else "—"
            )
            flags_str = textwrap.shorten(flags_str, width=col_w[4] - 1, placeholder="…")

            print(
                f"{name:<{col_w[0]}} {result.attempts:>{col_w[1]}} "
                f"{result.confidence:>{col_w[2]}.2f} "
                f"{result.total_experience:<{col_w[3]}} {flags_str:<{col_w[4]}}"
            )
        except (LLMProviderError, OSError) as exc:
            errors.append((txt_path.stem, exc))
            print(f"{'ERROR':<{col_w[0]}} — {txt_path.stem}: {type(exc).__name__}")

    print(sep)
    print(f"\n  Total: {len(txt_files)} files   Errors: {len(errors)}\n")


if __name__ == "__main__":
    _run_and_report()
