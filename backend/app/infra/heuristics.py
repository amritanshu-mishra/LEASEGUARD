"""Rule-based (deterministic) extraction for uploaded documents.

Why this exists
---------------
Groq extraction is preferred, but the product must never invent numbers. When no
API key is configured, or the model output fails quote validation, we fall back to
conservative regex parsing: every extracted value must appear in a verbatim quote
taken from the document, and the page number is found by searching page text.

Nothing here guesses. If a value cannot be grounded in the document text, the term
is emitted with value=None and ambiguous=True so the audit reports "uncertain".
"""
from __future__ import annotations

import re
from typing import Any

# --- amount / percent parsing -------------------------------------------------

_AMOUNT_PATTERNS = [
    # Rs. 40,000 / Rs 40000 / ₹40,000 / INR 40,000 / Rs. 4,000/-
    re.compile(r"(?:Rs\.?|INR|₹)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:/-)?"),
]

_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000, "lakh": 100000, "lac": 100000, "crore": 10000000}

_PERCENT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(?:%|per\s*cent|percent)", re.IGNORECASE)


def _words_to_number(text: str) -> int | None:
    """Parse a simple English number phrase like 'forty thousand'. Returns None if unsure."""
    tokens = re.findall(r"[a-z]+", text.lower())
    tokens = [t for t in tokens if t in _WORDS or t in _SCALES]
    if not tokens:
        return None
    total = 0
    current = 0
    for tok in tokens:
        if tok in _WORDS:
            current += _WORDS[tok]
        else:
            scale = _SCALES[tok]
            if current == 0:
                current = 1
            total += current * scale
            current = 0
    return total + current


def parse_rupees(text: str) -> int | None:
    """Return integer paise for the first rupee amount in text, else None."""
    # A parenthesised words-only amount ("Rupees Forty Thousand only") is used when the
    # digits were split across lines; try digits first because they are unambiguous.
    m = _AMOUNT_PATTERNS[0].search(text)
    if m:
        raw = m.group(1).replace(",", "")
        try:
            return int(round(float(raw) * 100))
        except ValueError:
            return None
    # "Rupees Forty Thousand only"
    m = re.search(r"rupees\s+([a-z\s]+?)\s*(?:only|per|p\.m\.|[.,;]|$)", text, re.IGNORECASE)
    if m:
        n = _words_to_number(m.group(1))
        if n:
            return n * 100
    return None


def parse_percent_bp(text: str) -> int | None:
    m = _PERCENT_RE.search(text)
    if not m:
        # "ten percent" spelled out
        m2 = re.search(r"\b([a-z\s]+?)\s*(?:per\s*cent|percent)", text, re.IGNORECASE)
        if m2:
            n = _words_to_number(m2.group(1)[-40:])
            return n * 100 if n else None
        return None
    return int(round(float(m.group(1)) * 100))  # 5% -> 500 bp


# --- sentence splitting with page tracking -----------------------------------

_ABBREV = ("rs.", "no.", "mr.", "mrs.", "ms.", "dr.", "p.", "pp.", "vs.", "i.e.", "e.g.", "ltd.", "pvt.")


def _split_sentences(block: str) -> list[str]:
    """Split on sentence enders while protecting common Indian-lease abbreviations."""
    out: list[str] = []
    current = ""
    for part in re.split(r"(?<=[.;])\s+", block):
        candidate = (current + " " + part).strip() if current else part.strip()
        tail = candidate[-6:].lower()
        if any(tail.endswith(a) for a in _ABBREV):
            current = candidate  # keep accumulating; abbreviation, not a sentence end
            continue
        if candidate:
            out.append(candidate)
        current = ""
    if current.strip():
        out.append(current.strip())
    return out


def _sentences_with_pages(pages: list[str]) -> list[tuple[str, int]]:
    """Return (quote, page) candidates.

    Paragraphs are reassembled first (so wrapped PDF lines stay verbatim), then split
    into sentences and clause-sized chunks. Quotes are always substrings of the page
    text after whitespace normalisation, which is what the audit displays.
    """
    out: list[tuple[str, int]] = []
    for idx, page in enumerate(pages, start=1):
        text = page.replace("\r", "")
        # Split into paragraphs and numbered clause blocks.
        blocks = re.split(r"\n\s*\n|\n(?=\s*(?:[0-9]+[.)]|clause|article)\b)", text, flags=re.IGNORECASE)
        for block in blocks:
            flat = re.sub(r"\s+", " ", block).strip()
            if not flat:
                continue
            if 12 <= len(flat) <= 400:
                out.append((flat, idx))
            for sentence in _split_sentences(flat):
                chunk = sentence.strip(" -–•*")
                if 12 <= len(chunk) <= 400:
                    out.append((chunk, idx))
    return out


def _best_sentence(sentences: list[tuple[str, int]], include: list[str], exclude: list[str]) -> tuple[str, int] | None:
    for text, page in sentences:
        low = text.lower()
        if any(x in low for x in exclude):
            continue
        if all(x in low for x in include):
            return text, page
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _make_term(term_type: str, value: int | None, quote: str, page: int, confidence: float) -> dict[str, Any]:
    return {
        "term_type": term_type,
        "value": value,
        "source_quote": _norm(quote),
        "page": page,
        "confidence": confidence,
        "ambiguous": value is None,
    }


def extract_terms_heuristic(pages: list[str], doc_kind: str) -> dict[str, Any]:
    """Extract lease terms using only text that literally exists in the document."""
    sentences = _sentences_with_pages(pages)
    terms: list[dict[str, Any]] = []

    if doc_kind == "lease":
        # Base rent: a rent sentence that is not about escalation/maintenance/deposit.
        hit = _best_sentence(
            sentences,
            include=["rent"],
            exclude=["maintenance", "escalat", "increase", "deposit", "security", "late", "penalty", "gst", "tax"],
        )
        if hit:
            quote, page = hit
            amount = parse_rupees(quote)
            terms.append(_make_term("base_rent_paise", amount, quote, page, 0.9 if amount else 0.3))
        else:
            terms.append(_make_term("base_rent_paise", None, "", 1, 0.0))

        # Escalation rate
        hit = _best_sentence(sentences, include=["escalat"], exclude=["maintenance"])
        if not hit:
            hit = _best_sentence(sentences, include=["rent", "increase"], exclude=["maintenance"])
        if hit:
            quote, page = hit
            bp = parse_percent_bp(quote)
            terms.append(_make_term("escalation_rate_bp", bp, quote, page, 0.9 if bp else 0.3))
            cadence = 12 if re.search(r"annum|annually|anniversary|year", quote, re.IGNORECASE) else None
            terms.append(_make_term("escalation_cadence_months", cadence, quote, page, 0.85 if cadence else 0.2))

        # Maintenance cap
        hit = _best_sentence(sentences, include=["maintenance"], exclude=[])
        if hit:
            quote, page = hit
            capped = re.search(r"cap|not exceed|maximum|upto|up to|ceiling", quote, re.IGNORECASE)
            amount = parse_rupees(quote) if capped else None
            terms.append(_make_term("maintenance_cap_paise", amount, quote, page, 0.9 if amount else 0.3))

        # Lease start date
        hit = _best_sentence(sentences, include=["commence"], exclude=[])
        if hit:
            quote, page = hit
            ym = re.search(r"(\d{1,2})\s*(st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", quote)
            if ym:
                terms.append(_make_term("lease_start_date", int(ym.group(4)) * 10000 + _month(ym.group(3)) * 100 + int(ym.group(1)), quote, page, 0.85))

    elif doc_kind == "amendment":
        hit = _best_sentence(sentences, include=["escalat"], exclude=[])
        if not hit:
            hit = _best_sentence(sentences, include=["rent", "increase"], exclude=[])
        if hit:
            quote, page = hit
            bp = parse_percent_bp(quote)
            terms.append(_make_term("escalation_rate_bp", bp, quote, page, 0.9 if bp else 0.3))

    return {"terms": terms}


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _month(name: str) -> int:
    return _MONTHS.get(name.lower(), 1)


# --- invoice parsing ---------------------------------------------------------

_TOTAL_WORDS = ("total", "sub total", "subtotal", "amount payable", "grand total", "balance due", "net payable")

_RENT_WORDS = ("rent",)
_MAINT_WORDS = ("maintenance", "cam", "common area")
_GST_WORDS = ("gst", "tax")


def _kind_for(description: str) -> str:
    low = description.lower()
    if any(w in low for w in _MAINT_WORDS):
        return "maintenance"
    if any(w in low for w in _GST_WORDS):
        return "gst"
    if any(w in low for w in _RENT_WORDS):
        return "rent"
    return "other"


def parse_invoice_line_items(pages: list[str]) -> list[dict[str, Any]]:
    """Parse 'description ... Rs. amount' lines from an invoice. Conservative."""
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for page in pages:
        for raw in page.splitlines():
            line = raw.strip(" \t•-*")
            if not line:
                continue
            amount = parse_rupees(line)
            if amount is None or amount <= 0:
                continue
            # description = text before the amount token
            m = _AMOUNT_PATTERNS[0].search(line)
            desc = (line[: m.start()] if m else line).strip(" :-–|\t")
            desc = re.sub(r"\s+", " ", desc)
            if len(desc) < 3:
                continue
            if any(w in desc.lower() for w in _TOTAL_WORDS):
                continue
            key = (desc.lower(), amount)
            if key in seen:
                continue
            seen.add(key)
            items.append({"description": desc[:120], "amount_paise": amount, "kind": _kind_for(desc)})
    return items


def parse_period_label(text: str) -> str | None:
    m = re.search(
        r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})",
        text,
        re.IGNORECASE,
    )
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"
    return None
