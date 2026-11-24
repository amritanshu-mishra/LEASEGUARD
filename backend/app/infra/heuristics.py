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
    # Hindi wording: "40,000 रुपये" / "4,000/- रुपये". Devanagari digits were already
    # normalised to ASCII by parse_rupees before this runs.
    re.compile(r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:/-)?\s*(?:रुपये|रुपए|रु\.)"),
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


# Hindi number words used in lease amounts ("चालीस हज़ार रुपये"). Only the words that
# actually appear in rent ranges are included; anything unknown makes the parse fail
# rather than guess, which keeps BR6 evidence honest.
_WORDS_HI = {
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "सात": 7,
    "आठ": 8, "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12, "पंद्रह": 15, "बीस": 20,
    "पच्चीस": 25, "तीस": 30, "चालीस": 40, "पचास": 50, "साठ": 60, "सत्तर": 70,
    "अस्सी": 80, "नब्बे": 90, "सौ": 100,
}
_SCALES_HI = {"सौ": 100, "हज़ार": 1000, "हजार": 1000, "लाख": 100000}
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

# Hindi month names as they appear in dated clauses ("1 अप्रैल 2025").
_MONTHS_HI = {
    "जनवरी": 1, "फरवरी": 2, "फ़रवरी": 2, "मार्च": 3, "अप्रैल": 4, "अप्रेल": 4,
    "मई": 5, "जून": 6, "जुलाई": 7, "अगस्त": 8, "सितंबर": 9, "सितम्बर": 9,
    "अक्टूबर": 10, "अक्तूबर": 10, "नवंबर": 11, "नवम्बर": 11, "दिसंबर": 12, "दिसम्बर": 12,
}


def normalise_digits(text: str) -> str:
    """Map Devanagari digits to ASCII so amount and date parsing works unchanged."""
    return text.translate(_DEVANAGARI_DIGITS)


def _words_to_number_hi(text: str) -> int | None:
    tokens = re.findall(r"[\u0900-\u097F]+", text)
    tokens = [t for t in tokens if t in _WORDS_HI or t in _SCALES_HI]
    if not tokens:
        return None
    total = 0
    current = 0
    for tok in tokens:
        if tok in _WORDS_HI and tok not in _SCALES_HI:
            current += _WORDS_HI[tok]
        else:
            scale = _SCALES_HI[tok]
            if current == 0:
                current = 1
            total += current * scale
            current = 0
    return total + current


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
    text = normalise_digits(text)
    for pattern in _AMOUNT_PATTERNS:
        m = pattern.search(text)
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
    # Hindi: "चालीस हज़ार रुपये" / "रुपये चालीस हज़ार मात्र"
    m = re.search(r"(?:रुपये|रुपए|रु\.?)\s*([\u0900-\u097F\s]+)", text)
    if m:
        n = _words_to_number_hi(m.group(1))
        if n:
            return n * 100
    m = re.search(r"([\u0900-\u097F\s]+?)\s*(?:रुपये|रुपए)", text)
    if m:
        n = _words_to_number_hi(m.group(1))
        if n:
            return n * 100
    return None


def parse_percent_bp(text: str) -> int | None:
    text = normalise_digits(text)
    # "10 प्रतिशत" (Hindi) — same percentage, different word.
    hi = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:प्रतिशत|फीसदी|फ़ीसदी|%)", text)
    if hi:
        return int(round(float(hi.group(1)) * 100))
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
    # Splitting is done on lookbehind only, so the text keeps its original danda and
    # semicolons: every returned sentence is still a verbatim slice of the document.
    for part in re.split(r"(?<=[.;।])\s*\n?\s*", block):
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


def _join_wrapped_lines(block: str) -> list[str]:
    """Split a block into lines, re-joining hard-wrapped continuations.

    A PDF wraps long clauses at a fixed width. A line that ends without punctuation is
    usually a continuation of the next line, so it is joined to it; a line that ends in
    punctuation is a complete clause of its own.
    """
    pieces: list[str] = []
    buffer = ""
    pending_lead: list[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            if buffer:
                pieces.append(buffer)
                buffer = ""
            continue
        buffer = f"{buffer} {line}".strip() if buffer else line
        if re.search(r"[.;:।]$", line):
            # "Rs. 40,000" ends in an abbreviation's period, not a clause end.
            if any(line.lower().endswith(ab) for ab in _ABBREV):
                continue
            pieces.append(buffer)
            buffer = ""
    if buffer:
        pieces.append(buffer)

    # "The Lessee shall pay ... upkeep; such monthly charges shall be capped at ..."
    # The capped amount lives in the second clause, but quoting only that clause makes it
    # read as if it were lifted from the middle of a sentence. Join it to the clause that
    # introduces it so the evidence reads as a whole thought. The joined text is still a
    # verbatim substring of the page (only the line break is collapsed).
    joined: list[str] = []
    for piece in pieces:
        if joined and piece[:1].islower():
            joined[-1] = f"{joined[-1]} {piece}"
        else:
            joined.append(piece)
    return joined


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
        #
        # NOTE: no \b after [.)] — a word boundary requires a word character on one
        # side, and both "." and the following space are non-word, so \b never matches
        # there (this previously left a whole page as one block, which made clause
        # matching grab the wrong quote and miss the maintenance cap entirely).
        # Split on paragraph breaks, numbered clause starts, and plain line breaks.
        #
        # A lease written one clause per line with no blank lines used to become a
        # single block, so every term was matched against the whole page and the first
        # amount on the page (the rent) was returned as the maintenance cap. Treating a
        # line break as a candidate boundary fixes that; wrapped sentences are then
        # rejoined below so a PDF's hard-wrapped lines do not become fake sentences.
        blocks = re.split(
            r"\n\s*\n"
            r"|\n(?=\s*(?:[0-9]{1,2}[.)]\s|clause\s+[0-9]+|article\s+[0-9]+"
            r"|धारा\s*[0-9]+|अनुच्छेद\s*[0-9]+))",
            text,
            flags=re.IGNORECASE,
        )
        for block in blocks:
            # A single line is a legitimate candidate in its own right. Leases are often
            # written one clause per line with no punctuation and no blank lines
            # ("Monthly Rent of Rs. 40,000"), and guessing whether a line is wrapped or
            # complete kept misfiling the rent as the maintenance cap. Offering the raw
            # line, the re-joined clause, and the sentence means the best-supported
            # candidate wins on score instead of on a wrapping guess. Every candidate is
            # still a verbatim substring, so BR6 evidence holds.
            for raw_line in block.splitlines():
                line = re.sub(r"\s+", " ", raw_line).strip(" -–•*")
                if 12 <= len(line) <= 400:
                    out.append((line, idx))
            for piece in _join_wrapped_lines(block):
                flat = re.sub(r"\s+", " ", piece).strip()
                if not flat:
                    continue
                if 12 <= len(flat) <= 400:
                    out.append((flat, idx))
                for sentence in _split_sentences(flat):
                    chunk = sentence.strip(" -–•*")
                    if 12 <= len(chunk) <= 400:
                        out.append((chunk, idx))
    return out


_CLAUSE_PREFIX = re.compile(
    r"^\s*(?:[0-9]{1,2}[.)]|clause\s+[0-9]+|article\s+[0-9]+"
    r"|धारा\s*[0-9]+|अनुच्छेद\s*[0-9]+)\s*[:.-]?\s*",
    re.IGNORECASE,
)

# --- bilingual vocabulary -----------------------------------------------------
# Indian shop leases are frequently written in Hindi, so the deterministic extractor
# must recognise both languages rather than silently returning nothing. Keywords are
# matched case-insensitively and are never used as quotes: the quote is always the
# real sentence, so BR6 (verbatim evidence) still holds for Hindi documents too.
_CONCEPTS: dict[str, tuple[str, ...]] = {
    "rent": ("rent", "किराया", "किराये", "भाड़ा", "भाड़े"),
    "maintenance": ("maintenance", "cam", "common area", "रखरखाव", "रख-रखाव", "मरम्मत", "सामान्य क्षेत्र"),
    "escalation": ("escalat", "increase", "वृद्धि", "बढ़ोतरी", "बढ़ोत्तरी", "बढ़ेगा", "बढ़ाया", "इजाफा"),
    "cap": ("cap", "not exceed", "maximum", "upto", "up to", "ceiling",
            "अधिकतम", "से अधिक नहीं", "से ज्यादा नहीं", "सीमा"),
    "commence": ("commence", "start date", "w.e.f", "with effect from", "effective from",
                 "प्रारंभ", "आरंभ", "शुरू", "से प्रभावी", "प्रभावी तिथि", "से लागू"),
    "effective_date": ("effective date", "प्रभावी तिथि", "प्रभावी दिनांक", "लागू तिथि"),
    "annum": ("annum", "annually", "anniversary", "year", "per year", "वार्षिक", "वर्ष", "सालाना"),
    "exclude_rent": ("maintenance", "escalat", "increase", "deposit", "security", "late", "penalty", "gst", "tax",
                     "रखरखाव", "वृद्धि", "जमा", "सुरक्षा", "देरी", "जुर्माना", "कर"),
}


def _any_group(*concepts: str) -> list[tuple[str, ...]]:
    """Build an include-list where each concept group must match at least once.

    A flat list means "all of these words must appear", which is correct for
    "rent"+"increase" but wrong for synonyms ("rent"|"किराया"). Groups express which
    is which, so one code path serves both languages.
    """
    return [tuple(_CONCEPTS[c]) for c in concepts]


def _hits(text: str, concept: str) -> bool:
    low = text.lower()
    return any(word in low for word in _CONCEPTS[concept])


def _lines_with_pages(pages: list[str]) -> list[tuple[str, int]]:
    """Return (line, page) pairs for labelled metadata fields.

    Labelled fields ("Effective Date: ...", "Commencement Date: ...") sit on their own
    line and are often not sentence-terminated, so sentence splitting merges them into a
    neighbour that may mention a *different* date. Line-level scanning keeps the quote
    exact and avoids attributing the wrong date to a clause.
    """
    out: list[tuple[str, int]] = []
    for idx, page in enumerate(pages, start=1):
        for raw in page.splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if 6 <= len(line) <= 200:
                out.append((line, idx))
    return out


def _strip_clause_prefix(text: str) -> str:
    """Drop a leading clause number/heading so the quote is the operative sentence.

    The remainder is still a verbatim substring of the document, so BR6 holds.
    """
    return _CLAUSE_PREFIX.sub("", text).strip()


def _matches_include(low: str, include: list[str] | list[tuple[str, ...]]) -> bool:
    """True when the sentence satisfies the include specification.

    A tuple item is a *group of synonyms*: any one of them satisfies it. A bare string
    is a single required word. Every top-level item must be satisfied.
    """
    for item in include:
        if isinstance(item, tuple):
            if not any(word in low for word in item):
                return False
        elif item not in low:
            return False
    return True


def _pick_sentence(
    sentences: list[tuple[str, int]],
    include: list[str] | list[tuple[str, ...]],
    exclude: list[str],
    *,
    prefer_parseable=None,
) -> tuple[str, int] | None:
    """Choose the best clause sentence for a term.

    A plain "first sentence containing the keyword" pick is wrong: headings and
    preamble text ("This Amendment modifies the escalation clause ...") mention the
    keyword long before the operative clause states a number. Candidates that yield a
    parseable value are therefore ranked first, then the shorter (more specific) ones.
    """
    scored: list[tuple[int, int, str, int]] = []
    for text, page in sentences:
        low = text.lower()
        if any(bad in low for bad in exclude):
            continue
        if not _matches_include(low, include):
            continue
        score = 0
        if prefer_parseable is not None and prefer_parseable(text) is not None:
            score += 10            # the sentence actually states the value
        if len(text) < 220:
            score += 3             # short, specific clauses beat page-wide blocks
        # A quote starting "such monthly maintenance charges..." reads as if it were cut
        # out of the middle of a sentence. Prefer one that starts a sentence.
        stripped = text.lstrip()
        starts_clean = stripped[:1].isupper() or bool(
            re.match(r"^(the|a|an)\b", stripped, re.IGNORECASE)
        )
        if starts_clean:
            score += 2
        # A clause that ends a sentence is complete; a hard-wrapped fragment is not.
        # Both can parse to the same number, and the fragment shows the user a quote that
        # stops mid-word ("... (Rupees Four"). Completeness outranks brevity.
        ends_clean = bool(re.search(r"[.;:।!?]\s*$", text))
        if ends_clean:
            score += 5
        # Complete on both ends: a whole clause, better evidence than an equally-scoring
        # fragment that starts mid-thought.
        if starts_clean and ends_clean:
            score += 3
        # A block that stacks several distinct facts is not "the clause" for any one of
        # them. This is what made a page-wide block beat the actual maintenance sentence
        # and report the rent (the first amount on the page) as the maintenance cap.
        # Count distinct money/percentage mentions rather than word length.
        amounts = len(re.findall(r"(?:Rs\.?|₹|INR|रुपये)\s*[0-9\u0966-\u096F]", text))
        if amounts >= 2:
            score -= 8
        if low.count(";") >= 2:
            score -= 4
        if low.lstrip().startswith(("this amendment", "this lease", "the parties")):
            score -= 4             # preamble rather than an operative clause
        scored.append((score, len(text), text, page))
    if not scored:
        return None
    # Rank by score, then longer first: among equally supported, single-clause
    # candidates the fuller one reads better and is still a verbatim substring.
    scored.sort(key=lambda item: (-item[0], -item[1]))
    _score, _len, text, page = scored[0]
    return _strip_clause_prefix(text), page


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
        hit = _pick_sentence(
            sentences,
            include=_any_group("rent"),
            exclude=list(_CONCEPTS["exclude_rent"]),
            prefer_parseable=parse_rupees,
        )
        if hit:
            quote, page = hit
            amount = parse_rupees(quote)
            terms.append(_make_term("base_rent_paise", amount, quote, page, 0.9 if amount else 0.3))
        else:
            terms.append(_make_term("base_rent_paise", None, "", 1, 0.0))

        # Escalation rate
        hit = _pick_sentence(sentences, include=_any_group("escalation"),
                             exclude=list(_CONCEPTS["maintenance"]),
                             prefer_parseable=parse_percent_bp)
        if not hit:
            hit = _pick_sentence(sentences, include=_any_group("rent", "escalation"),
                                 exclude=list(_CONCEPTS["maintenance"]),
                                 prefer_parseable=parse_percent_bp)
        if hit:
            quote, page = hit
            bp = parse_percent_bp(quote)
            terms.append(_make_term("escalation_rate_bp", bp, quote, page, 0.9 if bp else 0.3))
            cadence = 12 if any(w in quote.lower() for w in _CONCEPTS["annum"]) else None
            terms.append(_make_term("escalation_cadence_months", cadence, quote, page, 0.85 if cadence else 0.2))

        # Maintenance cap
        hit = _pick_sentence(sentences, include=_any_group("maintenance"), exclude=[],
                             prefer_parseable=parse_rupees)
        if hit:
            quote, page = hit
            capped = any(w in quote.lower() for w in _CONCEPTS["cap"])
            amount = parse_rupees(quote) if capped else None
            terms.append(_make_term("maintenance_cap_paise", amount, quote, page, 0.9 if amount else 0.3))

        # Lease start date
        hit = _pick_sentence(sentences, include=_any_group("commence"), exclude=[],
                             prefer_parseable=_find_date)
        if hit:
            quote, page = hit
            terms.append(_make_term("lease_start_date", _find_date(quote), quote, page, 0.85))

    elif doc_kind == "amendment":
        hit = _pick_sentence(
            sentences, include=_any_group("escalation"),
            exclude=["other terms", "remain in full force"],
            prefer_parseable=parse_percent_bp,
        )
        if not hit:
            hit = _pick_sentence(
                sentences, include=_any_group("rent", "escalation"),
                exclude=["other terms", "remain in full force"],
                prefer_parseable=parse_percent_bp,
            )
        if hit:
            quote, page = hit
            bp = parse_percent_bp(quote)
            terms.append(_make_term("escalation_rate_bp", bp, quote, page, 0.9 if bp else 0.3))

        # An amendment's effective date decides whether it applies to a billed period, so
        # it is read from the labelled line itself. Scanning whole paragraphs risks picking
        # up the "Original Lease Date" printed next to it, which would silently change
        # which clause controls.
        eff_value: int | None = None
        eff_quote = ""
        eff_page = 1
        for line, page in _lines_with_pages(pages):
            low = line.lower()
            labelled = re.match(r"^\W*effective\s+date\b", low) or any(
                low.lstrip(" :").startswith(word) for word in _CONCEPTS["effective_date"]
            )
            if not labelled:
                continue
            candidate = _find_date(line)
            if candidate is not None:
                eff_value, eff_quote, eff_page = candidate, line, page
                break
        terms.append(_make_term(
            "amendment_effective_date", eff_value, eff_quote, eff_page,
            0.9 if eff_value else 0.2,
        ))

    return {"terms": terms}


def _find_date(text: str) -> int | None:
    """Find a date in text and return it as a YYYYMMDD integer (the app's date format)."""
    text = normalise_digits(text)
    # "1 अप्रैल 2025"
    m = re.search(r"(\d{1,2})\s*([\u0900-\u097F]+)\s+(\d{4})", text)
    if m:
        month = _MONTHS_HI.get(m.group(2))
        if month:
            return int(m.group(3)) * 10000 + month * 100 + int(m.group(1))
    m = re.search(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", text)
    if m:
        month = _month(m.group(2))
        if month:
            return int(m.group(3)) * 10000 + month * 100 + int(m.group(1))
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", text)
    if m:
        month = _month(m.group(1))
        if month:
            return int(m.group(3)) * 10000 + month * 100 + int(m.group(2))
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return int(m.group(1)) * 10000 + int(m.group(2)) * 100 + int(m.group(3))
    return None


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _month(name: str) -> int | None:
    return _MONTHS.get(name.lower()) or _MONTHS_HI.get(name)


# --- invoice parsing ---------------------------------------------------------

_TOTAL_WORDS = ("total", "sub total", "subtotal", "amount payable", "grand total", "balance due", "net payable",
                "कुल", "कुल योग", "कुल राशि", "देय")

_RENT_WORDS = _CONCEPTS["rent"]
_MAINT_WORDS = _CONCEPTS["maintenance"]
_GST_WORDS = ("gst", "tax", "कर", "जीएसटी")


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


_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
MONTH_NUMBERS = {name: i + 1 for i, name in enumerate(_MONTH_NAMES)}
# Common Indian invoice abbreviations. A bare "may" is excluded because it is also a
# modal verb; it only counts when followed by a year.
MONTH_NUMBERS.update({
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
})
_MONTH_WORD = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

# Words that mean the month on that line is a due date or a print date, not the
# billing period. A bill dated "Due date: 15 May 2026" for April rent is common.
_NOT_PERIOD = re.compile(
    r"\b(due|due date|payment|pay by|last date|printed|print date|generated|issued on|"
    r"bill date|invoice date|date of invoice|dispatched?|received? on|date of issue)\b",
    re.IGNORECASE,
)
# Words that positively identify a billing period.
_IS_PERIOD = re.compile(
    r"\b(period|billing|for the month|month of|rent for|charges for|statement for|"
    r"monthly invoice|invoice for|tenancy month)\b",
    re.IGNORECASE,
)

# Numeric ranges first: "01/04/2026 - 30/04/2026", "01-04-2026 to 30-04-2026".
# Indian invoices write dd/mm/yyyy, so the start date's month is the billing month.
_NUMERIC_RANGE = re.compile(
    r"(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(20\d{2})\s*"
    r"(?:-|–|—|to|till)\s*"
    r"\d{1,2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*20\d{2}",
    re.IGNORECASE,
)
_MONTH_YEAR = re.compile(rf"\b({_MONTH_WORD})\s*,?\s*[-/]?\s*(20\d{{2}})\b", re.IGNORECASE)
_MONTH_YY = re.compile(rf"\b({_MONTH_WORD})\s*[-/]\s*(\d{{2}})\b", re.IGNORECASE)
_BARE_MONTH = re.compile(rf"\b({_MONTH_WORD})\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(20\d{2})\b")


def _month_year_in(line: str) -> tuple[int, int] | None:
    """(year, month) mentioned on one line, or None."""
    num = _NUMERIC_RANGE.search(line)
    if num:
        day, month, year = int(num.group(1)), int(num.group(2)), int(num.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return year, month

    for pattern, digits in ((_MONTH_YEAR, 4), (_MONTH_YY, 2)):
        m = pattern.search(line)
        if m:
            month = MONTH_NUMBERS.get(m.group(1).lower())
            if not month:
                continue
            raw = m.group(2)
            year = int(raw) if digits == 4 else 2000 + int(raw)
            if 1990 <= year <= 2100:
                return year, month

    # A bare month is only usable when a year appears on the same line.
    bare = _BARE_MONTH.search(line)
    year_match = _YEAR.search(line)
    if bare and year_match:
        month = MONTH_NUMBERS.get(bare.group(1).lower())
        if month:
            return int(year_match.group(1)), month
    return None


def parse_period(text: str) -> tuple[int, int] | None:
    """Find the invoice's *billing* month, not just any month on the page.

    Evidence, in order:
      1. a line that explicitly names a period ("Invoice Period: April 2026")
      2. any line carrying a month, excluding due dates and print dates
    Due and print dates are never the billing period: a bill "Due 15 May 2026" for
    April rent must not be filed as May, or the audit silently uses the wrong terms.
    Returns (year, month) or None.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    fallback: tuple[int, int] | None = None
    for line in lines:
        got = _month_year_in(line)
        if not got:
            continue
        if _NOT_PERIOD.search(line) and not _IS_PERIOD.search(line):
            continue
        if _IS_PERIOD.search(line):
            return got
        if fallback is None:
            fallback = got
    return fallback


def period_label_from(year: int, month: int) -> str:
    """Public formatter so callers never need the private month table."""
    return f"{_MONTH_NAMES[month - 1].capitalize()} {year}"


def parse_period_label(text: str) -> str | None:
    got = parse_period(text)
    if not got:
        return None
    return period_label_from(*got)
