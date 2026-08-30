"""
tests/test_text_extractor.py
-----------------------------
Unit tests for pipeline.text_extractor.

Run with:
    pytest tests/test_text_extractor.py -v
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make sure the project root is on sys.path when running directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.text_extractor import (
    ExtractionResult,
    clean_text,
    extract_text,
    parse_filename,
)


# ===========================================================================
# 1. parse_filename
# ===========================================================================

class TestParseFilename:
    def test_full_pattern(self):
        result = parse_filename("BA_LinkedIn_Alae ADDOU.pdf")
        assert result["profil"] == "BA"
        assert result["job_board"] == "LinkedIn"
        assert result["name_from_file"] == "Alae ADDOU"
        assert result["flags"] == []

    def test_profil_uppercased(self):
        result = parse_filename("dev_Indeed_Jean DUPONT.docx")
        assert result["profil"] == "DEV"

    def test_missing_name(self):
        result = parse_filename("BA_LinkedIn.pdf")
        assert result["profil"] == "BA"
        assert result["job_board"] == "LinkedIn"
        assert result["name_from_file"] is None
        assert "MISSING_NAME_IN_FILENAME" in result["flags"]

    def test_unknown_format(self):
        result = parse_filename("RandomFile.pdf")
        assert "FILENAME_FORMAT_UNKNOWN" in result["flags"]

    def test_name_with_hyphen(self):
        result = parse_filename("DEV_INDEED_Jean-Paul MARTIN.docx")
        assert result["name_from_file"] == "Jean-Paul MARTIN"

    def test_underscore_in_name_preserved(self):
        # Only 2 splits max, so extra underscores stay in name field
        result = parse_filename("BA_LinkedIn_First_Last SURNAME.pdf")
        assert result["name_from_file"] == "First_Last SURNAME"


# ===========================================================================
# 2. clean_text
# ===========================================================================

class TestCleanText:
    def test_strips_null_bytes(self):
        assert "\x00" not in clean_text("hello\x00world")

    def test_collapses_blank_lines(self):
        text = "a\n\n\n\n\nb"
        assert clean_text(text) == "a\n\nb"

    def test_strips_trailing_whitespace(self):
        text = "hello   \nworld   "
        lines = clean_text(text).splitlines()
        assert all(not line.endswith(" ") for line in lines)

    def test_unicode_normalization(self):
        # Composed form should survive
        assert clean_text("café") == "café"

    def test_empty_string(self):
        assert clean_text("") == ""


# ===========================================================================
# 3. extract_text — unsupported format
# ===========================================================================

class TestExtractTextUnsupported:
    def test_unsupported_format_flag(self, tmp_path):
        dummy = tmp_path / "resume.xyz"
        dummy.write_text("data")
        result = extract_text(dummy)
        assert "UNSUPPORTED_FORMAT" in result.extraction_flags
        assert result.raw_text == ""


# ===========================================================================
# 4. extract_text — PDF text-based (mocked pdfplumber)
# ===========================================================================

class TestExtractPDFText:
    def test_pdf_text_path(self, tmp_path):
        dummy = tmp_path / "BA_LinkedIn_Test USER.pdf"
        dummy.write_bytes(b"%PDF-1.4")  # minimal PDF magic bytes

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "A" * 250  # > _MIN_CHAR_COUNT (200)
        mock_page.extract_tables.return_value = []
        mock_page.extract_words.return_value = []
        mock_page.width = 595

        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [mock_page]

        with patch("pdfplumber.open", return_value=mock_pdf):
            result = extract_text(dummy)

        assert result.source_format == "pdf_text"
        assert result.confidence == 1.0
        assert result.file_meta["profil"] == "BA"
        assert result.file_meta["job_board"] == "LinkedIn"
        assert result.file_meta["name_from_file"] == "Test USER"
        assert len(result.raw_text) > 0


# ===========================================================================
# 5. extract_text — PDF OCR fallback (mocked pdf2image + tesseract)
# ===========================================================================

class TestExtractPDFOCR:
    def test_pdf_ocr_path(self, tmp_path):
        dummy = tmp_path / "BA_Indeed_Jean PAUL.pdf"
        dummy.write_bytes(b"%PDF-1.4")

        # _is_text_pdf returns False → triggers OCR branch
        mock_scanned_page = MagicMock()
        mock_scanned_page.extract_text.return_value = None

        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [mock_scanned_page]

        mock_image = MagicMock()

        with (
            patch("pdfplumber.open", return_value=mock_pdf),
            patch("pdf2image.convert_from_path", return_value=[mock_image]),
            patch("pytesseract.image_to_string", return_value="Scanned text content " * 15),
        ):
            result = extract_text(dummy)

        assert result.source_format == "pdf_ocr"
        assert "OCR_USED" in result.extraction_flags
        assert result.confidence == 0.80


# ===========================================================================
# 6. extract_text — DOCX (mocked python-docx)
# ===========================================================================

class TestExtractDOCX:
    def test_docx_path(self, tmp_path):
        dummy = tmp_path / "DEV_LinkedIn_Alice SMITH.docx"
        dummy.write_bytes(b"PK")  # minimal zip magic

        mock_para = MagicMock()
        # Text must exceed _MIN_CHAR_COUNT (200 chars) to avoid LOW_CHAR_COUNT flag
        mock_para.text = "Software Engineer with 5 years of experience in backend development, cloud infrastructure, and agile methodologies. Proficient in Python, Java, and distributed systems design. Experienced in leading cross-functional teams."

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para]
        mock_doc.tables = []

        with patch("docx.Document", return_value=mock_doc):
            result = extract_text(dummy)

        assert result.source_format == "docx"
        assert result.confidence == 1.0
        assert "Software Engineer" in result.raw_text


# ===========================================================================
# 7. LOW_CHAR_COUNT flag
# ===========================================================================

class TestLowCharCount:
    def test_low_char_triggers_flag(self, tmp_path):
        dummy = tmp_path / "BA_LinkedIn_Short FILE.pdf"
        dummy.write_bytes(b"%PDF-1.4")

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Short"  # > threshold for is_text_pdf? No.
        # Make _is_text_pdf return False, then OCR returns tiny text
        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = None

        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [mock_page2]

        mock_image = MagicMock()

        with (
            patch("pdfplumber.open", return_value=mock_pdf),
            patch("pdf2image.convert_from_path", return_value=[mock_image]),
            patch("pytesseract.image_to_string", return_value="Hi"),  # 2 chars
        ):
            result = extract_text(dummy)

        assert "LOW_CHAR_COUNT" in result.extraction_flags
        assert result.confidence <= 0.70
