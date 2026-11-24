"""Turn uploaded files into page-tagged plain text.

PDFs are parsed with pypdf; plain text/markdown is accepted directly. Images are
rejected with a clear message rather than silently producing an empty document.
"""
from __future__ import annotations

import io
import re

from ..core.config import MAX_PAGES

PDF_MAGIC = b"%PDF"


class DocumentParseError(Exception):
    pass


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

    # An image renamed to .pdf: say so plainly instead of "corrupted PDF".
    image_kind = _sniff_image(data)
    if image_kind:
        raise DocumentParseError(
            f"This looks like a {image_kind}, not a text-based PDF. "
            "Upload a PDF or text file, or paste the text."
        )

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
    if name.endswith((".png", ".jpg", ".jpeg", ".webp", ".heic")):
        raise DocumentParseError(
            "Image invoices are not supported yet. Please upload a PDF or paste the text."
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

    if not any(p.strip() for p in pages):
        raise DocumentParseError(
            "No readable text found in this PDF (it looks like a scan). "
            "Please upload a text-based PDF or paste the text."
        )
    return pages
