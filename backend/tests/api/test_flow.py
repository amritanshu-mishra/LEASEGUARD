"""API-level tests: the full upload -> audit -> approve -> letter journey on synthetic text."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Point the app at a throwaway DB before importing it.
_TMP = tempfile.mkdtemp(prefix="leaseguard-test-")
os.environ["DB_PATH"] = str(Path(_TMP) / "test.db")
os.environ["UPLOAD_DIR"] = str(Path(_TMP) / "uploads")
os.environ["USE_FIXTURES"] = "true"

from app.main import app  # noqa: E402  (import after env setup)

LEASE_TEXT = """COMMERCIAL LEASE DEED
Property: Shop No. 7, Sector 18 Market, Noida
Lessor: Mr. R. K. Sharma
Lessee: Ms. Neha Gupta, Proprietor, Gupta Fabrics

1. Term: The tenancy shall commence on 15 May 2024 and remain in force for 33 months.

2. Monthly Rent: The Lessee shall pay a monthly rent of Rs. 62,500 (Rupees Sixty Two Thousand Five Hundred only), exclusive of electricity.

3. Annual Increase: The rent shall be escalated by eight percent (8%) per annum on each anniversary of the commencement date.

4. Maintenance: The Lessee shall pay common area maintenance charges, which shall not exceed Rs. 6,500 (Rupees Six Thousand Five Hundred only) per month.
"""

AMENDMENT_TEXT = """AMENDMENT TO LEASE DEED
Property: Shop No. 7, Sector 18 Market, Noida
Effective Date: 1 January 2025

The annual escalation in Clause 3 is revised. Rent escalation shall not exceed six percent (6%) per annum from the next anniversary onwards.
"""

INVOICE_TEXT = """MONTHLY RENT INVOICE
Shop No. 7, Sector 18 Market, Noida
Invoice Period: June 2025

Monthly Rent (June 2025)        Rs. 67,500
Common Area Maintenance         Rs. 7,400
Electricity Charges             Rs. 1,200
"""


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _upload(client, lease_id, kind, filename, text, effective):
    files = {"file": (filename, text.encode("utf-8"), "text/plain")}
    data = {"kind": kind, "effective_date": effective}
    return client.post(f"/api/leases/{lease_id}/documents", files=files, data=data)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_upload_audit_and_letter_flow(client):
    created = client.post("/api/leases", json={
        "property_label": "Shop No. 7, Sector 18 Market, Noida",
        "start_date": "2024-05-15",
        "end_date": "2027-02-14",
    })
    assert created.status_code == 200, created.text
    lease_id = created.json()["lease_id"]

    r = _upload(client, lease_id, "lease", "lease.txt", LEASE_TEXT, "2024-05-15")
    assert r.status_code == 200, r.text
    body = r.json()
    terms = {t["term_type"]: t for t in body["terms"]}
    # Every extracted quote must be traceable to the document text.
    for t in body["terms"]:
        if t["source_quote"]:
            assert t["source_quote"].split("…")[0][:30].lower() in LEASE_TEXT.lower().replace("\n", " ")
    assert terms["base_rent_paise"]["value"] == 6250000
    assert terms["escalation_rate_bp"]["value"] == 800
    assert terms["maintenance_cap_paise"]["value"] == 650000

    r = _upload(client, lease_id, "amendment", "amend.txt", AMENDMENT_TEXT, "2025-01-01")
    assert r.status_code == 200, r.text
    assert any(t["term_type"] == "escalation_rate_bp" for t in r.json()["terms"])

    r = _upload(client, lease_id, "invoice", "invoice.txt", INVOICE_TEXT, "2025-06-01")
    assert r.status_code == 200, r.text
    invoice_id = r.json()["invoice_id"]
    kinds = {li["kind"] for li in r.json()["line_items"]}
    assert {"rent", "maintenance"} <= kinds

    audit = client.post(f"/api/leases/{lease_id}/audit?invoice_id={invoice_id}")
    assert audit.status_code == 200, audit.text
    findings = audit.json()["findings"]
    by_check = {f["check_type"]: f for f in findings}
    assert by_check["rent_escalation"]["status"] == "confirmed"
    # 6% amendment controls: 62500 * 1.06 = 66250; billed 67500 -> 1250 difference
    assert by_check["rent_escalation"]["expected_paise"] == 6625000
    assert by_check["rent_escalation"]["difference_paise"] == 125000
    assert by_check["maintenance_cap"]["difference_paise"] == 90000

    # Evidence requirements: document, page, effective date, quote, calculation.
    for f in findings:
        if f["status"] != "confirmed":
            continue
        ct = f["controlling_term"]
        assert ct["document_name"] and ct["page"] and ct["effective_date"] and ct["quote"]
        assert f["calculation"]

    impact = audit.json()["impact"]
    assert impact["excess_detected_paise"] == 215000
    assert impact["amount_flagged_paise"] >= impact["excess_detected_paise"]
    assert "Does not claim money recovered" in impact["disclaimer"]
    assert 0 < impact["time_saved_minutes"] <= 90

    # Letter must be refused before any approval.
    blocked = client.post(f"/api/invoices/{invoice_id}/letter")
    assert blocked.status_code == 409

    for f in findings:
        assert client.patch(f"/api/findings/{f['id']}", json={"approved": True}).status_code == 200

    letter = client.post(f"/api/invoices/{invoice_id}/letter")
    assert letter.status_code == 200, letter.text
    text = letter.json()["text"]
    assert "page" in text.lower()
    assert "Clause quote" in text
    assert "Calculation" in text
    assert "Rs. 2,150.00" in text
    assert "does not claim" not in text.lower() or "not legal conclusions" in text.lower()

    # Rejected findings never appear in the letter.
    client.patch(f"/api/findings/{findings[0]['id']}", json={"approved": False})
    letter2 = client.post(f"/api/invoices/{invoice_id}/letter")
    assert letter2.status_code == 200
    assert "Rs. 900.00" in letter2.json()["text"]
    assert letter2.json()["approved_count"] == 1


def test_unicode_multiline_note_duplicate_upload_rejected(client):
    created = client.post("/api/leases", json={"property_label": "Shop 9, Gomti Nagar, Lucknow"})
    lease_id = created.json()["lease_id"]
    r1 = _upload(client, lease_id, "lease", "lease.txt", LEASE_TEXT, "2024-05-15")
    assert r1.status_code == 200
    r2 = _upload(client, lease_id, "lease", "lease.txt", LEASE_TEXT, "2024-05-15")
    assert r2.status_code == 409
    assert r2.json()["detail"]["error"]["code"] == "duplicate_document"


def test_unreadable_upload_is_rejected_not_guessed(client):
    created = client.post("/api/leases", json={"property_label": "Shop 3, Aminabad, Lucknow"})
    lease_id = created.json()["lease_id"]
    files = {"file": ("scan.png", b"\x89PNG\r\n\x1a\n" + b"0" * 200, "image/png")}
    r = client.post(f"/api/leases/{lease_id}/documents", files=files, data={"kind": "lease"})
    assert r.status_code == 422
    assert r.json()["detail"]["error"]["code"] == "unreadable_document"


def test_sample_case_endpoint(client):
    r = client.post("/api/leases/sample")
    assert r.status_code == 200
    lease_id, invoice_id = r.json()["lease_id"], r.json()["invoice_id"]
    audit = client.post(f"/api/leases/{lease_id}/audit?invoice_id={invoice_id}")
    assert audit.status_code == 200
    diffs = {f["check_type"]: f["difference_paise"] for f in audit.json()["findings"]}
    assert diffs["rent_escalation"] == 200000
    assert diffs["maintenance_cap"] == 120000


def test_sample_pdf_pages_are_real(client):
    """The demo cites page numbers; they must exist in the shipped synthetic PDF."""
    from app.services.sample_service import DOC_FILES, SAMPLE_DIR

    assert SAMPLE_DIR.exists(), "samples/ directory missing"
    lease_pdf = SAMPLE_DIR / DOC_FILES["lease"]
    assert lease_pdf.exists(), "synthetic lease PDF missing; run scripts/make_samples.py"

    from pypdf import PdfReader
    reader = PdfReader(str(lease_pdf))
    assert len(reader.pages) >= 4
    # Rent clause on page 2, escalation on page 3, maintenance cap on page 4.
    assert "Monthly Rent of Rs. 40,000" in (reader.pages[1].extract_text() or "")
    assert "ten percent (10%)" in (reader.pages[2].extract_text() or "")
    assert "capped at Rs. 4,000" in (reader.pages[3].extract_text() or "")

    r = client.post("/api/leases/sample")
    lease_id, invoice_id = r.json()["lease_id"], r.json()["invoice_id"]
    audit = client.post(f"/api/leases/{lease_id}/audit?invoice_id={invoice_id}").json()
    rent = next(f for f in audit["findings"] if f["check_type"] == "rent_escalation")
    assert rent["controlling_term"]["page"] == 1  # amendment page
    maint = next(f for f in audit["findings"] if f["check_type"] == "maintenance_cap")
    assert maint["controlling_term"]["page"] == 4


def test_clean_invoice_yields_information_not_discrepancies(client):
    """G3 through the API: charges that match the lease must not become findings."""
    created = client.post("/api/leases", json={
        "property_label": "Clean Invoice Shop, Hazratganj",
        "start_date": "2024-05-15",
        "end_date": "2027-02-14",
    })
    lease_id = created.json()["lease_id"]
    # Rent 62,500 escalating 8% from 15 May 2025 => 67,500 is correct.
    lease_text = LEASE_TEXT.replace(
        "which shall not exceed Rs. 6,500 (Rupees Six Thousand Five Hundred only) per month",
        "which shall not exceed Rs. 9,000 (Rupees Nine Thousand only) per month",
    )
    assert _upload(client, lease_id, "lease", "l.txt", lease_text, "2024-05-15").status_code == 200

    # Billed exactly what the lease allows, maintenance under the cap.
    invoice_text = INVOICE_TEXT.replace("Rs. 7,400", "Rs. 8,000")
    r = _upload(client, lease_id, "invoice", "i.txt", invoice_text, "2025-06-01")
    assert r.status_code == 200, r.text
    invoice_id = r.json()["invoice_id"]

    audit = client.post(f"/api/leases/{lease_id}/audit?invoice_id={invoice_id}").json()
    findings = audit["findings"]
    assert findings, "clean charges should still be reported as information"
    assert all(f["status"] == "no_issue" for f in findings)
    assert all(f["informational"] is True for f in findings)

    impact = audit["impact"]
    assert impact["excess_detected_paise"] == 0
    assert impact["amount_flagged_paise"] == 0
    assert impact["confirmed_count"] == 0

    # An informational row can never be approved, and never reaches a letter.
    blocked = client.patch(f"/api/findings/{findings[0]['id']}", json={"approved": True})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["error"]["code"] == "informational_only"
    assert client.post(f"/api/invoices/{invoice_id}/letter").status_code == 409


def test_undercharge_is_information_not_a_finding(client):
    created = client.post("/api/leases", json={
        "property_label": "Undercharge Shop, Aminabad",
        "start_date": "2024-05-15",
        "end_date": "2027-02-14",
    })
    lease_id = created.json()["lease_id"]
    assert _upload(client, lease_id, "lease", "l.txt", LEASE_TEXT, "2024-05-15").status_code == 200

    # Rent billed below the agreed 67,500; maintenance below the cap.
    invoice_text = INVOICE_TEXT.replace("Rs. 67,500", "Rs. 60,000").replace("Rs. 7,400", "Rs. 3,000")
    r = _upload(client, lease_id, "invoice", "i.txt", invoice_text, "2025-06-01")
    invoice_id = r.json()["invoice_id"]

    audit = client.post(f"/api/leases/{lease_id}/audit?invoice_id={invoice_id}").json()
    assert {f["status"] for f in audit["findings"]} == {"no_issue"}
    assert audit["impact"]["excess_detected_paise"] == 0
    # No invented money: a negative difference must never be summed into "excess".
    assert all((f["difference_paise"] or 0) <= 0 for f in audit["findings"])


def test_audit_log_records_who_did_what(client):
    """BR10: approvals and rejections are recorded with the decision value."""
    created = client.post("/api/leases/sample")
    lease_id, invoice_id = created.json()["lease_id"], created.json()["invoice_id"]
    client.post(f"/api/leases/{lease_id}/audit?invoice_id={invoice_id}")
    findings = client.get(f"/api/invoices/{invoice_id}/findings").json()["findings"]

    client.patch(f"/api/findings/{findings[0]['id']}", json={"approved": True})
    client.patch(f"/api/findings/{findings[1]['id']}", json={"approved": False})

    events = client.get("/api/audit-log?limit=100").json()["events"]
    decisions = [e for e in events if e["action"] == "finding_decision"]
    assert {e["detail"] for e in decisions} >= {"approved", "rejected"}
    assert all(e["user_id"] for e in events)
    assert all(e["created_at"] for e in events)
    # No document text in the log.
    assert not any("monthly rent" in (e.get("detail") or "").lower() for e in events)


def test_finding_from_another_user_is_not_visible(client):
    """BR11: a finding id alone must not grant access; lookups are lease-scoped."""
    from app.infra import store

    created = client.post("/api/leases/sample")
    lease_id, invoice_id = created.json()["lease_id"], created.json()["invoice_id"]
    client.post(f"/api/leases/{lease_id}/audit?invoice_id={invoice_id}")
    finding_id = client.get(f"/api/invoices/{invoice_id}/findings").json()["findings"][0]["id"]

    assert store.get_finding_for_user(finding_id, "demo-user") is not None
    assert store.get_finding_for_user(finding_id, "someone-else") is None


def test_terms_in_force_shows_controlling_and_superseded_terms(client):
    """The lease screen must show which clause controls and which it overrode."""
    created = client.post("/api/leases/sample")
    lease_id = created.json()["lease_id"]
    lease = client.get(f"/api/leases/{lease_id}?as_of=2026-04-01").json()

    in_force = {t["term_type"]: t for t in lease["terms_in_force"]}
    # Amendment (effective 2026-01-01) controls escalation on 2026-04-01.
    assert in_force["escalation_rate_bp"]["status"] == "resolved"
    assert in_force["escalation_rate_bp"]["value"] == 500
    assert in_force["escalation_rate_bp"]["document_kind"] == "amendment"
    assert in_force["escalation_rate_bp"]["page"] == 1
    assert in_force["escalation_rate_bp"]["source_quote"]

    # Every resolved term carries the evidence the UI renders.
    for term in lease["terms_in_force"]:
        if term["status"] != "resolved":
            continue
        assert term["document_name"]
        assert term["page"] >= 1
        assert term["effective_date"]
        assert term["source_quote"]

    # The overridden 10% lease term is still visible for transparency.
    superseded = lease["superseded_terms"]
    assert any(s["value"] == 1000 for s in superseded), superseded


def test_terms_in_force_before_the_amendment_uses_the_lease(client):
    created = client.post("/api/leases/sample")
    lease_id = created.json()["lease_id"]
    lease = client.get(f"/api/leases/{lease_id}?as_of=2025-06-01").json()
    in_force = {t["term_type"]: t for t in lease["terms_in_force"]}
    # Before the amendment took effect, the lease's own 10% clause controls.
    assert in_force["escalation_rate_bp"]["value"] == 1000
    assert in_force["escalation_rate_bp"]["document_kind"] == "lease"


def test_unresolved_term_is_reported_as_uncertain_not_dropped(client):
    """A lease with no maintenance cap must surface "uncertain", not vanish."""
    created = client.post("/api/leases", json={
        "property_label": "No Cap Shop, Chowk",
        "start_date": "2024-05-15",
        "end_date": "2027-02-14",
    })
    lease_id = created.json()["lease_id"]
    # Remove the maintenance clause from the synthetic lease text.
    words_only_lease = LEASE_TEXT.replace(
        "4. Maintenance: The Lessee shall pay common area maintenance charges, which "
        "shall not exceed Rs. 6,500 (Rupees Six Thousand Five Hundred only) per month.",
        "4. Signage: The Lessee may display a signboard of reasonable size.",
    )
    assert words_only_lease != LEASE_TEXT
    assert _upload(client, lease_id, "lease", "l.txt", words_only_lease, "2024-05-15").status_code == 200

    lease = client.get(f"/api/leases/{lease_id}?as_of=2025-06-01").json()
    maint = next(t for t in lease["terms_in_force"] if t["term_type"] == "maintenance_cap_paise")
    assert maint["status"] == "uncertain"
    assert maint["reason"] == "term_not_found"
