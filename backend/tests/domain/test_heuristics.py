"""Extraction tests against the shipped synthetic PDFs.

These exist because three real upload bugs slipped through earlier:
1. A `\\b` after a clause number never matched, so a whole page stayed one block and the
   maintenance cap was missed entirely.
2. The first sentence mentioning a keyword was picked, which was usually a heading or
   preamble with no number in it.
3. The amendment's effective date was read from a paragraph that also mentioned the
   *original* lease date, so the wrong date could be attributed to the amendment.
"""
from __future__ import annotations

from pathlib import Path

import re

import pytest

from app.infra import documents, heuristics

SAMPLES = Path(__file__).resolve().parents[3] / "samples"


def load(name: str, kind: str) -> list[str]:
    path = SAMPLES / name
    assert path.exists(), f"missing sample {name}"
    return documents.parse_pages(name, path.read_bytes())


def terms_by_type(parsed: dict) -> dict[str, dict]:
    return {t["term_type"]: t for t in parsed["terms"]}


def _assert_quotes_verbatim(parsed: dict, pages: list[str]) -> None:
    full = "\n".join(pages)
    normalised = " ".join(full.split()).lower()
    for t in parsed["terms"]:
        quote = t.get("source_quote") or ""
        assert " ".join(quote.split()).lower() in normalised, f"quote not verbatim: {quote!r}"


# --- lease PDF -------------------------------------------------------------

def test_lease_pdf_extracts_every_term_with_correct_page():
    pages = load("Lease_Agreement_Shop12_SYNTHETIC.pdf", "lease")
    parsed = heuristics.extract_terms_heuristic(pages, "lease")
    _assert_quotes_verbatim(parsed, pages)
    terms = terms_by_type(parsed)

    assert terms["base_rent_paise"]["value"] == 40_00_000      # Rs 40,000
    assert terms["base_rent_paise"]["page"] == 2
    assert terms["escalation_rate_bp"]["value"] == 1000        # 10%
    assert terms["escalation_rate_bp"]["page"] == 3
    assert terms["maintenance_cap_paise"]["value"] == 4_00_000  # Rs 4,000
    assert terms["maintenance_cap_paise"]["page"] == 4, "cap is on page 4; page 2 means a block merge"
    assert terms["lease_start_date"]["value"] == 20250401


def test_lease_quote_does_not_swallow_the_next_clause():
    """The base-rent quote must not include the maintenance clause from another page."""
    pages = load("Lease_Agreement_Shop12_SYNTHETIC.pdf", "lease")
    parsed = terms_by_type(heuristics.extract_terms_heuristic(pages, "lease"))
    base_quote = parsed["base_rent_paise"]["source_quote"].lower()
    assert "maintenance" not in base_quote
    assert "rs. 40,000" in base_quote


def test_no_term_is_ambiguous_in_a_clean_lease_pdf():
    pages = load("Lease_Agreement_Shop12_SYNTHETIC.pdf", "lease")
    parsed = heuristics.extract_terms_heuristic(pages, "lease")
    assert [t["term_type"] for t in parsed["terms"] if t["ambiguous"]] == []


# --- amendment PDF ---------------------------------------------------------

def test_amendment_pdf_extracts_capped_rate():
    pages = load("Amendment_1_Shop12_SYNTHETIC.pdf", "amendment")
    parsed = heuristics.extract_terms_heuristic(pages, "amendment")
    _assert_quotes_verbatim(parsed, pages)
    terms = terms_by_type(parsed)
    assert terms["escalation_rate_bp"]["value"] == 500         # 5%
    assert terms["escalation_rate_bp"]["page"] == 1
    # The preamble ("This Amendment modifies the escalation clause") has no number and
    # must not win over the operative clause.
    assert "five percent" in terms["escalation_rate_bp"]["source_quote"].lower()


def test_amendment_effective_date_is_the_amendment_not_the_original_lease():
    pages = load("Amendment_1_Shop12_SYNTHETIC.pdf", "amendment")
    parsed = terms_by_type(heuristics.extract_terms_heuristic(pages, "amendment"))
    term = parsed["amendment_effective_date"]
    # 1 January 2026 -> 20260101. 20250401 would be the original lease date.
    assert term["value"] == 20260101
    assert term["value"] != 20250401
    assert "effective date" in term["source_quote"].lower()


# --- invoice PDF -----------------------------------------------------------

def test_invoice_pdf_parses_line_items_and_period():
    pages = load("Invoice_April_2026_SYNTHETIC.pdf", "invoice")
    items = heuristics.parse_invoice_line_items(pages)
    by_kind = {i["kind"]: i["amount_paise"] for i in items}
    assert by_kind["rent"] == 44_00_000        # Rs 44,000 = 4,400,000 paise
    assert by_kind["maintenance"] == 5_20_000  # Rs 5,200 = 520,000 paise
    assert heuristics.parse_period_label("\n".join(pages)) == "April 2026"


def test_invoice_totals_are_not_treated_as_line_items():
    pages = load("Invoice_April_2026_SYNTHETIC.pdf", "invoice")
    descriptions = [i["description"].lower() for i in heuristics.parse_invoice_line_items(pages)]
    assert not any("total due" in d for d in descriptions), descriptions


# --- end-to-end through the extraction router ------------------------------


# --- Hindi documents ---------------------------------------------------------
# Indian shop leases are often written in Hindi. The extractor must read them, and
# must still quote the document verbatim (a rewritten quote would break BR6).

HINDI_LEASE = """दुकान किरायानामा
यह पट्टा दिनांक 1 अप्रैल 2025 से प्रभावी है।
धारा 1: मासिक किराया 40,000 रुपये प्रति माह है।
धारा 2: किराये में प्रत्येक वर्ष 10 प्रतिशत की वृद्धि होगी।
धारा 3: रखरखाव शुल्क 4,000 रुपये प्रति माह से अधिक नहीं होगा।
"""


def test_hindi_lease_terms_are_extracted():
    from app.infra.heuristics import extract_terms_heuristic

    result = extract_terms_heuristic([HINDI_LEASE], "lease")
    values = {t["term_type"]: t["value"] for t in result["terms"]}
    assert values["base_rent_paise"] == 4_000_000       # Rs 40,000 in paise
    assert values["escalation_rate_bp"] == 1000         # 10%
    assert values["escalation_cadence_months"] == 12
    assert values["maintenance_cap_paise"] == 400_000   # Rs 4,000
    assert values["lease_start_date"] == 20250401


def test_hindi_quotes_stay_verbatim():
    """The danda must survive: a quote is evidence, not a rendering."""
    from app.infra.extraction import validate_quote
    from app.infra.heuristics import extract_terms_heuristic

    result = extract_terms_heuristic([HINDI_LEASE], "lease")
    flat = " ".join(HINDI_LEASE.split())
    for term in result["terms"]:
        if term["source_quote"]:
            assert term["source_quote"] in flat, term["source_quote"]
            assert validate_quote(term["source_quote"], HINDI_LEASE)


def test_hindi_amount_words_and_digits():
    from app.infra.heuristics import parse_rupees

    assert parse_rupees("रुपये चालीस हज़ार मात्र") == 4_000_000
    assert parse_rupees("रखरखाव शुल्क 4,000 रुपये") == 400_000
    assert parse_rupees("किराया ४४,००० रुपये") == 4_400_000   # Devanagari digits


def test_hindi_percent_and_date():
    from app.infra.heuristics import _find_date, parse_percent_bp

    assert parse_percent_bp("किराये में 10 प्रतिशत की वृद्धि") == 1000
    assert parse_percent_bp("५ प्रतिशत") == 500          # Devanagari digit
    assert _find_date("यह पट्टा दिनांक 1 अप्रैल 2025 से प्रभावी है") == 20250401


def test_plain_line_delimited_lease_is_not_misfiled():
    """One clause per line, no blank lines, no numbering, no full stops.

    This shape previously collapsed into a single block, so the first amount on the
    page (the rent) was returned as the maintenance cap: Rs 40,000 instead of Rs 4,000.
    A wrong number is worse than a missing one, so this is pinned.
    """
    from app.infra.heuristics import extract_terms_heuristic

    doc = (
        "LEASE\n"
        "Monthly Rent of Rs. 40,000\n"
        "Rent increases 10 percent per annum on each anniversary\n"
        "Maintenance capped at Rs. 4,000 per month\n"
        "Commencement Date: 1 April 2025"
    )
    values = {t["term_type"]: t["value"] for t in extract_terms_heuristic([doc], "lease")["terms"]}
    assert values["base_rent_paise"] == 4_000_000      # Rs 40,000
    assert values["maintenance_cap_paise"] == 400_000  # Rs 4,000  (not Rs 40,000)
    assert values["escalation_rate_bp"] == 1000
    assert values["lease_start_date"] == 20250401




def test_quotes_are_complete_clauses_not_wrapped_fragments():
    """Evidence must not stop mid-word.

    A hard-wrapped PDF line used to win the scoring tie and the UI showed
    "... capped at Rs. 4,000 (Rupees Four" — a quote that visibly cuts off mid-word,
    which undermines the whole point of evidence-backed findings.
    """
    pages = load("Lease_Agreement_Shop12_SYNTHETIC.pdf", "lease")
    parsed = heuristics.extract_terms_heuristic(pages, "lease")
    for term in parsed["terms"]:
        quote = term["source_quote"]
        if not quote:
            continue
        # The last word must be a whole word present in the document. Labelled metadata
        # lines ("Commencement Date: 1 April 2025") legitimately carry no full stop, so
        # the rule is "not cut mid-word", not "always ends in punctuation".
        assert not quote.endswith("-"), f"quote cut on a hyphen: {quote[-30:]!r}"
        last_word = quote.split()[-1].strip(".;:।!?")
        assert last_word and last_word in " ".join(pages), (
            f"quote appears truncated mid-word: ...{quote[-40:]!r}"
        )


def test_page_wide_block_does_not_impersonate_a_clause():
    """A block stacking several amounts must not satisfy a single-term lookup."""
    from app.infra.heuristics import extract_terms_heuristic

    doc = (
        "LEASE\n"
        "Monthly Rent of Rs. 40,000\n"
        "Maintenance capped at Rs. 4,000 per month\n"
        "Security deposit Rs. 1,20,000\n"
    )
    values = {t["term_type"]: t["value"] for t in extract_terms_heuristic([doc], "lease")["terms"]}
    assert values["base_rent_paise"] == 4_000_000
    assert values["maintenance_cap_paise"] == 400_000
