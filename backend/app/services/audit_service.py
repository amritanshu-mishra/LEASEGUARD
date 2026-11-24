from __future__ import annotations

from datetime import date
from typing import Any

from ..domain.checks.escalation import check_rent_escalation
from ..domain.checks.maintenance_cap import check_maintenance_cap
from ..domain.models import CheckType, DocumentKind, Finding, Term, TermType
from ..domain.money import format_inr
from ..domain.precedence import controlling_term
from ..domain.reasons import FindingStatus
from ..infra import store


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _term_from_row(r: dict) -> Term:
    return Term(
        id=r["id"],
        document_id=r["document_id"],
        document_name=r.get("document_name", ""),
        document_kind=DocumentKind(r.get("document_kind", "lease")),
        effective_date=_parse_date(r["effective_date"]),
        term_type=TermType(r["term_type"]),
        value=r.get("value_integer"),
        source_quote=r.get("source_quote", ""),
        page=int(r.get("page", 1)),
        confidence=float(r.get("confidence", 1.0)),
        validated=bool(r.get("validated", True)),
        ambiguous=bool(r.get("ambiguous", False)),
    )


def get_terms_for_lease(lease_id: str) -> list[Term]:
    return [_term_from_row(r) for r in store.list_terms(lease_id)]


def terms_in_force(lease_id: str, as_of: str) -> list[dict]:
    terms = get_terms_for_lease(lease_id)
    d = _parse_date(as_of)
    out = []
    for tt in [TermType.BASE_RENT_PAISE, TermType.ESCALATION_RATE_BP, TermType.MAINTENANCE_CAP_PAISE]:
        res = controlling_term(terms, tt, d)
        item: dict[str, Any] = {"term_type": tt.value, "status": "resolved" if res.ok else "uncertain"}
        if res.ok and res.term:
            t = res.term
            item.update({
                "value": t.value,
                "source_quote": t.source_quote,
                "page": t.page,
                "document_name": t.document_name,
                "document_kind": t.document_kind.value,
                "effective_date": t.effective_date.isoformat(),
            })
        else:
            item["reason"] = res.reason.value if res.reason else "term_not_found"
            if res.conflicts:
                item["conflicts"] = [{
                    "document_name": c.document_name, "effective_date": c.effective_date.isoformat(),
                    "value": c.value, "source_quote": c.source_quote, "page": c.page
                } for c in res.conflicts]
        out.append(item)
    # also list superseded terms for transparency
    superseded = []
    for t in terms:
        if t.term_type in {TermType.BASE_RENT_PAISE, TermType.ESCALATION_RATE_BP, TermType.MAINTENANCE_CAP_PAISE} and t.effective_date <= d:
            res = controlling_term(terms, t.term_type, d)
            if res.ok and res.term and res.term.id != t.id:
                superseded.append({
                    "term_type": t.term_type.value, "value": t.value, "document_name": t.document_name,
                    "effective_date": t.effective_date.isoformat(), "page": t.page, "source_quote": t.source_quote,
                })
    return out, superseded  # type: ignore


def _finding_to_dict(f: Finding) -> dict:
    ct = None
    if f.controlling_term:
        ct = {
            "term_id": f.controlling_term.term_id,
            "document_name": f.controlling_term.document_name,
            "document_kind": f.controlling_term.document_kind,
            "effective_date": f.controlling_term.effective_date,
            "quote": f.controlling_term.quote,
            "page": f.controlling_term.page,
            "value": f.controlling_term.value,
        }
    return {
        "id": f.id,
        "invoice_id": f.invoice_id,
        "check_type": f.check_type.value,
        "status": f.status.value,
        "billed_paise": f.billed_paise,
        "expected_paise": f.expected_paise,
        "difference_paise": f.difference_paise,
        "reason": f.reason.value if f.reason else None,
        "message": f.message,
        "controlling_term": ct,
        "calculation": f.calculation,
        "approved": f.approved,
        "informational": getattr(f, "informational", False),
    }


def run_audit(lease_id: str, invoice: dict, user_id: str) -> dict:
    # Lease start date is needed for anniversary-based escalation; read it directly.
    from ..infra.db import db
    with db() as conn:
        lease_row = conn.execute("SELECT * FROM leases WHERE id=? AND user_id=?", (lease_id, user_id)).fetchone()
    if not lease_row:
        raise ValueError("Lease not found")
    lease_start = _parse_date(lease_row["start_date"])
    period_start = _parse_date(invoice["period_start"])
    terms = get_terms_for_lease(lease_id)

    findings: list[Finding] = []
    line_items = invoice.get("line_items", [])
    rent_item = next((li for li in line_items if li.get("kind") == "rent"), None)
    maint_item = next((li for li in line_items if li.get("kind") == "maintenance"), None)

    if rent_item:
        f = check_rent_escalation(
            finding_id=store.uid(), invoice_id=invoice["id"], lease_start=lease_start,
            period_start=period_start, billed_rent_paise=int(rent_item["amount_paise"]), terms=terms,
        )
        if f:
            findings.append(f)
    if maint_item:
        f = check_maintenance_cap(
            finding_id=store.uid(), invoice_id=invoice["id"], period_start=period_start,
            billed_maintenance_paise=int(maint_item["amount_paise"]), terms=terms,
        )
        if f:
            findings.append(f)

    finding_dicts = [_finding_to_dict(f) for f in findings]
    store.replace_findings_for_invoice(invoice["id"], lease_id, finding_dicts)
    store.log_event(user_id, "audit_run", "invoice", invoice["id"])
    saved = store.list_findings(invoice["id"])
    return {
        "invoice_id": invoice["id"],
        "period_start": invoice["period_start"],
        "period_label": invoice.get("period_label", invoice["period_start"]),
        "findings": saved,
        "impact": compute_impact(saved),
    }


def compute_impact(findings: list[dict]) -> dict:
    """Impact metric that cannot be mistaken for recovered money.

    - excess_detected: sum of confirmed billed-minus-expected differences.
    - amount_flagged: confirmed differences PLUS billed amounts of uncertain items
      that a human still has to review. Labelled separately so nothing is overclaimed.
    - time_saved: transparent estimate, scaled by findings, with its basis shown.
    """
    # BR8: informational rows (charges that matched or were below the agreed rate) are
    # excluded from every money total. They must never inflate "excess detected".
    billable = [f for f in findings if not f.get("informational")]
    confirmed = [f for f in billable if f.get("status") == "confirmed"]
    uncertain = [f for f in billable if f.get("status") == "uncertain"]
    no_issue = [f for f in findings if f.get("status") == "no_issue"]

    excess = sum(int(f.get("difference_paise") or 0) for f in confirmed)
    # Uncertain items: we cannot compute a difference, so flag the billed amount under review.
    uncertain_billed = sum(int(f.get("billed_paise") or 0) for f in uncertain)
    flagged = excess + uncertain_billed

    # Estimate: ~10 min to read a lease + invoice by hand, +8 min per item to verify clause,
    # +10 min if an amendment has to be cross-checked. Capped so the number stays modest.
    minutes = 0
    if findings:
        minutes = min(90, 10 + 8 * len(confirmed) + 6 * len(uncertain) + 3 * len(no_issue))

    return {
        "excess_detected_paise": excess,
        "excess_detected_display": format_inr(excess),
        "amount_flagged_paise": flagged,
        "amount_flagged_display": format_inr(flagged),
        "confirmed_difference_paise": excess,
        "uncertain_billed_paise": uncertain_billed,
        "confirmed_count": len(confirmed),
        "uncertain_count": len(uncertain),
        "no_issue_count": len(no_issue),
        "time_saved_minutes": minutes,
        "time_saved_display": f"~{minutes} min" if minutes else "0 min",
        "time_saved_basis": "Estimate: 10 min to read lease + invoice, 8 min per confirmed item, 6 min per uncertain item. Not money recovered.",
        "disclaimer": "Estimated time saved vs manual checking. Does not claim money recovered.",
    }
