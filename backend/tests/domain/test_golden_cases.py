"""Golden cases G1-G6 and unit cases U1-U2 from the technical design spec (section 10.4).

Each test encodes the spec's expected result literally, so a behaviour change that
breaks a documented rule fails here rather than in the demo.
"""
from __future__ import annotations

from datetime import date

from app.domain.checks.escalation import check_rent_escalation
from app.domain.checks.maintenance_cap import check_maintenance_cap
from app.domain.models import DocumentKind, Term, TermType
from app.domain.precedence import controlling_term
from app.domain.reasons import FindingStatus, ReasonCode

LEASE_START = date(2025, 4, 1)
APRIL_2026 = date(2026, 4, 1)


def term(
    tid: str,
    ttype: TermType,
    value: int | None,
    effective: str,
    *,
    doc: str = "Lease_Agreement_SYNTHETIC.pdf",
    kind: DocumentKind = DocumentKind.LEASE,
    page: int = 1,
    quote: str = "quoted clause text",
    validated: bool = True,
    ambiguous: bool = False,
) -> Term:
    return Term(
        id=tid, document_id=f"doc-{tid}", document_name=doc, document_kind=kind,
        effective_date=date.fromisoformat(effective), term_type=ttype, value=value,
        source_quote=quote, page=page, confidence=1.0,
        validated=validated, ambiguous=ambiguous,
    )


def base_terms() -> list[Term]:
    return [
        term("base", TermType.BASE_RENT_PAISE, 40_00_000, "2025-04-01", page=2),
        term("esc10", TermType.ESCALATION_RATE_BP, 1000, "2025-04-01", page=3),
        term("maint", TermType.MAINTENANCE_CAP_PAISE, 4_00_000, "2025-04-01", page=4),
    ]


def amendment_5pct() -> Term:
    return term(
        "amend5", TermType.ESCALATION_RATE_BP, 500, "2026-01-01",
        doc="Amendment_1_SYNTHETIC.pdf", kind=DocumentKind.AMENDMENT, page=1,
        quote="annual rent escalation shall not exceed five percent (5%) per annum",
    )


# --- G1: amendment ignored -------------------------------------------------
def test_g1_amendment_caps_escalation():
    terms = base_terms() + [amendment_5pct()]
    f = check_rent_escalation("g1", "inv", LEASE_START, APRIL_2026, 44_00_000, terms)
    assert f is not None
    assert f.status is FindingStatus.CONFIRMED
    assert f.difference_paise == 2_00_000          # Rs 2,000
    assert f.expected_paise == 42_00_000           # Rs 42,000
    assert f.controlling_term.document_name == "Amendment_1_SYNTHETIC.pdf"
    assert f.controlling_term.effective_date == "2026-01-01"
    assert not f.informational


# --- G2: maintenance above cap --------------------------------------------
def test_g2_maintenance_above_cap():
    f = check_maintenance_cap("g2", "inv", APRIL_2026, 5_20_000, base_terms())
    assert f is not None
    assert f.status is FindingStatus.CONFIRMED
    assert f.difference_paise == 1_20_000          # Rs 1,200
    assert f.controlling_term.page == 4


# --- G3: clean invoice -> no findings --------------------------------------
def test_g3_clean_invoice_produces_no_findings():
    terms = base_terms() + [amendment_5pct()]
    rent = check_rent_escalation("g3a", "inv", LEASE_START, APRIL_2026, 42_00_000, terms)
    maint = check_maintenance_cap("g3b", "inv", APRIL_2026, 3_80_000, terms)
    # Neither charge is a discrepancy. Per BR8 both are informational, never confirmed.
    for finding in (rent, maint):
        assert finding is not None
        assert finding.status is FindingStatus.NO_ISSUE
        assert finding.informational is True
        assert finding.approved is None


# --- G4: ambiguous clause --------------------------------------------------
def test_g4_ambiguous_clause_is_uncertain_with_no_amount():
    terms = base_terms() + [
        term(
            "vague", TermType.ESCALATION_RATE_BP, None, "2026-01-01",
            doc="Amendment_2_SYNTHETIC.pdf", kind=DocumentKind.AMENDMENT,
            quote="a reasonable increase in line with the market", ambiguous=True,
        )
    ]
    f = check_rent_escalation("g4", "inv", LEASE_START, APRIL_2026, 44_00_000, terms)
    assert f is not None
    assert f.status is FindingStatus.UNCERTAIN
    # The 10% lease term is still eligible, so G4 only bites when no clean term exists.
    # Assert on the precedence layer directly for the spec's exact expectation.
    res = controlling_term(
        [t for t in terms if t.term_type is TermType.ESCALATION_RATE_BP and t.id == "vague"],
        TermType.ESCALATION_RATE_BP, APRIL_2026,
    )
    assert res.reason is ReasonCode.CLAUSE_AMBIGUOUS
    assert res.term is None


def test_g4_ambiguous_clause_never_computes_an_amount():
    """A vague clause must not become a number, even if it is the only term."""
    only_vague = base_terms()[:1] + [
        term("vague", TermType.ESCALATION_RATE_BP, None, "2025-04-01",
             quote="reasonable increase in line with the market", ambiguous=True)
    ]
    f = check_rent_escalation("g4b", "inv", LEASE_START, APRIL_2026, 44_00_000, only_vague)
    assert f is not None
    assert f.status is FindingStatus.UNCERTAIN
    assert f.reason is ReasonCode.CLAUSE_AMBIGUOUS
    assert f.expected_paise is None
    assert f.difference_paise is None


# --- G5: conflicting amendments -------------------------------------------
def test_g5_conflicting_amendments_are_uncertain_and_show_both_quotes():
    terms = base_terms() + [
        amendment_5pct(),
        term("amend7", TermType.ESCALATION_RATE_BP, 700, "2026-01-01",
             doc="Amendment_3_SYNTHETIC.pdf", kind=DocumentKind.AMENDMENT,
             quote="annual rent escalation shall not exceed seven percent (7%) per annum"),
    ]
    f = check_rent_escalation("g5", "inv", LEASE_START, APRIL_2026, 44_00_000, terms)
    assert f is not None
    assert f.status is FindingStatus.UNCERTAIN
    assert f.reason is ReasonCode.CONFLICTING_TERMS
    assert f.expected_paise is None
    res = controlling_term(terms, TermType.ESCALATION_RATE_BP, APRIL_2026)
    assert {c.document_name for c in res.conflicts} == {
        "Amendment_1_SYNTHETIC.pdf", "Amendment_3_SYNTHETIC.pdf"
    }


# --- G6: amendment not yet effective --------------------------------------
def test_g6_future_amendment_does_not_control():
    future = term("amend_future", TermType.ESCALATION_RATE_BP, 500, "2026-07-01",
                  doc="Amendment_4_SYNTHETIC.pdf", kind=DocumentKind.AMENDMENT)
    terms = base_terms() + [future]
    f = check_rent_escalation("g6", "inv", LEASE_START, APRIL_2026, 44_00_000, terms)
    # Lease 10% controls: 40,000 -> 44,000, so the billed amount is correct => no discrepancy.
    assert f is not None
    assert f.status is FindingStatus.NO_ISSUE
    assert f.informational is True
    res = controlling_term(terms, TermType.ESCALATION_RATE_BP, APRIL_2026)
    assert res.term.id == "esc10"


# --- U1 / U2 unit cases ---------------------------------------------------
def test_u1_unverifiable_quote_is_rejected():
    terms = [term("unverified", TermType.MAINTENANCE_CAP_PAISE, 4_00_000, "2025-04-01",
                  validated=False)]
    f = check_maintenance_cap("u1", "inv", APRIL_2026, 5_20_000, terms)
    assert f is not None
    assert f.status is FindingStatus.UNCERTAIN
    assert f.reason is ReasonCode.QUOTE_UNVERIFIED
    assert f.expected_paise is None


def test_u2_missing_term_is_uncertain_not_inferred():
    f = check_maintenance_cap("u2", "inv", APRIL_2026, 5_20_000, base_terms()[:2])
    assert f is not None
    assert f.status is FindingStatus.UNCERTAIN
    assert f.reason is ReasonCode.TERM_NOT_FOUND
    assert f.expected_paise is None


# --- BR8: undercharge is information, not a discrepancy -------------------
def test_br8_undercharge_is_informational_only():
    f = check_maintenance_cap("br8", "inv", APRIL_2026, 3_00_000, base_terms())
    assert f is not None
    assert f.status is FindingStatus.NO_ISSUE
    assert f.informational is True
    assert f.difference_paise == -1_00_000
    assert f.status is not FindingStatus.CONFIRMED


# --- BR2: base lease effective only from lease start ----------------------
def test_br2_term_not_effective_before_its_date():
    res = controlling_term(base_terms(), TermType.BASE_RENT_PAISE, date(2025, 3, 31))
    assert res.reason is ReasonCode.TERM_NOT_FOUND


# --- BR7: rate arithmetic uses integer paise, half-up ---------------------
def test_br7_expected_rent_rounds_half_up():
    # 33,333 * 1.075 = 35,832.975 -> 35,832.98 (round half up at the paise)
    terms = [
        term("b", TermType.BASE_RENT_PAISE, 33_33_300, "2025-04-01"),
        term("e", TermType.ESCALATION_RATE_BP, 750, "2025-04-01"),
    ]
    f = check_rent_escalation("br7", "inv", LEASE_START, APRIL_2026, 36_00_000, terms)
    assert f is not None
    assert f.expected_paise == 35_83_298   # 3333300 * 10750 / 10000 = 3583297.5 -> half up
