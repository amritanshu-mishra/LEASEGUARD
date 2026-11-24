"""Rent escalation check. Deterministic, integer paise only."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..models import CheckType, ControllingTermInfo, Finding, Term, TermType
from ..money import apply_rate, format_inr
from ..precedence import controlling_term
from ..reasons import FindingStatus, REASON_MESSAGES, ReasonCode


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    # clamp day to end of month
    import calendar
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def _anniversaries(lease_start: date, period_start: date, cadence_months: int) -> int:
    if cadence_months <= 0:
        return 0
    n = 0
    while True:
        nxt = _add_months(lease_start, (n + 1) * cadence_months)
        if nxt <= period_start:
            n += 1
        else:
            break
        if n > 200:  # safety
            break
    return n


def _term_info(t: Term) -> ControllingTermInfo:
    return ControllingTermInfo(
        term_id=t.id,
        document_name=t.document_name,
        document_kind=t.document_kind.value,
        effective_date=t.effective_date.isoformat(),
        quote=t.source_quote,
        page=t.page,
        value=t.value,
    )


def check_rent_escalation(
    finding_id: str,
    invoice_id: str,
    lease_start: date,
    period_start: date,
    billed_rent_paise: int,
    terms: list[Term],
) -> Finding | None:
    """Return a Finding if there's a discrepancy or uncertainty; None if no issue."""
    base_res = controlling_term(terms, TermType.BASE_RENT_PAISE, period_start)
    if not base_res.ok:
        reason = base_res.reason or ReasonCode.TERM_NOT_FOUND
        return Finding(
            id=finding_id, invoice_id=invoice_id, check_type=CheckType.RENT_ESCALATION,
            status=FindingStatus.UNCERTAIN, billed_paise=billed_rent_paise,
            expected_paise=None, difference_paise=None, reason=reason,
            message=REASON_MESSAGES.get(reason, "Unable to verify rent escalation."),
            controlling_term=_term_info(base_res.conflicts[0]) if base_res.conflicts else None,
            calculation="Base rent term missing or ambiguous; no calculation performed.",
        )

    cadence_res = controlling_term(terms, TermType.ESCALATION_CADENCE_MONTHS, period_start)
    cadence = cadence_res.term.value if cadence_res.ok and cadence_res.term and cadence_res.term.value else 12

    base = base_res.term.value  # type: ignore
    n = _anniversaries(lease_start, period_start, cadence)

    # Compute expected rent stepwise, resolving rate at each anniversary
    rent = base
    calc_steps = [f"Base rent = {format_inr(base)} ({base_res.term.document_name}, p.{base_res.term.page})"]
    controlling_for_last = None
    last_rate = None
    for i in range(n):
        ann_date = _add_months(lease_start, (i + 1) * cadence)
        rate_res = controlling_term(terms, TermType.ESCALATION_RATE_BP, ann_date)
        if not rate_res.ok:
            reason = rate_res.reason or ReasonCode.TERM_NOT_FOUND
            # If ambiguous/conflict, surface it
            ct = None
            if rate_res.conflicts:
                ct = _term_info(rate_res.conflicts[0])
            elif rate_res.reason == ReasonCode.CLAUSE_AMBIGUOUS:
                # find the ambiguous candidate for evidence
                amb = [t for t in terms if t.term_type == TermType.ESCALATION_RATE_BP and t.effective_date <= ann_date and (t.ambiguous or t.value is None)]
                ct = _term_info(amb[-1]) if amb else None
            return Finding(
                id=finding_id, invoice_id=invoice_id, check_type=CheckType.RENT_ESCALATION,
                status=FindingStatus.UNCERTAIN, billed_paise=billed_rent_paise,
                expected_paise=None, difference_paise=None, reason=reason,
                message=REASON_MESSAGES.get(reason, "Unable to verify rent escalation."),
                controlling_term=ct,
                calculation="; ".join(calc_steps) + f"; escalation rate at {ann_date.isoformat()} unresolved ({reason.value}).",
            )
        rate = rate_res.term.value  # type: ignore
        new_rent = apply_rate(rent, rate)
        pct = rate / 100
        calc_steps.append(
            f"Anniversary {i+1} ({ann_date.isoformat()}): rate {pct:.2f}% from {rate_res.term.document_name} (effective {rate_res.term.effective_date.isoformat()}, p.{rate_res.term.page}) -> {format_inr(rent)} x (10000+{rate})/10000 = {format_inr(new_rent)}"
        )
        rent = new_rent
        controlling_for_last = rate_res.term
        last_rate = rate

    expected = rent
    diff = billed_rent_paise - expected
    if n == 0:
        calc_steps.append(f"No completed anniversaries as of {period_start.isoformat()}; expected = base rent {format_inr(expected)}.")
    calc_steps.append(f"Billed rent = {format_inr(billed_rent_paise)}. Difference = {format_inr(billed_rent_paise)} - {format_inr(expected)} = {format_inr(diff)}.")

    calculation = " ".join(calc_steps)

    if diff > 0:
        # confirmed discrepancy
        ct = _term_info(controlling_for_last) if controlling_for_last else _term_info(base_res.term)
        pct_str = f"{last_rate/100:.2f}%" if last_rate is not None else "the base rate"
        msg = (
            f"Invoice applies a higher rent increase than the controlling term allows. "
            f"Expected {format_inr(expected)}, billed {format_inr(billed_rent_paise)}. "
            f"Difference: {format_inr(diff)}."
        )
        return Finding(
            id=finding_id, invoice_id=invoice_id, check_type=CheckType.RENT_ESCALATION,
            status=FindingStatus.CONFIRMED, billed_paise=billed_rent_paise,
            expected_paise=expected, difference_paise=diff, reason=None,
            message=msg, controlling_term=ct, calculation=calculation,
        )

    # BR8: billed <= expected is not a discrepancy. Report it as information so the
    # tenant sees the rent was checked, without offering an approval action.
    if diff == 0:
        msg = f"Rent matches the controlling term ({format_inr(expected)}). No action needed."
    else:
        msg = (
            f"Rent is below the controlling term "
            f"(expected {format_inr(expected)}, billed {format_inr(billed_rent_paise)}, "
            f"under by {format_inr(-diff)}). No action needed."
        )
    return Finding(
        id=finding_id, invoice_id=invoice_id, check_type=CheckType.RENT_ESCALATION,
        status=FindingStatus.NO_ISSUE, billed_paise=billed_rent_paise,
        expected_paise=expected, difference_paise=diff, reason=None,
        message=msg, controlling_term=(_term_info(controlling_for_last) if controlling_for_last else _term_info(base_res.term)), calculation=calculation,
        informational=True,
    )
