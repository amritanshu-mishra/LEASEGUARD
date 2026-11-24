"""Unit tests for the two trust-critical services: impact metric and letter builder.

These encode the product's honesty rules. If a change ever makes the product claim
recovered money, inflate a total, or put an unapproved finding in a letter, it fails here.
"""
from __future__ import annotations

import pytest

from app.domain.money import format_inr
from app.services.audit_service import compute_impact
from app.services.letter_service import build_letter


def _sentences_with(text: str, needle: str) -> list[str]:
    """Return the sentences of `text` that mention `needle`, so negations can be checked."""
    out = []
    for sentence in text.replace("\n", " ").split(". "):
        if needle in sentence:
            out.append(sentence.strip())
    return out


def finding(
    *,
    status: str = "confirmed",
    billed: int = 4_40_000,
    expected: int | None = 4_20_000,
    diff: int | None = 2_00_000,
    approved: bool | None = None,
    informational: bool = False,
    check: str = "rent_escalation",
    quote: str = "annual rent escalation shall not exceed five percent (5%) per annum",
) -> dict:
    ct = None
    if not informational:
        ct = {
            "term_id": "t1",
            "document_name": "Amendment_1_SYNTHETIC.pdf",
            "document_kind": "amendment",
            "effective_date": "2026-01-01",
            "quote": quote,
            "page": 1,
            "value": 500,
        }
    return {
        "id": f"f-{status}-{billed}-{check}",
        "invoice_id": "inv",
        "check_type": check,
        "status": status,
        "billed_paise": billed,
        "expected_paise": expected,
        "difference_paise": diff,
        "reason": None,
        "message": "message",
        "controlling_term": ct,
        "calculation": "Base rent = Rs. 40,000.00 -> Rs. 42,000.00",
        "approved": approved,
        "informational": informational,
    }


LEASE = {"property_label": "Shop 12, Market Complex, Hazratganj, Lucknow"}
INVOICE = {"period_label": "April 2026", "period_start": "2026-04-01"}


# --- impact metric ---------------------------------------------------------

def test_impact_sums_only_confirmed_differences():
    impact = compute_impact([
        finding(diff=2_00_000),
        finding(diff=1_20_000, check="maintenance_cap"),
    ])
    assert impact["excess_detected_paise"] == 3_20_000
    assert impact["confirmed_count"] == 2
    assert impact["uncertain_count"] == 0


def test_impact_excludes_informational_rows_entirely():
    impact = compute_impact([
        finding(diff=2_00_000),
        finding(status="no_issue", expected=4_00_000, diff=0, informational=True),
        finding(status="no_issue", expected=4_00_000, diff=-1_00_000, informational=True),
    ])
    assert impact["excess_detected_paise"] == 2_00_000, "undercharges must not change the total"
    assert impact["amount_flagged_paise"] == 2_00_000
    assert impact["no_issue_count"] == 2
    assert impact["confirmed_count"] == 1


def test_impact_never_produces_a_negative_excess():
    impact = compute_impact([
        finding(status="no_issue", expected=4_00_000, diff=-50_000, informational=True),
    ])
    assert impact["excess_detected_paise"] == 0
    assert impact["amount_flagged_paise"] == 0


def test_impact_flags_uncertain_billed_amount_for_human_review():
    impact = compute_impact([
        finding(diff=2_00_000),
        finding(status="uncertain", expected=None, diff=None, billed=5_20_000, check="maintenance_cap"),
    ])
    # Confirmed difference + the billed amount a human still has to check.
    assert impact["excess_detected_paise"] == 2_00_000
    assert impact["amount_flagged_paise"] == 2_00_000 + 5_20_000
    assert impact["uncertain_billed_paise"] == 5_20_000
    assert impact["uncertain_count"] == 1


def test_impact_disclaimer_says_it_is_not_recovered_money():
    impact = compute_impact([finding()])
    assert "does not claim money recovered" in impact["disclaimer"].lower()
    assert "recovered" in impact["time_saved_basis"].lower()
    # No field may assert recovery as a fact. Allow only negations of it.
    blob = " ".join(str(v) for v in impact.values()).lower()
    for occurrence in _sentences_with(blob, "money recovered"):
        assert "not" in occurrence or "does not" in occurrence, occurrence


def test_impact_is_empty_when_there_are_no_findings():
    impact = compute_impact([])
    assert impact["excess_detected_paise"] == 0
    assert impact["time_saved_minutes"] == 0
    assert impact["time_saved_display"] == "0 min"


def test_time_saved_is_capped_and_scales_with_volume():
    one = compute_impact([finding()])["time_saved_minutes"]
    many = compute_impact([finding() for _ in range(40)])["time_saved_minutes"]
    assert one < many
    assert many <= 90, "estimate must stay modest, not inflate"


# --- letter builder --------------------------------------------------------

def test_letter_refuses_when_nothing_is_approved():
    with pytest.raises(ValueError):
        build_letter(LEASE, INVOICE, [finding(approved=None)])


def test_letter_refuses_approved_informational_rows():
    """A clean charge can never be escalated into a dispute."""
    with pytest.raises(ValueError):
        build_letter(LEASE, INVOICE, [
            finding(status="no_issue", diff=0, approved=True, informational=True)
        ])


def test_letter_contains_only_approved_findings():
    letter = build_letter(LEASE, INVOICE, [
        finding(approved=True, diff=2_00_000),
        finding(approved=False, diff=1_20_000, check="maintenance_cap"),
        finding(approved=None, diff=9_99_999, check="maintenance_cap"),
    ])
    assert letter["approved_count"] == 1
    assert letter["total_flagged_paise"] == 2_00_000
    assert "Rs. 2,000.00" in letter["text"]
    assert "Rs. 1,200.00" not in letter["text"]
    assert "Rs. 9,999.99" not in letter["text"]


def test_letter_includes_every_evidence_field():
    letter = build_letter(LEASE, INVOICE, [finding(approved=True)])
    text = letter["text"]
    assert "Amendment_1_SYNTHETIC.pdf" in text        # document
    assert "page 1" in text                            # page
    assert "2026-01-01" in text                        # effective date
    assert "shall not exceed five percent" in text     # verbatim quote
    assert "Base rent = Rs. 40,000.00" in text         # calculation
    assert "discrepancies for review" in text.lower()  # BR9 wording


def test_letter_never_calls_anything_fraud_or_recovered():
    text = build_letter(LEASE, INVOICE, [finding(approved=True)])["text"].lower()
    for forbidden in ("fraud", "cheat", "illegal", "money recovered", "recovered rs"):
        assert forbidden not in text


def test_hindi_letter_translates_prose_but_not_the_quote():
    letter = build_letter(LEASE, INVOICE, [finding(approved=True)], language="hi")
    assert letter["language"] == "hi"
    assert "समीक्षा हेतु" in letter["text"]                 # Hindi prose
    assert "क्लॉज़ का हवाला" in letter["text"]               # Hindi label
    # The evidence itself is untouched.
    assert "shall not exceed five percent (5%) per annum" in letter["text"]
    assert "Amendment_1_SYNTHETIC.pdf" in letter["text"]


def test_letter_handles_a_missing_calculation_gracefully():
    f = finding(approved=True)
    f["calculation"] = ""
    letter = build_letter(LEASE, INVOICE, [f])
    assert "Calculation" not in letter["text"]
    assert letter["approved_count"] == 1


def test_letter_totals_use_inr_formatting():
    letter = build_letter(LEASE, INVOICE, [
        finding(approved=True, diff=2_00_000),
        finding(approved=True, diff=1_20_000, check="maintenance_cap"),
    ])
    assert letter["total_flagged_display"] == format_inr(3_20_000)
    assert "Rs. 3,200.00" in letter["text"]
