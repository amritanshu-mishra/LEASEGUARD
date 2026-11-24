"""OCR path: scanned PDFs and photos must be read, and clearly marked as such.

The important assertions here are not "a number was found" but "the caller was
told this came from a picture". Measured behaviour: the OCR engine can drop a
whole line and still report ~0.98 mean confidence on the lines it did read, so a
confidence threshold alone is not a safety net - the `ocr` flag is.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from app.infra import documents, ocr


def _pytest_ocr_available() -> bool:
    """Skip when OCR is absent; FAIL when it is installed but cannot load.

    An installed-but-unimportable engine (the libGL.so.1 case) is a deployment
    bug, not a reason to quietly run without OCR coverage.
    """
    if ocr.available():
        return True
    if ocr.installed():
        pytest.fail(
            "OCR is installed but cannot load: "
            f"{ocr.unavailable_reason()}. Run scripts/install_backend.sh - "
            "a GUI-linked opencv-python wheel likely replaced the headless one."
        )
    return False


def _render_pdf(text_lines: list[str], path: Path) -> Path:
    """Build a real PDF with a text layer (so it goes down the pypdf path)."""
    fitz = pytest.importorskip("pymupdf")
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in text_lines:
        page.insert_text((72, y), line, fontsize=13)
        y += 24
    doc.save(path)
    doc.close()
    return path


def _render_scanned_pdf(text_lines: list[str], path: Path) -> Path:
    """A PDF that contains only a picture of text - the photographed-lease case."""
    fitz = pytest.importorskip("pymupdf")
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 34)
    img = Image.new("RGB", (1700, 240), "white")
    draw = ImageDraw.Draw(img)
    y = 40
    for line in text_lines:
        draw.text((40, y), line, fill="black", font=font)
        y += 60
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    doc = fitz.open()
    page = doc.new_page(width=850, height=200)
    page.insert_image(fitz.Rect(0, 0, 850, 200), stream=buf.getvalue())
    doc.save(path)
    doc.close()
    return path


LEASE_LINES = [
    "COMMERCIAL LEASE DEED",
    "Monthly Rent is Rs. 40,000 per month.",
    "Maintenance shall be capped at Rs. 4,000 per month.",
]


class TestNumericRepair:
    def test_letter_o_inside_a_number_becomes_zero(self):
        # This exact corruption was produced by the OCR engine on a real render.
        assert ocr.repair_numeric_confusions("Rs. 40,ooo per month") == "Rs. 40,000 per month"
        assert ocr.repair_numeric_confusions("Rs. 4,ooo") == "Rs. 4,000"
        assert ocr.repair_numeric_confusions("Rs. 1,20,00o") == "Rs. 1,20,000"
        assert ocr.repair_numeric_confusions("1o percent") == "10 percent"

    def test_l_for_one_is_repaired(self):
        # Measured on a photographed lease: "10 percent" read as "l0 percent".
        # Left unrepaired, the parser extracts an escalation rate of 0, i.e.
        # "rent never rises" - a silently wrong audit.
        assert (
            ocr.repair_numeric_confusions("Rent shall increase by l0 percent per annum.")
            == "Rent shall increase by 10 percent per annum."
        )
        assert ocr.repair_numeric_confusions("I0 percent") == "10 percent"

    def test_real_words_are_left_alone(self):
        for text in (
            "Commencement in October 2025",
            "no of units: 2",
            "Property: Shop 12, Sector 18",
            "to October",
            # A corrupted word is worse than an unrepaired digit, so the rules
            # stay narrow on purpose.
            "B2B invoices",
            "S18 market",
            "COMMERCIAL LEASE DEED",
        ):
            assert ocr.repair_numeric_confusions(text) == text


class TestQuoteValidationOnOcrText:
    """The BR6 guarantee must survive OCR spacing.

    OCR renders "Rs. 50,000" as "Rs.50,000". A naive whitespace normalisation
    reads the period as a sentence end, inserts a space the document never had,
    and rejects a quote that is actually verbatim - whereupon BR6 nulls the
    value. Measured: base rent came out as None on a scanned lease while every
    other term worked.
    """

    def test_missing_space_after_currency_abbreviation_still_matches(self):
        from app.infra.extraction import validate_quote

        assert validate_quote("Rs. 50,000 per month", "Monthly Rent is Rs.50,000 per month.")

    def test_normal_spacing_still_matches(self):
        from app.infra.extraction import validate_quote

        assert validate_quote("Rs. 50,000 per month", "Monthly Rent is Rs. 50,000 per month.")

    def test_a_different_amount_is_still_rejected(self):
        """The loosening must not become a hole that lets any number through."""
        from app.infra.extraction import validate_quote

        assert not validate_quote("Rs. 60,000 per month", "Monthly Rent is Rs.50,000 per month.")

    def test_unrelated_text_is_still_rejected(self):
        from app.infra.extraction import validate_quote

        assert not validate_quote("The deposit is refundable", "Monthly Rent is Rs.50,000.")


class TestScannedDocuments:
    def test_scanned_pdf_is_ocred_and_flagged(self, tmp_path):
        if not _pytest_ocr_available():
            pytest.skip("OCR engine not installed")
        path = _render_scanned_pdf(LEASE_LINES, tmp_path / "scan.pdf")
        parsed = documents.parse_pages("scan.pdf", path.read_bytes())

        assert parsed.ocr is True, "caller must be told this came from a picture"
        assert 0.0 < parsed.confidence <= 1.0
        text = "\n".join(parsed)
        assert "40,000" in text, f"rent not read and repaired: {text!r}"

    def test_escalation_rate_survives_ocr(self, tmp_path):
        """The expensive failure: a lost digit turning 10% into 0%."""
        if not _pytest_ocr_available():
            pytest.skip("OCR engine not installed")
        path = _render_scanned_pdf(
            ["Rent shall increase by 10 percent per annum on each anniversary."],
            tmp_path / "esc.pdf",
        )
        parsed = documents.parse_pages("esc.pdf", path.read_bytes())
        text = "\n".join(parsed)
        # Either the digit came through clean, or it was repaired to a clean 10.
        assert "10 percent" in text, f"escalation not readable from scan: {text!r}"
        assert "l0 percent" not in text, "letter-l survived into the extracted text"

    def test_photo_upload_is_ocred(self, tmp_path):
        if not _pytest_ocr_available():
            pytest.skip("OCR engine not installed")
        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 34)
        img = Image.new("RGB", (1700, 200), "white")
        draw = ImageDraw.Draw(img)
        draw.text((40, 40), "Monthly Rent is Rs. 40,000 per month.", fill="black", font=font)
        draw.text((40, 110), "Capped maintenance Rs. 4,000.", fill="black", font=font)
        photo = tmp_path / "lease.jpg"
        img.save(photo, format="JPEG")

        parsed = documents.parse_pages("lease.jpg", photo.read_bytes())
        assert parsed.ocr is True
        assert "40,000" in "\n".join(parsed)

    def test_text_pdf_is_not_marked_as_ocr(self, tmp_path):
        path = _render_pdf(["Monthly Rent is Rs. 40,000 per month."], tmp_path / "text.pdf")
        parsed = documents.parse_pages("text.pdf", path.read_bytes())
        assert parsed.ocr is False
        assert parsed.confidence == 1.0
        assert "40,000" in "\n".join(parsed)

    def test_unreadable_scan_gives_a_useful_message(self, tmp_path):
        """A blank page must not be accepted as a lease."""
        if not _pytest_ocr_available():
            pytest.skip("OCR engine not installed")
        fitz = pytest.importorskip("pymupdf")
        doc = fitz.open()
        doc.new_page()
        path = tmp_path / "blank.pdf"
        doc.save(path)
        doc.close()

        with pytest.raises(documents.DocumentParseError) as exc:
            documents.parse_pages("blank.pdf", path.read_bytes())
        message = str(exc.value).lower()
        assert "scan" in message or "text" in message
        # No raw exception type names leaking to the tenant.
        assert not re.search(r"Error|Exception|Traceback", str(exc.value))

    def test_ocr_available_is_a_bool(self):
        assert isinstance(ocr.available(), bool)

    def test_installed_is_a_bool(self):
        assert isinstance(ocr.installed(), bool)
