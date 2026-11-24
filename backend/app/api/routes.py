from __future__ import annotations

import hashlib
import re
from datetime import date, datetime


from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel

from ..core.config import MAX_UPLOAD_MB, UPLOAD_DIR
from ..core import limits
from ..core.session import current_user
from ..infra import documents as docparse
from ..infra import extraction, heuristics, store
from ..services.audit_service import compute_impact, run_audit, terms_in_force
from ..services.letter_service import build_letter
from ..services.sample_service import load_sample_case

router = APIRouter(prefix="/api")


class DecisionBody(BaseModel):
    approved: bool


class CorrectionBody(BaseModel):
    value: int | None = None
    note: str | None = None


class UserTermBody(BaseModel):
    """A value the tenant states because the document is silent or contradictory."""

    term_type: str
    value: int
    note: str | None = None
    # The date from which this applies. Defaults to today - see the endpoint.
    effective_date: str | None = None


# Term types a tenant may supply. Deliberately a short, explicit list: this
# endpoint creates evidence, so it should not be a general-purpose write.
_USER_SUPPLIABLE = {
    "base_rent_paise",
    "escalation_rate_bp",
    "escalation_cadence_months",
    "maintenance_cap_paise",
}


class LeaseBody(BaseModel):
    property_label: str
    start_date: str | None = None
    end_date: str | None = None
    as_of: str | None = None


@router.get("/audit-log")
def audit_log(limit: int = Query(default=50, ge=1, le=500), user_id: str = Depends(current_user)):
    """The current user's action trail (BR10)."""
    return {"events": store.list_audit_log(user_id, limit=limit)}


@router.get("/health")
def health():
    return {"ok": True, "service": "leaseguard", "synthetic_demo": True}


@router.get("/leases")
def list_leases(user_id: str = Depends(current_user)):
    return {"leases": store.list_leases(user_id)}


@router.post("/leases/sample")
def create_sample(user_id: str = Depends(current_user)):
    result = load_sample_case(user_id)
    lease = result["lease"]
    return {
        "ok": True,
        "lease_id": lease["id"],
        "invoice_id": result["invoice"]["id"],
        "lease": lease,
        "reused": bool(result.get("reused")),
    }


@router.post("/leases")
def create_lease(body: LeaseBody, user_id: str = Depends(current_user)):
    label = (body.property_label or "").strip()
    if len(label) < 3:
        raise HTTPException(status_code=422, detail={"error": {"code": "invalid_lease", "message": "Property name is too short."}})
    lease = store.create_lease(user_id, label, body.start_date or "2025-04-01", body.end_date or "2028-03-31")
    store.log_event(user_id, "lease_created", "lease", lease["id"])
    return {"ok": True, "lease_id": lease["id"], "lease": lease}


@router.post("/leases/{lease_id}/documents")
async def upload_document(
    lease_id: str,
    kind: str = Form(...),
    effective_date: str | None = Form(default=None),
    file: UploadFile = File(...),
    user_id: str = Depends(current_user),
):
    """Upload a lease / amendment / invoice document and extract grounded terms."""
    lease = store.get_lease(lease_id, user_id)
    if not lease:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Lease not found"}})
    kind = kind.strip().lower()
    if kind not in {"lease", "amendment", "invoice"}:
        raise HTTPException(status_code=422, detail={"error": {"code": "invalid_kind", "message": "kind must be lease, amendment or invoice"}})

    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail={"error": {"code": "empty_file", "message": "The uploaded file is empty."}})
    if effective_date is not None:
        # Validate the date before parsing the file: a typo in the date should say so,
        # not produce a confusing complaint about the file contents.
        try:
            datetime.strptime(effective_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail={"error": {"code": "invalid_effective_date",
                                  "message": "effective_date must be YYYY-MM-DD."}},
            )
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail={"error": {"code": "file_too_large", "message": f"File must be under {MAX_UPLOAD_MB} MB."}})

    limits.enforce(limits.UPLOAD_LIMIT, user_id, "uploads")

    sha = hashlib.sha256(data).hexdigest()
    existing = next((d for d in store.list_documents(lease_id) if d.get("sha256") == sha), None)
    if existing:
        raise HTTPException(status_code=409, detail={"error": {"code": "duplicate_document", "message": "This exact file was already uploaded to this lease."}})

    try:
        pages = docparse.parse_pages(file.filename or "", data)
    except docparse.DocumentParseError as exc:
        raise HTTPException(status_code=422, detail={"error": {"code": "unreadable_document", "message": str(exc)}}) from exc

    eff = effective_date or (lease["start_date"] if kind != "invoice" else date.today().isoformat())
    try:
        date.fromisoformat(eff)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": {"code": "invalid_date", "message": "effective_date must be YYYY-MM-DD."}}) from exc

    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", (file.filename or f"{kind}.pdf"))[:120]
    storage = UPLOAD_DIR / f"{sha[:12]}_{safe_name}"
    storage.write_bytes(data)

    doc = store.create_document(
        lease_id, kind, file.filename or safe_name, str(storage), eff, sha, len(pages),
        "\n\n".join(pages),
        ocr=bool(getattr(pages, "ocr", False)),
        ocr_confidence=float(getattr(pages, "confidence", 1.0)),
    )

    ocr_used = bool(getattr(pages, "ocr", False))
    ocr_confidence = float(getattr(pages, "confidence", 1.0))
    response: dict = {
        "ok": True, "document_id": doc["id"], "kind": kind, "page_count": len(pages), "pages": [],
        # How the text was obtained. The UI uses this to say "read from a scan"
        # instead of implying the numbers were pulled from a text layer.
        "ocr": ocr_used, "ocr_confidence": round(ocr_confidence, 3) if ocr_used else None,
    }
    ocr_warning = (
        "This file is a scan or a photo, so the text was read with OCR. OCR can "
        "silently skip a line - check every term below against the document, and "
        "fix any that are wrong."
        if ocr_used else ""
    )

    if kind == "invoice":
        parsed = extraction.extract_invoice(pages)
        if not parsed["line_items"]:
            raise HTTPException(status_code=422, detail={
                "error": {"code": "no_line_items",
                          "message": "Could not find any billed amounts in this invoice. Try a clearer PDF or paste the text."}
            })
        invoice_warnings: list[str] = []
        if ocr_warning:
            invoice_warnings.append(ocr_warning)
        # Reconcile the typed date with the period printed on the bill. The label is
        # what the user reads; period_start is what the audit uses to pick the lease
        # terms in force. If they disagree, one of them is a lie. The document wins on
        # the month, the typed date wins on the day.
        period = heuristics.parse_period("\n\n".join(pages))
        label = parsed["period_label"] or eff[:7]
        period_start = eff
        if period:
            py, pm = period
            label = heuristics.period_label_from(py, pm)
            printed = f"{py:04d}-{pm:02d}"
            if eff[:7] != printed:
                period_start = f"{printed}-01"
                invoice_warnings.append(
                    f"The date you entered ({eff}) does not match the billing period printed on "
                    f"the invoice ({label}). The audit uses {label}."
                )
        invoice = store.create_invoice(lease_id, doc["id"], period_start, label, parsed["line_items"])
        kinds_found = {li.get("kind") for li in parsed["line_items"]}
        if "rent" not in kinds_found:
            invoice_warnings.append(
                "No rent line was found in this invoice, so rent cannot be checked. "
                "If the rent is on the bill, upload a clearer copy."
            )
        if "maintenance" not in kinds_found:
            invoice_warnings.append(
                "No maintenance/CAM line was found. Only rent will be checked for this invoice."
            )
        # A billed amount we cannot find in the document must not quietly become
        # evidence. Say so now, while the tenant is looking at the file.
        unverified = parsed.get("unverified_items") or []
        if unverified:
            invoice_warnings.append(
                "These amounts could not be verified against the document text: "
                + ", ".join(unverified[:5])
                + (", and others." if len(unverified) > 5 else ".")
                + " Check them before approving anything based on them."
            )
        response.update({
            "invoice_id": invoice["id"], "line_items": parsed["line_items"],
            "period_label": label, "extractor": parsed["extractor"], "invoice": invoice,
            "warnings": invoice_warnings,
        })
    else:
        parsed = await extraction.extract_terms(pages, kind)
        terms = []
        for t in parsed["terms"]:
            terms.append({
                "term_type": t["term_type"],
                "value": t.get("value"),
                "source_quote": t.get("source_quote", ""),
                "page": t.get("page", 1),
                "confidence": t.get("confidence", 0.5),
                "validated": bool(t.get("source_quote")) and not t.get("ambiguous"),
                "ambiguous": bool(t.get("ambiguous")),
            })
        store.replace_terms_for_document(doc["id"], lease_id, terms)
        uncertain = [t["term_type"] for t in terms if t.get("ambiguous") or not t.get("source_quote")]
        found = [t["term_type"] for t in terms if t.get("value") is not None and not t.get("ambiguous")]

        # Tell the user plainly when a document yielded nothing usable. Silently
        # accepting it would mean an later audit says "term_not_found" with no
        # indication that the upload itself was the problem.
        warnings: list[str] = []
        if ocr_warning:
            warnings.append(ocr_warning)
        if kind == "lease" and not found:
            warnings.append(
                "No lease terms could be read from this file. Check that it is the lease "
                "agreement with text (not a photo), then upload it again."
            )
        elif kind == "amendment" and "escalation_rate_bp" not in found:
            warnings.append(
                "The amendment uploaded, but its escalation clause could not be read. "
                "It will not change any charge until that clause is extracted."
            )
        if kind == "amendment" and "amendment_effective_date" not in found:
            warnings.append(
                "The amendment's effective date could not be read. Enter it in the "
                "effective-date field so the correct clause controls."
            )

        if parsed.get("fallback_reason"):
            warnings.append(
                "The reading model was unavailable, so the built-in reader was used "
                f"({parsed['fallback_reason']}). Values below are still checked against "
                "the document text, but re-check them if a number looks wrong."
            )

        response.update({
            "terms": terms,
            "extractor": parsed.get("extractor", "heuristic"),
            "uncertain_terms": uncertain,
            "found_terms": found,
            "warnings": warnings,
        })

    # Surface per-page text so the UI can show the real page the quote came from.
    response["pages"] = [{"page": i + 1, "text": p[:4000]} for i, p in enumerate(pages)]
    store.log_event(user_id, "document_uploaded", "document", doc["id"])
    return response


@router.get("/leases/{lease_id}")
def get_lease(lease_id: str, as_of: str = Query(default=None), user_id: str = Depends(current_user)):
    lease = store.get_lease(lease_id, user_id)
    if not lease:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Lease not found"}})
    # "Today" by default. The previous default was a hardcoded demo date, which meant
    # any non-UI caller silently asked for the terms in force seven months ago.
    as_of = as_of or date.today().isoformat()
    in_force, superseded = terms_in_force(lease_id, as_of)
    lease["terms_in_force"] = in_force
    lease["superseded_terms"] = superseded
    lease["as_of"] = as_of
    # attach findings summary per invoice
    for inv in lease.get("invoices", []):
        findings = store.list_findings(inv["id"])
        # Informational rows (BR8) are not discrepancies, so they are excluded from the
        # counts that drive the "review needed" badge on the lease screen.
        actionable = [f for f in findings if not f.get("informational")]
        inv["findings_count"] = len(actionable)
        inv["confirmed_count"] = sum(1 for f in actionable if f["status"] == "confirmed")
        inv["uncertain_count"] = sum(1 for f in actionable if f["status"] == "uncertain")
        inv["informational_count"] = len(findings) - len(actionable)
    return lease


@router.patch("/terms/{term_id}")
def correct_term(term_id: str, body: CorrectionBody, user_id: str = Depends(current_user)):
    """Let the tenant correct a misread clause value.

    Never trust the reader blindly: if it read Rs 4,000 as Rs 40,000 the audit is
    wrong and the tenant is the only one who can tell. The correction is recorded as
    user-supplied so the UI cannot present it as an extracted quote.
    """
    existing = store.get_term_for_user(term_id, user_id)
    if not existing:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Term not found"}})
    if body.value is not None and body.value < 0:
        raise HTTPException(status_code=422, detail={
            "error": {"code": "invalid_value", "message": "A term value cannot be negative."}})
    updated = store.correct_term(term_id, user_id, body.value, body.note or "")
    store.log_event(user_id, "term_corrected", "term", term_id,
                    detail=f"{existing['term_type']} -> {body.value}")
    return {"ok": True, "term": updated}


@router.post("/leases/{lease_id}/terms")
def add_user_term(lease_id: str, body: UserTermBody, user_id: str = Depends(current_user)):
    """Record a value the tenant supplies for a term we could not resolve.

    This closes a dead end the spec left open: a term can come back `uncertain`
    because the clause was missing or two amendments contradicted each other, and
    the screen said so with no way forward. The tenant can now state the value.

    It is stored as *entered by you*, with no document attached, and the audit
    still distinguishes it from a quoted clause - so this cannot be used to make
    the product look more certain than it is.
    """
    lease = store.get_lease(lease_id, user_id)
    if not lease:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Lease not found"}})
    if body.term_type not in _USER_SUPPLIABLE:
        raise HTTPException(status_code=422, detail={
            "error": {"code": "unsupported_term_type",
                      "message": f"Term type must be one of: {', '.join(sorted(_USER_SUPPLIABLE))}."}})
    if body.value < 0:
        raise HTTPException(status_code=422, detail={
            "error": {"code": "invalid_value", "message": "A term value cannot be negative."}})
    if body.term_type == "escalation_rate_bp" and body.value > 10_000:
        raise HTTPException(status_code=422, detail={
            "error": {"code": "invalid_value", "message": "An escalation rate above 100% is almost certainly a typo."}})

    if body.effective_date:
        try:
            date.fromisoformat(body.effective_date)
        except ValueError:
            raise HTTPException(status_code=422, detail={
                "error": {"code": "invalid_date",
                          "message": "effective_date must be YYYY-MM-DD."}}) from None

    term = store.record_user_term(
        lease_id, user_id, body.term_type, body.value, body.note or "", body.effective_date
    )
    if not term:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Lease not found"}})
    store.log_event(user_id, "term_entered_by_user", "term", term["id"],
                    detail=f"{body.term_type} = {body.value}")
    return {"ok": True, "term": term}


@router.post("/leases/{lease_id}/audit")
def audit_lease(lease_id: str, invoice_id: str | None = Query(default=None), user_id: str = Depends(current_user)):
    limits.enforce(limits.AUDIT_LIMIT, user_id, "audits")
    lease = store.get_lease(lease_id, user_id)
    if not lease:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Lease not found"}})
    invoices = lease.get("invoices", [])
    if not invoices:
        raise HTTPException(status_code=409, detail={"error": {"code": "missing_invoice", "message": "No invoice found for this lease"}})
    inv = None
    if invoice_id:
        inv = next((i for i in invoices if i["id"] == invoice_id), None)
    else:
        inv = invoices[0]  # newest first
    if not inv:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Invoice not found"}})
    # re-fetch invoice with line items as stored
    invoice = store.get_invoice(inv["id"], user_id)
    if not invoice:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Invoice not found"}})
    result = run_audit(lease_id, invoice, user_id)
    return result


@router.get("/invoices/{invoice_id}/findings")
def get_findings(invoice_id: str, user_id: str = Depends(current_user)):
    invoice = store.get_invoice(invoice_id, user_id)
    if not invoice:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Invoice not found"}})
    findings = store.list_findings(invoice_id)
    return {
        "invoice": invoice,
        "findings": findings,
        "impact": compute_impact(findings),
    }


@router.patch("/findings/{finding_id}")
def decide_finding(finding_id: str, body: DecisionBody, user_id: str = Depends(current_user)):
    # BR11: look the finding up through its lease ownership, not by raw id.
    finding = store.get_finding_for_user(finding_id, user_id)
    if not finding:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Finding not found"}})
    if finding.get("informational"):
        raise HTTPException(status_code=409, detail={
            "error": {"code": "informational_only",
                      "message": "This charge was checked and needs no action, so it cannot be approved or rejected."}
        })
    updated = store.set_finding_decision(finding_id, user_id, body.approved)
    store.log_event(
        user_id, "finding_decision", "finding", finding_id,
        detail="approved" if body.approved else "rejected",
    )
    return {"ok": True, "finding": updated}


@router.post("/invoices/{invoice_id}/letter")
def generate_letter(invoice_id: str, language: str = Query(default="en"), user_id: str = Depends(current_user)):
    invoice = store.get_invoice(invoice_id, user_id)
    if not invoice:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Invoice not found"}})
    lease = store.get_lease(invoice["lease_id"], user_id)
    if not lease:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Lease not found"}})
    findings = store.list_findings(invoice_id)
    approved = [f for f in findings if f.get("approved") is True and not f.get("informational")]
    if not approved:
        raise HTTPException(status_code=409, detail={"error": {"code": "no_approved_findings", "message": "Approve at least one finding before generating a letter."}})
    letter = build_letter(lease, invoice, findings, language=language)
    store.log_event(user_id, "letter_generated", "invoice", invoice_id)
    return letter


@router.delete("/leases/{lease_id}")
def delete_lease(lease_id: str, user_id: str = Depends(current_user)):
    # Read the file paths first: after the delete the rows that name them are gone.
    paths = store.document_storage_paths(lease_id)
    ok = store.delete_lease(lease_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Lease not found"}})
    removed = store.purge_orphan_files(paths)
    store.log_event(user_id, "lease_deleted", "lease", lease_id)
    return {"ok": True, "files_removed": removed}
