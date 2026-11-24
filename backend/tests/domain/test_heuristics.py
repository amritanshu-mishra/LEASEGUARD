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
