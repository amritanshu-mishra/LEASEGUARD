from datetime import date

from app.domain.money import apply_rate, format_inr, rupees_to_paise
from app.domain.models import CheckType, DocumentKind, Term, TermType
from app.domain.precedence import controlling_term
from app.domain.reasons import FindingStatus, ReasonCode
from app.domain.checks.escalation import check_rent_escalation
from app.domain.checks.maintenance_cap import check_maintenance_cap


def make_term(id, doc, kind, eff, tt, val, quote="q", page=1, validated=True, ambiguous=False):
    return Term(
        id=id, document_id=doc, document_name=doc, document_kind=DocumentKind(kind),
        effective_date=date.fromisoformat(eff), term_type=tt, value=val,
        source_quote=quote, page=page, confidence=0.99, validated=validated, ambiguous=ambiguous,
    )


def test_rupees_paise_format():
    assert rupees_to_paise(40000) == 4000000
    assert "40,000.00" in format_inr(4000000)


def test_apply_rate_5pct():
    # 40,000.00 * 1.05 = 42,000.00
    assert apply_rate(4000000, 500) == 4200000


def test_precedence_latest_wins():
    terms = [
        make_term("t1", "Lease", "lease", "2025-04-01", TermType.ESCALATION_RATE_BP, 1000),
        make_term("t2", "Amendment 1", "amendment", "2026-01-01", TermType.ESCALATION_RATE_BP, 500),
    ]
    res = controlling_term(terms, TermType.ESCALATION_RATE_BP, date(2026, 4, 1))
    assert res.ok
    assert res.term.value == 500
    assert res.term.document_name == "Amendment 1"


def test_precedence_conflict():
    terms = [
        make_term("t1", "A1", "amendment", "2026-01-01", TermType.ESCALATION_RATE_BP, 500),
        make_term("t2", "A2", "amendment", "2026-01-01", TermType.ESCALATION_RATE_BP, 700),
    ]
    res = controlling_term(terms, TermType.ESCALATION_RATE_BP, date(2026, 4, 1))
    assert not res.ok
    assert res.reason == ReasonCode.CONFLICTING_TERMS


def test_g1_amendment_caps_escalation():
    terms = [
        make_term("b", "Lease", "lease", "2025-04-01", TermType.BASE_RENT_PAISE, 4000000, "Monthly Rent of Rs. 40,000", 2),
        make_term("e1", "Lease", "lease", "2025-04-01", TermType.ESCALATION_RATE_BP, 1000, "ten percent (10%) per annum", 3),
        make_term("e2", "Amendment 1", "amendment", "2026-01-01", TermType.ESCALATION_RATE_BP, 500, "not exceed five percent (5%) per annum", 1),
    ]
    f = check_rent_escalation("f1", "inv1", date(2025, 4, 1), date(2026, 4, 1), 4400000, terms)
    assert f is not None
    assert f.status == FindingStatus.CONFIRMED
    assert f.expected_paise == 4200000
    assert f.difference_paise == 200000
    assert f.controlling_term.document_name == "Amendment 1"


def test_g2_maintenance_above_cap():
    terms = [
        make_term("m", "Lease", "lease", "2025-04-01", TermType.MAINTENANCE_CAP_PAISE, 400000, "capped at Rs. 4,000", 4),
    ]
    f = check_maintenance_cap("f2", "inv1", date(2026, 4, 1), 520000, terms)
    assert f is not None
    assert f.status == FindingStatus.CONFIRMED
    assert f.difference_paise == 120000


def test_g3_clean_invoice_no_finding():
    terms = [
        make_term("b", "Lease", "lease", "2025-04-01", TermType.BASE_RENT_PAISE, 4000000),
        make_term("e1", "Lease", "lease", "2025-04-01", TermType.ESCALATION_RATE_BP, 1000),
        make_term("e2", "Amendment 1", "amendment", "2026-01-01", TermType.ESCALATION_RATE_BP, 500),
    ]
    f = check_rent_escalation("f3", "inv1", date(2025, 4, 1), date(2026, 4, 1), 4200000, terms)
    # BR8: a correct charge is reported as information, not as a discrepancy (G3).
    assert f is not None
    assert f.status is FindingStatus.NO_ISSUE
    assert f.informational is True
    assert f.difference_paise == 0


def test_g6_amendment_not_yet_effective():
    terms = [
        make_term("b", "Lease", "lease", "2025-04-01", TermType.BASE_RENT_PAISE, 4000000),
        make_term("e1", "Lease", "lease", "2025-04-01", TermType.ESCALATION_RATE_BP, 1000),
        make_term("e2", "Amendment 1", "amendment", "2026-07-01", TermType.ESCALATION_RATE_BP, 500),
    ]
    # April 2026 invoice: amendment effective July 2026, so lease 10% controls -> 44,000 is correct
    f = check_rent_escalation("f6", "inv1", date(2025, 4, 1), date(2026, 4, 1), 4400000, terms)
    # G6: the future amendment does not control, so 44,000 is correct => information only.
    assert f is not None
    assert f.status is FindingStatus.NO_ISSUE
    assert f.informational is True
