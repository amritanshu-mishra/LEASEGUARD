"""Amendment precedence resolution. Pure logic, no I/O."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from .models import Term, TermType
from .reasons import ReasonCode


@dataclass
class ControllingResult:
    term: Optional[Term]
    reason: Optional[ReasonCode] = None
    conflicts: list[Term] | None = None

    @property
    def ok(self) -> bool:
        return self.term is not None and self.reason is None


def controlling_term(terms: list[Term], term_type: TermType, period_start: date) -> ControllingResult:
    """Pick the controlling term for term_type as of period_start.

    Rules (from spec BR2-BR5):
    - Eligible: validated, not ambiguous, value is not None, effective_date <= period_start.
    - Latest effective_date wins.
    - If multiple terms share latest effective_date but differ in value -> Conflict.
    - If none eligible -> Missing.
    """
    eligible = [
        t for t in terms
        if t.term_type == term_type
        and t.validated
        and not t.ambiguous
        and t.value is not None
        and t.effective_date <= period_start
    ]
    if not eligible:
        # Distinguish ambiguous/unverified presence? Keep simple: report term_not_found;
        # caller can inspect raw terms for ambiguous reason.
        # Check if there were candidates that were ambiguous/unvalidated.
        candidates = [
            t for t in terms
            if t.term_type == term_type and t.effective_date <= period_start
        ]
        if any(t.ambiguous or t.value is None for t in candidates):
            return ControllingResult(term=None, reason=ReasonCode.CLAUSE_AMBIGUOUS)
        if any(not t.validated for t in candidates):
            return ControllingResult(term=None, reason=ReasonCode.QUOTE_UNVERIFIED)
        return ControllingResult(term=None, reason=ReasonCode.TERM_NOT_FOUND)

    latest = max(t.effective_date for t in eligible)

    # An ambiguous or unverified term dated *at or after* the controlling one could
    # still change the answer. Silently falling back to the older clause would be a
    # guess, so the result is uncertain instead (spec G4/G5, BR6).
    later_unusable = [
        t for t in terms
        if t.term_type == term_type
        and t.effective_date <= period_start
        and t.effective_date >= latest
        and (t.ambiguous or t.value is None or not t.validated)
    ]
    if later_unusable:
        if any(t.ambiguous or t.value is None for t in later_unusable):
            return ControllingResult(term=None, reason=ReasonCode.CLAUSE_AMBIGUOUS,
                                     conflicts=sorted(later_unusable, key=lambda t: t.id))
        return ControllingResult(term=None, reason=ReasonCode.QUOTE_UNVERIFIED,
                                 conflicts=sorted(later_unusable, key=lambda t: t.id))

    top = [t for t in eligible if t.effective_date == latest]

    # A value the tenant stated themselves settles a contradiction; it does not
    # join one. Without this, "Use this clause" on two conflicting amendments
    # produced a three-way conflict (both clauses plus the choice) and the screen
    # stayed uncertain - the button did nothing visible.
    user_chosen = [t for t in top if t.entered_by_user]
    if user_chosen:
        return ControllingResult(term=sorted(user_chosen, key=lambda t: t.id)[0])

    values = {t.value for t in top}
    if len(values) > 1:
        return ControllingResult(term=None, reason=ReasonCode.CONFLICTING_TERMS, conflicts=top)
    # If multiple with same value and same date, pick deterministically (first by id).
    top_sorted = sorted(top, key=lambda t: t.id)
    return ControllingResult(term=top_sorted[0])
