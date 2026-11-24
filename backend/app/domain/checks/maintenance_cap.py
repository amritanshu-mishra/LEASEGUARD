"""Maintenance cap check. Deterministic, integer paise only."""
from __future__ import annotations

from datetime import date

from ..models import CheckType, ControllingTermInfo, Finding, Term, TermType
from ..money import format_inr
from ..precedence import controlling_term
from ..reasons import FindingStatus, REASON_MESSAGES, ReasonCode


def _term_info(t: Term) -> ControllingTermInfo:
    return ControllingTermInfo(
        term_id=t.id, document_name=t.document_name, document_kind=t.document_kind.value,
        effective_date=t.effective_date.isoformat(), quote=t.source_quote, page=t.page, value=t.value,
    )


def check_maintenance_cap(
    finding_id: str,
    invoice_id: str,
    period_start: date,
    billed_maintenance_paise: int,
    terms: list[Term],
) -> Finding | None:
    res = controlling_term(terms, TermType.MAINTENANCE_CAP_PAISE, period_start)
    if not res.ok:
        reason = res.reason or ReasonCode.TERM_NOT_FOUND
        ct = None
        if res.conflicts:
            ct = _term_info(res.conflicts[0])
        elif reason == ReasonCode.CLAUSE_AMBIGUOUS:
            amb = [t for t in terms if t.term_type == TermType.MAINTENANCE_CAP_PAISE and t.effective_date <= period_start and (t.ambiguous or t.value is None)]
            ct = _term_info(amb[-1]) if amb else None
        return Finding(
            id=finding_id, invoice_id=invoice_id, check_type=CheckType.MAINTENANCE_CAP,
            status=FindingStatus.UNCERTAIN, billed_paise=billed_maintenance_paise,
            expected_paise=None, difference_paise=None, reason=reason,
            message=REASON_MESSAGES.get(reason, "Unable to verify maintenance cap."),
            controlling_term=ct,
            calculation="Maintenance cap term missing or ambiguous; no calculation performed.",
        )

    cap = res.term.value  # type: ignore
    diff = billed_maintenance_paise - cap
    calc = (
        f"Maintenance cap = {format_inr(cap)} ({res.term.document_name}, effective {res.term.effective_date.isoformat()}, p.{res.term.page}). "
        f"Billed maintenance = {format_inr(billed_maintenance_paise)}. "
        f"Difference = {format_inr(billed_maintenance_paise)} - {format_inr(cap)} = {format_inr(diff)}."
    )
    if diff > 0:
        return Finding(
            id=finding_id, invoice_id=invoice_id, check_type=CheckType.MAINTENANCE_CAP,
            status=FindingStatus.CONFIRMED, billed_paise=billed_maintenance_paise,
            expected_paise=cap, difference_paise=diff, reason=None,
            message=(
                f"Maintenance billed above the agreed cap. "
                f"Cap {format_inr(cap)}, billed {format_inr(billed_maintenance_paise)}. "
                f"Difference: {format_inr(diff)}."
            ),
            controlling_term=_term_info(res.term),
            calculation=calc,
        )

    # BR8: billed <= cap is not a discrepancy. It is reported as information so the
    # tenant can see the charge was checked, but it never enters a letter or the impact
    # total, and it carries no approve/reject action.
    if diff == 0:
        msg = (
            f"Maintenance matches the agreed cap of {format_inr(cap)}. "
            "No action needed."
        )
    else:
        msg = (
            f"Maintenance is below the agreed cap of {format_inr(cap)} "
            f"(billed {format_inr(billed_maintenance_paise)}, under by {format_inr(-diff)}). "
            "No action needed."
        )
    return Finding(
        id=finding_id, invoice_id=invoice_id, check_type=CheckType.MAINTENANCE_CAP,
        status=FindingStatus.NO_ISSUE, billed_paise=billed_maintenance_paise,
        expected_paise=cap, difference_paise=diff, reason=None,
        message=msg, controlling_term=_term_info(res.term),
        calculation=calc, informational=True,
    )
