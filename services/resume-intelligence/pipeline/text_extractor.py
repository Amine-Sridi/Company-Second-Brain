"""
pipeline/text_extractor.py
--------------------------
Deterministic text-extraction module — NO LLM involved.

Supported formats
-----------------
  .pdf   → pdfplumber (text-based) or pdf2image + pytesseract (scanned / OCR)
  .docx  → python-docx  (paragraphs + tables)
  .jpg / .jpeg / .png → pytesseract direct OCR

Legacy .doc (pre-2007 binary Word format) is deliberately NOT supported: it
required shelling out to the antiword CLI tool, which was never installed in
this service's Docker image -- every .doc upload silently degraded to an
empty extraction rather than a clear error. Rather than add antiword as a new
system dependency for a rare, legacy format, .doc is rejected up front by
api.py's allowed-suffix check (422), same as any other unsupported format.

Output contract
---------------
  ExtractionResult dataclass  (see bottom of file)
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Minimum character threshold — below this we consider the extraction poor.
# ---------------------------------------------------------------------------
_MIN_CHAR_COUNT = 200
_PDF_TEXT_THRESHOLD = 50   # chars on first 2 pages → "text-based PDF"


# ===========================================================================
# 1.  Filename metadata parser
# ===========================================================================

def parse_filename(filepath: str | Path) -> dict:
    """
    Parse structured metadata encoded in the resume filename.

    Expected pattern: ``PROFIL_JOBBOARD_Firstname LASTNAME.ext``

    Returns
    -------
    dict with keys:
        profil          – uppercase profile code   (str | None)
        job_board       – job-board label           (str | None)
        name_from_file  – raw candidate name        (str | None)
        flags           – list of flag strings
    """
    stem = Path(filepath).stem          # e.g. "BA_LinkedIn_Alae ADDOU"
    parts = stem.split("_", 2)          # max 2 splits

    result: dict = {
        "profil": None,
        "job_board": None,
        "name_from_file": None,
        "flags": [],
    }

    if len(parts) == 3:
        result["profil"]         = parts[0].strip().upper()
        result["job_board"]      = parts[1].strip()
        result["name_from_file"] = parts[2].strip()
    elif len(parts) == 2:
        result["profil"]    = parts[0].strip().upper()
        result["job_board"] = parts[1].strip()
        result["flags"].append("MISSING_NAME_IN_FILENAME")
    else:
        result["flags"].append("FILENAME_FORMAT_UNKNOWN")

    return result


# ===========================================================================
# 2.  Text cleaning
# ===========================================================================

def clean_text(raw: str) -> str:
    """
    Normalise raw extracted text before passing it downstream.

    Steps
    -----
    1. Unicode NFC normalisation (accents, ligatures)
    2. Collapse 3+ consecutive blank lines -> 2
    3. Strip null bytes and non-printable control chars (keep newline and tab)
    4. Strip trailing whitespace per line
    """
    text = unicodedata.normalize("NFC", raw)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return text.strip()


# ===========================================================================
# 3.  Format-specific extraction helpers
# ===========================================================================

def _is_text_pdf(path: str) -> bool:
    """Return True if the first 2 pages contain selectable text."""
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:2]:
                txt = page.extract_text()
                if txt and len(txt.strip()) >= _PDF_TEXT_THRESHOLD:
                    return True
    except Exception as exc:
        logger.warning("pdfplumber check failed: %s", exc)
    return False


def _pdfplumber_extract(path: str) -> tuple[str, list[str]]:
    """
    Extract text from a text-based PDF using pdfplumber.

    Returns (text, flags) where flags may include
    TABLE_DETECTED and/or MULTI_COLUMN_DETECTED.
    """
    import pdfplumber  # type: ignore

    lines: list[str] = []
    flags: list[str] = []
    table_found = False

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            # --- tables first (structured content) ---
            tables = page.extract_tables()
            if tables:
                table_found = True
                for table in tables:
                    for row in table:
                        row_text = " | ".join(str(c) for c in row if c)
                        if row_text.strip():
                            lines.append(row_text)

            # --- remaining flowing text ---
            text = page.extract_text(x_tolerance=3, y_tolerance=3)
            if text:
                lines.append(text)

            # Heuristic multi-column detection via word bounding boxes
            try:
                words = page.extract_words()
                if words:
                    x_coords = [w["x0"] for w in words]
                    if max(x_coords) - min(x_coords) > page.width * 0.55:
                        flags.append("MULTI_COLUMN_DETECTED")
            except Exception:
                pass

    if table_found:
        flags.append("TABLE_DETECTED")

    return "\n".join(lines), list(dict.fromkeys(flags))   # deduplicate


def _ocr_extract(path: str) -> tuple[str, list[str]]:
    """
    OCR-based extraction: convert PDF pages (or a single image) to PIL
    images, then run pytesseract with fra+eng language pack.

    Works for both scanned PDFs and direct image files.
    """
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore

    flags: list[str] = ["OCR_USED"]
    texts: list[str] = []
    suffix = Path(path).suffix.lower()

    if suffix == ".pdf":
        try:
            from pdf2image import convert_from_path  # type: ignore
            images = convert_from_path(path, dpi=300)
        except Exception as exc:
            logger.error("pdf2image conversion failed: %s", exc)
            return "", flags
    else:
        # Direct image file
        try:
            images = [Image.open(path)]
        except Exception as exc:
            logger.error("Cannot open image %s: %s", path, exc)
            return "", flags

    for img in images:
        try:
            text = pytesseract.image_to_string(img, lang="fra+eng")
        except pytesseract.pytesseract.TesseractError:
            # Fallback to English only if French language pack not installed
            text = pytesseract.image_to_string(img, lang="eng")
        texts.append(text)

    return "\n".join(texts), flags


def _docx_extract(path: str) -> tuple[str, list[str]]:
    """
    Extract text from a .docx file using python-docx.
    Reads both paragraphs and table cells.
    """
    try:
        from docx import Document  # type: ignore
    except ImportError:
        logger.error("python-docx not installed. Run: pip install python-docx")
        return "", []

    doc = Document(path)
    parts: list[str] = []

    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)

    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(
                cell.text.strip() for cell in row.cells if cell.text.strip()
            )
            if row_text:
                parts.append(row_text)

    return "\n".join(parts), []


# ===========================================================================
# 4.  Output dataclass
# ===========================================================================

@dataclass
class ExtractionResult:
    """
    Output contract for the text-extraction stage.

    Attributes
    ----------
    raw_text          : Cleaned full text content.
    file_meta         : Metadata parsed from the filename
                        {profil, job_board, name_from_file}.
    source_format     : "pdf_text" | "pdf_ocr" | "docx" | "image_ocr".
    confidence        : 1.0 for native text; 0.7-0.85 for OCR.
    extraction_flags  : Runtime flags raised during extraction.
    """
    raw_text: str
    file_meta: dict
    source_format: str
    confidence: float
    extraction_flags: list[str] = field(default_factory=list)


# ===========================================================================
# 5.  Public entry point
# ===========================================================================

def extract_text(file_path: str | Path) -> ExtractionResult:
    """
    Extract raw text from *file_path* and return an ExtractionResult.

    Decision tree
    -------------
    .pdf  -> pdfplumber (text-based) else pdf2image + tesseract
    .docx -> python-docx
    .jpg / .jpeg / .png -> pytesseract

    Parameters
    ----------
    file_path : str or Path
        Absolute or relative path to the resume file.

    Returns
    -------
    ExtractionResult
    """
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()
    path_str = str(file_path)

    # --- parse filename metadata (flags are promoted to extraction_flags) ---
    meta = parse_filename(file_path)
    filename_flags: list[str] = meta.pop("flags", [])

    raw_text: str = ""
    source_format: str = "unknown"
    confidence: float = 1.0
    extra_flags: list[str] = []

    # --- dispatch by format ---
    if suffix == ".pdf":
        if _is_text_pdf(path_str):
            logger.info("PDF is text-based -> pdfplumber: %s", file_path.name)
            raw_text, extra_flags = _pdfplumber_extract(path_str)
            source_format = "pdf_text"
            confidence = 1.0
        else:
            logger.info("PDF appears scanned -> OCR fallback: %s", file_path.name)
            raw_text, extra_flags = _ocr_extract(path_str)
            source_format = "pdf_ocr"
            confidence = 0.80

    elif suffix == ".docx":
        logger.info("Extracting DOCX: %s", file_path.name)
        raw_text, extra_flags = _docx_extract(path_str)
        source_format = "docx"
        confidence = 1.0

    elif suffix in {".jpg", ".jpeg", ".png"}:
        logger.info("Extracting image via OCR: %s", file_path.name)
        raw_text, extra_flags = _ocr_extract(path_str)
        source_format = "image_ocr"
        confidence = 0.75

    else:
        logger.error("Unsupported file format: %s", suffix)
        extra_flags.append("UNSUPPORTED_FORMAT")

    # --- clean ---
    raw_text = clean_text(raw_text)

    # --- post-extraction flags ---
    all_flags: list[str] = filename_flags + extra_flags
    if raw_text and len(raw_text) < _MIN_CHAR_COUNT:
        all_flags.append("LOW_CHAR_COUNT")
        confidence = min(confidence, 0.70)

    return ExtractionResult(
        raw_text=raw_text,
        file_meta=meta,
        source_format=source_format,
        confidence=confidence,
        extraction_flags=all_flags,
    )
