"""Read text out of scans and phone photos.

Most small tenants do not have a text-based PDF of their lease — they have a
photograph, or a PDF that is itself a picture. Without this module those files
fail outright.

What this module will and will not promise
------------------------------------------
OCR is *lossy in a way that text extraction is not*: measured on a five-line
synthetic page, the engine read every line with a mono or serif font but dropped
the escalation clause entirely in a sans font — and returned only 4 of 5 lines
with no error, no empty result, and high confidence on the lines it did return.
A missed escalation clause silently changes the audit.

So OCR text is never treated as equal to extracted text: callers get a
confidence figure and an `ocr` flag, the upload response carries a warning, and
the UI asks the tenant to check the terms it read. The engine is also optional —
if it is not installed the app still runs and says so plainly.

Digit repair
------------
The engine routinely renders 0 as the letter o ("40,ooo"). `repair_numeric_
confusions` fixes that only inside tokens that already contain a digit, so real
words ("October", "no") are untouched.
"""
from __future__ import annotations

import re
import threading
from typing import Any

# Mean per-line confidence below which we tell the tenant the scan was poor.
LOW_CONFIDENCE = 0.75

# Rendering width in points. 200 DPI is the usual sweet spot: enough detail for
# small print, without turning a 50-page lease into a 4 GB image.
_DPI = 200

_engine: Any = None
_engine_lock = threading.Lock()
_engine_error: str | None = None


def installed() -> bool:
    """True when the OCR package is present, whether or not it can be used.

    Distinguished from `available()` so tests can tell "this machine has no OCR
    support" (skip) from "OCR is installed and broken" (fail). That difference
    matters: a headless server with a GUI-linked cv2 installs fine and then
    raises ImportError("libGL.so.1") at import time, which is exactly the state
    that should fail a build rather than silently disable a feature.
    """
    try:
        import importlib.util  # noqa: PLC0415

        return importlib.util.find_spec("rapidocr_onnxruntime") is not None
    except Exception:
        return False


def available() -> bool:
    """True when the OCR engine can actually be loaded on this machine."""
    return _load_engine() is not None


def unavailable_reason() -> str:
    return _engine_error or "OCR engine not installed"


def _load_engine() -> Any:
    global _engine, _engine_error
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415 - optional dep
        except Exception as exc:  # ImportError, or a missing system library
            _engine_error = f"OCR engine unavailable ({type(exc).__name__})"
            return None
        try:
            _engine = RapidOCR()
        except Exception as exc:  # pragma: no cover - model load failure
            _engine_error = f"OCR engine failed to start ({type(exc).__name__})"
            return None
        return _engine


# Characters the engine substitutes for digits. `l`/`I` for 1, `o`/`O` for 0,
# `S` for 5, `B` for 8. Measured cases: "40,ooo", "l0 percent" for "10 percent".
_CONFUSABLE = {"o": "0", "O": "0", "l": "1", "I": "1", "S": "5", "B": "8"}


def repair_numeric_confusions(text: str) -> str:
    """Turn letters that OCR read inside a number back into digits.

    This is not cosmetic. On a photographed lease the engine read "10 percent"
    as "l0 percent"; the parser then extracted an escalation rate of 0, i.e.
    "rent never increases" - a silently wrong audit, the exact failure this app
    exists to prevent.

    Only tokens that already contain at least one digit are rewritten, so
    "October", "no of units" and "Sector 18" survive untouched.
    """
    def _fix(token: re.Match[str]) -> str:
        return "".join(_CONFUSABLE.get(ch, ch) for ch in token.group(0))

    # A run of digits/separators/confusables that contains at least one digit.
    text = re.sub(
        r"(?<![\w])[\d,.]*[\d][\d,.]*(?:[oOlISB][\d,.]*)*(?![\w])",
        lambda m: _fix(m) if any(c in "oOlISB" for c in m.group(0)) else m.group(0),
        text,
    )
    # Confusable glued to the front of a number: "l0 percent", "o.5", "I0".
    # `(?<![A-Za-z])` rather than `(?<!\w)` because the confusable is itself a
    # word character, so a \w lookbehind would block every match.
    #
    # Deliberately limited to l/I/o/O. Allowing S/B here would rewrite real
    # tokens such as "B2B" or "S18", and a corrupted word is worse than an
    # un-repaired digit.
    text = re.sub(
        r"(?<![A-Za-z])([oOlI])(?=[.,]?\d+(?![A-Za-z]))",
        lambda m: _CONFUSABLE[m.group(1)],
        text,
    )
    # The same glued to the end: "5O" for "50", "1o" for "10". Again limited to
    # l/I/o/O so that "B2B" cannot become "B28".
    text = re.sub(
        r"(?<=\d)([oOlI])(?![A-Za-z])",
        lambda m: _CONFUSABLE[m.group(1)],
        text,
    )
    return text


def _ocr_image(engine: Any, image: Any) -> tuple[str, float]:
    """OCR one PIL image. Returns (text, mean confidence 0..1)."""
    result, _elapsed = engine(image)
    if not result:
        return "", 0.0
    # Result rows are [box, text, confidence]; sort into reading order by the
    # top-left corner so lines do not arrive shuffled.
    rows = sorted(result, key=lambda r: (round(r[0][0][1] / 12), r[0][0][0]))
    lines = [str(r[1]) for r in rows]
    scores = [float(r[2]) for r in rows if len(r) > 2]
    mean = sum(scores) / len(scores) if scores else 0.0
    return repair_numeric_confusions("\n".join(lines)), mean


def ocr_image_bytes(data: bytes) -> tuple[str, float]:
    """OCR a standalone image (PNG/JPEG/WebP). Returns (text, confidence)."""
    engine = _load_engine()
    if engine is None:
        raise OcrUnavailable(unavailable_reason())
    from PIL import Image  # noqa: PLC0415 - optional dep

    import io

    with Image.open(io.BytesIO(data)) as img:
        return _ocr_image(engine, img.convert("RGB"))


class OcrUnavailable(Exception):
    pass


def ocr_pdf_pages(data: bytes, max_pages: int) -> list[tuple[str, float]]:
    """Render each PDF page to an image and OCR it.

    Returns one (text, confidence) pair per page actually read. Stops at
    `max_pages` for the same reason the text parser does: a phone upload should
    not pin a small server.
    """
    engine = _load_engine()
    if engine is None:
        raise OcrUnavailable(unavailable_reason())

    try:
        import pymupdf as fitz  # PyMuPDF >= 2.0  # noqa: PLC0415 - optional dep
    except ImportError:
        try:
            import fitz  # older PyMuPDF  # noqa: PLC0415
        except Exception as exc:
            raise OcrUnavailable(f"PDF rendering unavailable ({type(exc).__name__})") from exc

    out: list[tuple[str, float]] = []
    with fitz.open(stream=data, filetype="pdf") as pdf:
        zoom = _DPI / 72
        matrix = fitz.Matrix(zoom, zoom)
        for index, page in enumerate(pdf):
            if index >= max_pages:
                break
            pix = page.get_pixmap(matrix=matrix)
            from PIL import Image  # noqa: PLC0415

            import io

            with Image.open(io.BytesIO(pix.tobytes("png"))) as img:
                out.append(_ocr_image(engine, img.convert("RGB")))
    return out
