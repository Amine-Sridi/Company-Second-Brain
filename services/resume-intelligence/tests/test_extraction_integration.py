"""
tests/test_extraction_integration.py
--------------------------------------
Integration tests that run extract_text() against every real sample resume
in tests/sample_resumes/.

Usage
-----
    python tests/test_extraction_integration.py
    # or via pytest (skipped automatically if no sample files found):
    pytest tests/test_extraction_integration.py -v -s

Each resume is tested for:
  1. ExtractionResult is returned (no exception)
  2. raw_text is non-empty
  3. file_meta fields (profil, job_board, name_from_file) are populated
  4. source_format is one of the known values
  5. confidence is in [0.0, 1.0]

A summary table is printed at the end.
"""

import sys
import textwrap
from pathlib import Path

import pytest

# Ensure project root is on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.text_extractor import ExtractionResult, extract_text
from config import OUTPUT_DIR

# Where extracted .txt files are written
EXTRACTED_DIR = OUTPUT_DIR / "extracted"
EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Locate sample files
# ---------------------------------------------------------------------------
SAMPLE_DIR = ROOT / "tests" / "sample_resumes"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".jpg", ".jpeg", ".png"}

sample_files = sorted(
    f for f in SAMPLE_DIR.iterdir()
    if f.suffix.lower() in SUPPORTED_EXTENSIONS
)

if not sample_files:
    pytest.skip("No sample resume files found in tests/sample_resumes/", allow_module_level=True)


# ---------------------------------------------------------------------------
# Parametrised test — one test per file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("resume_path", sample_files, ids=[f.name for f in sample_files])
def test_extract_sample_resume(resume_path: Path):
    """Extract text from a real resume file and assert basic contracts."""
    result = extract_text(resume_path)

    # Must return correct type
    assert isinstance(result, ExtractionResult), "Expected ExtractionResult"

    # raw_text must not be empty
    assert result.raw_text.strip(), (
        f"Empty extraction for {resume_path.name} "
        f"(format={result.source_format}, flags={result.extraction_flags})"
    )

    # source_format must be a known value
    assert result.source_format in {"pdf_text", "pdf_ocr", "docx", "image_ocr", "unknown"}, (
        f"Unexpected source_format: {result.source_format}"
    )

    # confidence must be a valid float in [0, 1]
    assert 0.0 <= result.confidence <= 1.0, f"confidence out of range: {result.confidence}"

    # file_meta must have the expected keys
    for key in ("profil", "job_board", "name_from_file"):
        assert key in result.file_meta, f"Missing key '{key}' in file_meta"

    # LOW_CHAR_COUNT should not be present for real resumes (sanity check)
    if "LOW_CHAR_COUNT" in result.extraction_flags:
        # Warn but don't fail — OCR on a scanned doc may produce little text
        print(f"\n  ⚠  LOW_CHAR_COUNT on {resume_path.name}: only {len(result.raw_text)} chars extracted")

    # Save extracted text to output/extracted/<stem>.txt
    out_file = EXTRACTED_DIR / (resume_path.stem + ".txt")
    out_file.write_text(result.raw_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Standalone runner — pretty summary table
# ---------------------------------------------------------------------------

def _run_all_and_print_report():
    col_w = [36, 10, 6, 22, 30]
    header = (
        f"{'File':<{col_w[0]}} {'Format':<{col_w[1]}} {'Conf':>{col_w[2]}} "
        f"{'Chars':>{col_w[3]}} {'Flags':<{col_w[4]}}"
    )
    sep = "-" * sum(col_w + [4 * 1])

    print(f"\nSaving extracted text to: {EXTRACTED_DIR}")
    print("\n" + sep)
    print(header)
    print(sep)

    errors = []
    for path in sample_files:
        try:
            r = extract_text(path)
            # Save .txt file
            out_file = EXTRACTED_DIR / (path.stem + ".txt")
            out_file.write_text(r.raw_text, encoding="utf-8")

            name = textwrap.shorten(path.name, width=col_w[0] - 1, placeholder="…")
            flags_str = ", ".join(r.extraction_flags) if r.extraction_flags else "—"
            flags_str = textwrap.shorten(flags_str, width=col_w[4] - 1, placeholder="…")
            char_count = f"{len(r.raw_text):,}"
            print(
                f"{name:<{col_w[0]}} {r.source_format:<{col_w[1]}} {r.confidence:>{col_w[2]}.2f} "
                f"{char_count:>{col_w[3]}} {flags_str:<{col_w[4]}}"
            )
        except Exception as exc:
            errors.append((path.name, exc))
            print(f"{'ERROR':<{col_w[0]}} — {path.name}: {exc}")

    print(sep)
    print(f"\n  Total: {len(sample_files)} files   Errors: {len(errors)}\n")

    if errors:
        print("Errors detail:")
        for name, exc in errors:
            print(f"  {name}: {exc}")


if __name__ == "__main__":
    _run_all_and_print_report()
