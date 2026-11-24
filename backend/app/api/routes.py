from __future__ import annotations

import hashlib
import re
from datetime import date, datetime


from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from ..core.config import DEMO_USER_ID, MAX_UPLOAD_MB, UPLOAD_DIR
from ..infra import documents as docparse
from ..infra import extraction, store
from ..services.audit_service import compute_impact, run_audit, terms_in_force
from ..services.letter_service import build_letter
from ..services.sample_service import load_sample_case

router = APIRouter(prefix="/api")


class DecisionBody(BaseModel):
    approved: bool


class LeaseBody(BaseModel):
    property_label: str
    start_date: str | None = None
    end_date: str | None = None
    as_of: str | None = None


@router.get("/audit-log")
def audit_log(limit: int = Query(default=50, ge=1, le=500)):
    """The current user's action trail (BR10)."""
    return {"events": store.list_audit_log(DEMO_USER_ID, limit=limit)}


@router.get("/health")
def health():
    return {"ok": True, "service": "leaseguard", "synthetic_demo": True}


@router.get("/leases")
def list_leases():
    return {"leases": store.list_leases(DEMO_USER_ID)}


@router.post("/leases/sample")
def create_sample():
    result = load_sample_case(DEMO_USER_ID)
    lease = result["lease"]
    return {
        "ok": True,
        "lease_id": lease["id"],
        "invoice_id": result["invoice"]["id"],
        "lease": lease,
        "reused": bool(result.get("reused")),
    }


@router.post("/leases")
def create_lease(body: LeaseBody):
    label = (body.property_label or "").strip()
    if len(label) < 3:
        raise HTTPException(status_code=422, detail={"error": {"code": "invalid_lease", "message": "Property name is too short."}})
    lease = store.create_lease(DEMO_USER_ID, label, body.start_date or "2025-04-01", body.end_date or "2028-03-31")
    store.log_event(DEMO_USER_ID, "lease_created", "lease", lease["id"])
    return {"ok": True, "lease_id": lease["id"], "lease": lease}


@router.post("/leases/{lease_id}/documents")
async def upload_document(
    lease_id: str,
    kind: str = Form(...),
    effective_date: str | None = Form(default=None),
    file: UploadFile = File(...),
):
    """Upload a lease / amendment / invoice document and extract grounded terms."""
    lease = store.get_lease(lease_id, DEMO_USER_ID)
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
    )

    response: dict = {"ok": True, "document_id": doc["id"], "kind": kind, "page_count": len(pages), "pages": []}

    if kind == "invoice":
        parsed = extraction.extract_invoice(pages)
        if not parsed["line_items"]:
            raise HTTPException(status_code=422, detail={
                "error": {"code": "no_line_items",
                          "message": "Could not find any billed amounts in this invoice. Try a clearer PDF or paste the text."}
            })
        label = parsed["period_label"] or eff[:7]
        invoice = store.create_invoice(lease_id, doc["id"], eff, label, parsed["line_items"])
        kinds_found = {li.get("kind") for li in parsed["line_items"]}
        invoice_warnings = []
        if "rent" not in kinds_found:
            invoice_warnings.append(
                "No rent line was found in this invoice, so rent cannot be checked. "
                "If the rent is on the bill, upload a clearer copy."
            )
        if "maintenance" not in kinds_found:
            invoice_warnings.append(
                "No maintenance/CAM line was found. Only rent will be checked for this invoice."
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

        response.update({
            "terms": terms,
            "extractor": parsed.get("extractor", "heuristic"),
            "uncertain_terms": uncertain,
            "found_terms": found,
            "warnings": warnings,
        })

    # Surface per-page text so the UI can show the real page the quote came from.
    response["pages"] = [{"page": i + 1, "text": p[:4000]} for i, p in enumerate(pages)]
    store.log_event(DEMO_USER_ID, "document_uploaded", "document", doc["id"])
    return response


@router.get("/leases/{lease_id}")
def get_lease(lease_id: str, as_of: str = Query(default="2026-04-01")):
    lease = store.get_lease(lease_id, DEMO_USER_ID)
    if not lease:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Lease not found"}})
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


@router.post("/leases/{lease_id}/audit")
def audit_lease(lease_id: str, invoice_id: str | None = Query(default=None)):
    lease = store.get_lease(lease_id, DEMO_USER_ID)
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
    invoice = store.get_invoice(inv["id"])
    if not invoice:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Invoice not found"}})
    result = run_audit(lease_id, invoice, DEMO_USER_ID)
    return result


@router.get("/invoices/{invoice_id}/findings")
def get_findings(invoice_id: str):
    invoice = store.get_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Invoice not found"}})
    findings = store.list_findings(invoice_id)
    return {
        "invoice": invoice,
        "findings": findings,
        "impact": compute_impact(findings),
    }


@router.patch("/findings/{finding_id}")
def decide_finding(finding_id: str, body: DecisionBody):
    # BR11: look the finding up through its lease ownership, not by raw id.
    finding = store.get_finding_for_user(finding_id, DEMO_USER_ID)
    if not finding:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Finding not found"}})
    if finding.get("informational"):
        raise HTTPException(status_code=409, detail={
            "error": {"code": "informational_only",
                      "message": "This charge was checked and needs no action, so it cannot be approved or rejected."}
        })
    updated = store.set_finding_decision(finding_id, DEMO_USER_ID, body.approved)
    store.log_event(
        DEMO_USER_ID, "finding_decision", "finding", finding_id,
        detail="approved" if body.approved else "rejected",
    )
    return {"ok": True, "finding": updated}


@router.post("/invoices/{invoice_id}/letter")
def generate_letter(invoice_id: str, language: str = Query(default="en")):
    invoice = store.get_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Invoice not found"}})
    lease = store.get_lease(invoice["lease_id"], DEMO_USER_ID)
    if not lease:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Lease not found"}})
    findings = store.list_findings(invoice_id)
    approved = [f for f in findings if f.get("approved") is True and not f.get("informational")]
    if not approved:
        raise HTTPException(status_code=409, detail={"error": {"code": "no_approved_findings", "message": "Approve at least one finding before generating a letter."}})
    letter = build_letter(lease, invoice, findings, language=language)
    store.log_event(DEMO_USER_ID, "letter_generated", "invoice", invoice_id)
    return letter


@router.delete("/leases/{lease_id}")
def delete_lease(lease_id: str):
    ok = store.delete_lease(lease_id, DEMO_USER_ID)
    if not ok:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "Lease not found"}})
    store.log_event(DEMO_USER_ID, "lease_deleted", "lease", lease_id)
    return {"ok": True}
