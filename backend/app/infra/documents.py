"""Turn uploaded files into page-tagged plain text.

Text-based PDFs go through pypdf. Scans, photographed pages and plain images go
through OCR (see `ocr.py`) when the engine is installed, and get a confidence
score and an `ocr` marker so the caller can ask the tenant to check the result.
Anything we genuinely cannot read is refused with a clear message.
"""
from __future__ import annotations

import io
import re

from ..core.config import MAX_PAGES
from . import ocr

PDF_MAGIC = b"%PDF"


class DocumentParseError(Exception):
    pass


class ParsedDocument(list):
    """A list of pages that also says how the text was obtained.

    `ocr` is True when any page came from a picture. `confidence` is the mean
    OCR confidence, or 1.0 for real text extraction.
    """

    ocr: bool = False
    confidence: float = 1.0


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Image signatures that are frequently renamed to .pdf by phone/gallery exports.
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "PNG image"),
    (b"\xff\xd8\xff", "JPEG image"),
    (b"GIF87a", "GIF image"),
    (b"GIF89a", "GIF image"),
    (b"RIFF", "WebP image"),
)


def _sniff_image(data: bytes) -> str | None:
    for magic, label in _IMAGE_MAGIC:
        if data.startswith(magic):
            return label
    return None


def parse_pages(filename: str, data: bytes) -> list[str]:
    name = (filename or "").lower()

    # An image renamed to .pdf is still an image; read it with OCR.
    image_kind = _sniff_image(data)
    if image_kind:
        return _ocr_single_image(data, what=image_kind)

    if data[:4] == PDF_MAGIC or name.endswith(".pdf"):
        return _parse_pdf(data)
    if name.endswith((".txt", ".md", ".text")) or _looks_texty(data):
        text = _clean(data.decode("utf-8", errors="replace"))
        if len(text) < 20:
            raise DocumentParseError(
                "This file has almost no readable text. Check that the upload is the "
                "document itself, not a blank or near-empty file."
            )
        return [text]
    if name.endswith((".png", ".jpg", ".jpeg", ".webp")):
        return _ocr_single_image(data)
    if name.endswith(".heic"):
        raise DocumentParseError(
            "HEIC photos are not supported. In your phone's camera settings choose "
            "'Most Compatible', or convert the photo to JPEG and upload again."
        )
    raise DocumentParseError("Unsupported file type. Upload a PDF or a text file.")


def _looks_texty(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:2000]
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
    return printable / max(len(sample), 1) > 0.9


def _parse_pdf(data: bytes) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is in requirements
        raise DocumentParseError("Server missing pypdf; cannot read PDFs.") from exc

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise DocumentParseError("Could not read this PDF. It may be corrupted or password-protected.") from exc

    pages: list[str] = []
    for idx, page in enumerate(reader.pages):
        if idx >= MAX_PAGES:
            break
        try:
            pages.append(_clean(page.extract_text() or ""))
        except Exception:
            pages.append("")

    if any(p.strip() for p in pages):
        parsed = ParsedDocument(pages)
        parsed.ocr = False
        parsed.confidence = 1.0
        return parsed

    # No text layer at all: this is a scan. Read it as pictures.
    return _ocr_pdf(data)


def _ocr_pdf(data: bytes) -> ParsedDocument:
    if not ocr.available():
        raise DocumentParseError(
            "This PDF has no text in it - it is a scan or a photo. OCR is not "
            f"available on this server ({ocr.unavailable_reason()}), so it cannot "
            "be read. Paste the text instead."
        )
    try:
        results = ocr.ocr_pdf_pages(data, MAX_PAGES)
    except ocr.OcrUnavailable as exc:
        raise DocumentParseError(
            f"This looks like a scanned PDF and OCR could not run ({exc}). "
            "Paste the text instead."
        ) from exc
    except Exception as exc:
        raise DocumentParseError(
            "This looks like a scanned PDF but the scan could not be read. "
            "Try a sharper photo or paste the text."
        ) from exc

    pages = [_clean(text) for text, _conf in results]
    if not any(p.strip() for p in pages):
        raise DocumentParseError(
            "This looks like a scanned PDF, but no text could be read from it. "
            "Try a sharper photo or paste the text."
        )
    return _mark_ocr(pages, [c for _t, c in results])


def _ocr_single_image(data: bytes, what: str = "image") -> ParsedDocument:
    if not ocr.available():
        raise DocumentParseError(
            f"This is an {what}, and OCR is not available on this server "
            f"({ocr.unavailable_reason()}). Upload a text-based PDF or paste the text."
        )
    try:
        text, confidence = ocr.ocr_image_bytes(data)
    except ocr.OcrUnavailable as exc:
        raise DocumentParseError(f"OCR could not run on this image ({exc}).") from exc
    except Exception as exc:
        raise DocumentParseError(
            "This looks like an image but it could not be read. Try a sharper photo."
        ) from exc
    text = _clean(text)
    if len(text) < 20:
        raise DocumentParseError(
            "Almost no text could be read from this image. Try a sharper photo with "
            "the whole page visible, or paste the text."
        )
    return _mark_ocr([text], [confidence])


def _mark_ocr(pages: list[str], confidences: list[float]) -> ParsedDocument:
    parsed = ParsedDocument(pages)
    parsed.ocr = True
    real = [c for c in confidences if c > 0]
    parsed.confidence = (sum(real) / len(real)) if real else 0.0
    return parsed
