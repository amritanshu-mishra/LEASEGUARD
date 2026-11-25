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
    """BR11: a finding id alone must not grant access; lookups are lease-scoped.

    Two separate browser sessions are used so this also proves the per-session
    identity actually isolates tenants, rather than both calls landing on demo-user.
    """
    from app.core.session import DEMO_SESSION
    from app.infra import store

    owner = client.post("/api/leases/sample", cookies={"lg_session": DEMO_SESSION})
    lease_id, invoice_id = owner.json()["lease_id"], owner.json()["invoice_id"]
    client.post(f"/api/leases/{lease_id}/audit?invoice_id={invoice_id}",
                cookies={"lg_session": DEMO_SESSION})
    finding_id = client.get(f"/api/invoices/{invoice_id}/findings",
                            cookies={"lg_session": DEMO_SESSION}).json()["findings"][0]["id"]

    # The owner can see it; a different session cannot.
    assert store.get_finding_for_user(finding_id, "demo-user") is not None
    other = client.get(f"/api/invoices/{invoice_id}/findings", cookies={"lg_session": DEMO_SESSION + "x"})
    # An unknown session gets a fresh identity, which owns nothing.
    assert other.status_code == 404
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


def test_real_pdf_upload_produces_a_correct_audit(client):
    """The full journey from the shipped PDFs — the path the demo actually uses."""
    from app.services.sample_service import DOC_FILES, SAMPLE_DIR

    created = client.post("/api/leases", json={
        "property_label": "PDF Journey Shop, Hazratganj",
        "start_date": "2025-04-01",
        "end_date": "2028-03-31",
    })
    lease_id = created.json()["lease_id"]

    def upload_pdf(kind, effective):
        path = SAMPLE_DIR / DOC_FILES[kind]
        files = {"file": (path.name, path.read_bytes(), "application/pdf")}
        return client.post(
            f"/api/leases/{lease_id}/documents",
            files=files,
            data={"kind": kind, "effective_date": effective},
        )

    lease_res = upload_pdf("lease", "2025-04-01")
    assert lease_res.status_code == 200, lease_res.text
    terms = {t["term_type"]: t for t in lease_res.json()["terms"]}
    assert terms["base_rent_paise"]["value"] == 40_00_000
    assert terms["escalation_rate_bp"]["value"] == 1000
    assert terms["maintenance_cap_paise"]["value"] == 4_00_000
    assert terms["maintenance_cap_paise"]["page"] == 4
    # Nothing should be uncertain when the PDF is clean.
    assert lease_res.json()["uncertain_terms"] == []

    amend_res = upload_pdf("amendment", "2026-01-01")
    assert amend_res.status_code == 200, amend_res.text
    amend_terms = {t["term_type"]: t for t in amend_res.json()["terms"]}
    assert amend_terms["escalation_rate_bp"]["value"] == 500
    assert amend_terms["amendment_effective_date"]["value"] == 20260101

    inv_res = upload_pdf("invoice", "2026-04-01")
    assert inv_res.status_code == 200, inv_res.text
    invoice_id = inv_res.json()["invoice_id"]
    assert inv_res.json()["period_label"] == "April 2026"

    audit = client.post(f"/api/leases/{lease_id}/audit?invoice_id={invoice_id}").json()
    diffs = {f["check_type"]: f for f in audit["findings"]}
    assert diffs["rent_escalation"]["difference_paise"] == 2_00_000
    assert diffs["rent_escalation"]["controlling_term"]["document_name"].startswith("Amendment_1")
    assert diffs["rent_escalation"]["controlling_term"]["effective_date"] == "2026-01-01"
    assert diffs["maintenance_cap"]["difference_paise"] == 1_20_000
    assert diffs["maintenance_cap"]["controlling_term"]["page"] == 4
    assert audit["impact"]["excess_detected_paise"] == 3_20_000

    # The letter must quote the same evidence.
    for f in audit["findings"]:
        client.patch(f"/api/findings/{f['id']}", json={"approved": True})
    letter = client.post(f"/api/invoices/{invoice_id}/letter").json()
    assert "Amendment_1" in letter["text"]
    assert "page 4" in letter["text"]
    assert "Rs. 3,200.00" in letter["text"]


def test_amount_parser_covers_indian_formats(client):
    """Formats that appear in real Indian invoices: 1,00,000 / Rs. 2.5 lakh / (Rupees ...)."""
    from app.infra import heuristics

    assert heuristics.parse_rupees("Rent Rs. 1,00,000") == 1_00_00_000     # 1 crore paise
    assert heuristics.parse_rupees("INR 62500") == 62_50_000
    assert heuristics.parse_rupees("Rent of Rs. 40,000 (Rupees Forty Thousand only)") == 40_00_000
    assert heuristics.parse_rupees("Total: ₹ 5,200/-") == 5_20_000
    assert heuristics.parse_percent_bp("escalation of 5%") == 500
    assert heuristics.parse_percent_bp("increase by ten percent") == 1000


def test_sample_is_idempotent(client):
    """Loading the demo case twice must not create a duplicate lease."""
    first = client.post("/api/leases/sample").json()
    second = client.post("/api/leases/sample").json()
    assert first["lease_id"] == second["lease_id"]
    assert second["reused"] is True
    leases = client.get("/api/leases").json()["leases"]
    labels = [l["property_label"] for l in leases]
    assert labels.count(first["lease"]["property_label"]) == 1


def test_deleting_a_lease_removes_its_uploaded_files(client):
    """A deleted lease must not leave readable rent figures on disk.

    The rows cascade, but the raw file used to survive forever. This is a privacy
    promise, not housekeeping: the user asked for the lease to be gone.
    """
    from app.core.config import UPLOAD_DIR

    created = client.post("/api/leases", json={
        "property_label": "Privacy Test Shop", "start_date": "2025-04-01", "end_date": "2028-03-31",
    }).json()
    lease_id = created["lease_id"]

    before = set(UPLOAD_DIR.iterdir())
    r = _upload(client, lease_id, "lease", "privacy.txt", LEASE_TEXT, "2025-04-01")
    assert r.status_code == 200
    written = set(UPLOAD_DIR.iterdir()) - before
    assert written, "upload should have written a file"

    deleted = client.delete(f"/api/leases/{lease_id}")
    assert deleted.status_code == 200
    assert deleted.json()["files_removed"] >= 1
    assert not (written & set(UPLOAD_DIR.iterdir())), "file survived the lease delete"


def test_shared_document_file_is_not_removed_while_still_referenced(client):
    """Two leases can share one content-addressed file; deleting one must not break the other."""
    from app.core.config import UPLOAD_DIR

    same_text = LEASE_TEXT + "\nShared upload marker.\n"
    a = client.post("/api/leases", json={
        "property_label": "Shared A", "start_date": "2025-04-01", "end_date": "2028-03-31",
    }).json()["lease_id"]
    b = client.post("/api/leases", json={
        "property_label": "Shared B", "start_date": "2025-04-01", "end_date": "2028-03-31",
    }).json()["lease_id"]
    _upload(client, a, "lease", "shared.txt", same_text, "2025-04-01")
    _upload(client, b, "lease", "shared.txt", same_text, "2025-04-01")

    client.delete(f"/api/leases/{a}")
    remaining = client.get(f"/api/leases/{b}").json()
    assert remaining["documents"], "lease B lost its document"


def test_invoice_period_ignores_due_date(client):
    """A bill 'Due 15 May 2026' for April rent must be filed as April.

    Filing it as May silently audits it against the wrong terms in force.
    """
    lease_id = client.post("/api/leases", json={
        "property_label": "Period Shop", "start_date": "2025-04-01", "end_date": "2028-03-31",
    }).json()["lease_id"]
    text = (
        "TAX INVOICE\n"
        "Due date: 15 May 2026\n"
        "Rent for the month of April 2026   Rs. 44,000\n"
        "Common Area Maintenance            Rs. 5,200\n"
    )
    r = _upload(client, lease_id, "invoice", "period.txt", text, "2026-04-01")
    assert r.status_code == 200
    body = r.json()
    assert body["period_label"] == "April 2026"
    assert body["invoice"]["period_start"] == "2026-04-01"


def test_invoice_period_overrides_a_mismatched_typed_date(client):
    """When the typed date and the printed period disagree, say so and use the document."""
    lease_id = client.post("/api/leases", json={
        "property_label": "Mismatch Shop", "start_date": "2025-04-01", "end_date": "2028-03-31",
    }).json()["lease_id"]
    text = (
        "INVOICE\nInvoice Period: June 2026\n"
        "Monthly Rent  Rs. 44,000\nCommon Area Maintenance  Rs. 5,200\n"
    )
    r = _upload(client, lease_id, "invoice", "mismatch.txt", text, "2026-09-15")
    assert r.status_code == 200
    body = r.json()
    assert body["period_label"] == "June 2026"
    assert body["invoice"]["period_start"] == "2026-06-01"
    assert any("does not match" in w for w in body["warnings"])


def test_hindi_lease_survives_the_whole_journey(client):
    """A Hindi lease + amendment + invoice must reach the same audit result in Hindi."""
    hindi_lease = (
        "दुकान किरायानामा\n"
        "यह पट्टा दिनांक 1 अप्रैल 2025 से प्रभावी है।\n"
        "धारा 1: मासिक किराया 40,000 रुपये प्रति माह है।\n"
        "धारा 2: किराये में प्रत्येक वर्ष 10 प्रतिशत की वृद्धि होगी।\n"
        "धारा 3: रखरखाव शुल्क 4,000 रुपये प्रति माह से अधिक नहीं होगा।\n"
    )
    hindi_amendment = (
        "पहला संशोधन\n"
        "प्रभावी तिथि: 1 जनवरी 2026\n"
        "धारा 1: किराये की वार्षिक वृद्धि 5 प्रतिशत से अधिक नहीं होगी।\n"
    )
    hindi_invoice = (
        "किराया चालान\nबिलिंग अवधि: अप्रैल 2026\n"
        "मासिक किराया 44,000 रुपये\nरखरखाव 5,200 रुपये\n"
    )
    lease_id = client.post("/api/leases", json={
        "property_label": "हिंदी दुकान", "start_date": "2025-04-01", "end_date": "2028-03-31",
    }).json()["lease_id"]

    for kind, text, eff in (
        ("lease", hindi_lease, "2025-04-01"),
        ("amendment", hindi_amendment, "2026-01-01"),
        ("invoice", hindi_invoice, "2026-04-01"),
    ):
        r = _upload(client, lease_id, kind, f"{kind}.txt", text, eff)
        assert r.status_code == 200, (kind, r.text)

    audit = client.post(f"/api/leases/{lease_id}/audit").json()
    findings = {f["check_type"]: f for f in audit["findings"]}
    assert findings["rent_escalation"]["status"] == "confirmed"
    assert findings["rent_escalation"]["difference_paise"] == 200_000    # Rs 2,000
    assert findings["rent_escalation"]["expected_paise"] == 4_200_000    # Rs 42,000
    assert findings["maintenance_cap"]["difference_paise"] == 120_000    # Rs 1,200
    assert audit["impact"]["excess_detected_paise"] == 320_000           # Rs 3,200


def test_unknown_api_path_is_a_real_404():
    """The SPA fallback used to answer unmatched API paths with HTTP 200.

    A typo'd endpoint, or the wrong method on a real one, looked like success to
    any client that checks `res.ok`, then failed later on a missing field. The
    fallback must not speak for the API.
    """
    c = TestClient(app)
    with c:
        miss = c.get("/api/definitely-not-a-route")
        assert miss.status_code == 404
        assert miss.json()["error"]["code"] == "not_found"

        # Wrong method on a real path is the same class of mistake. (`sample` is a
        # bad example: it is a valid lease_id for GET /leases/{id}, so that route
        # matches first and correctly answers 404 for a lease that does not exist.)
        wrong = c.put("/api/leases/sample")
        assert wrong.status_code == 405
        assert wrong.json()["error"]["code"] == "method_not_allowed"
        assert "POST" in wrong.headers["allow"]

        # The SPA itself still loads on a client-side route.
        web = c.get("/leases/anything")
        assert web.status_code == 200
        assert "text/html" in web.headers["content-type"]
