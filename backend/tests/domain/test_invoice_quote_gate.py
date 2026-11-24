"""Billed amounts get the same quote gate as lease terms.

Before this, a misread invoice figure went straight into the audit. Lease terms
were verified against the document; the numbers that trigger the finding were
not - so the most consequential input was the least checked one.
"""
from __future__ import annotations

import pytest

from app.infra import extraction, heuristics

# One string per PAGE, which is how parse_pages returns them. Building this as
# a list of lines instead would mis-attribute pages and make a page assertion
# meaningless.
INVOICE = [
    "MONTHLY RENT INVOICE\nInvoice Period: April 2026\n"
    "Monthly Rent Rs. 44,000\nCommon Area Maintenance Rs. 5,200\nTotal Rs. 49,200"
]


def test_verified_invoice_has_no_unverified_items():
    result = extraction.extract_invoice(INVOICE)
    assert result["unverified_items"] == []
    assert all(item["verified"] is True for item in result["line_items"])
    assert all(item["confidence"] == 1.0 for item in result["line_items"])


def test_line_items_carry_the_text_they_came_from():
    """Without a quote there is nothing to verify, so the quote is the feature."""
    result = extraction.extract_invoice(INVOICE)
    rent = next(i for i in result["line_items"] if i["kind"] == "rent")
    assert "44,000" in rent["source_quote"]
    assert rent["page"] == 1


def test_misread_amount_is_flagged_not_audited(monkeypatch):
    """The failure this exists to catch: a wrong number that looks confident."""
    real = heuristics.parse_invoice_line_items

    def wrong_amount(pages):
        items = real(pages)
        for item in items:
            if item["kind"] == "maintenance":
                item["amount_paise"] = 999_999  # not in the document
        return items

    monkeypatch.setattr(heuristics, "parse_invoice_line_items", wrong_amount)
    result = extraction.extract_invoice(INVOICE)

    assert result["unverified_items"] == ["Common Area Maintenance"]
    bad = next(i for i in result["line_items"] if i["kind"] == "maintenance")
    assert bad["verified"] is False
    assert bad["confidence"] == 0.0
    # The good line is untouched - the gate is per item, not all-or-nothing.
    good = next(i for i in result["line_items"] if i["kind"] == "rent")
    assert good["verified"] is True


def test_amount_without_thousands_separator_still_verifies():
    """Invoices print "Rs 44000" as often as "Rs. 44,000"."""
    result = extraction.extract_invoice(["Monthly Rent Rs. 44000"])
    assert result["unverified_items"] == []
    assert result["line_items"][0]["amount_paise"] == 4_400_000


def test_ocr_spacing_does_not_break_verification():
    """The same Rs./amount spacing case that broke lease terms."""
    result = extraction.extract_invoice(["Monthly Rent Rs.44000", "CAM Rs.5,200"])
    assert result["unverified_items"] == []


@pytest.mark.parametrize(
    "line",
    [
        "Maintenance charges Rs. 5,200",
        "CAM Rs. 5,200/-",
        "Rent payable: Rs. 44,000 per month",
    ],
)
def test_normal_invoice_wording_is_not_flagged(line):
    result = extraction.extract_invoice([line])
    assert result["unverified_items"] == [], result
