"""Correcting a misread clause value.

Extraction is heuristic, so a wrong number is always possible: "Rs 4,000" read as
"Rs 40,000" silently changes the audit. Before this endpoint existed the only remedy
was to delete the lease and start over. These tests pin both the mechanism and the
honesty rules around it.
"""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.core.session import DEMO_SESSION
from app.main import app

LEASE = (
    "COMMERCIAL LEASE\n"
    "Monthly Rent of Rs. 40,000 per month.\n"
    "Rent shall increase by 10 percent per annum.\n"
    "Maintenance shall be capped at Rs. 4,000 per month.\n"
    "Commencement Date: 1 April 2025\n"
)
INVOICE = (
    "INVOICE\nInvoice Period: April 2026\n"
    "Monthly Rent Rs. 44,000\nCommon Area Maintenance Rs. 5,200\n"
)
COOKIES = {"lg_session": DEMO_SESSION}


def _lease_with_invoice():
    c = TestClient(app)
    lease_id = c.post("/api/leases", json={
        "property_label": "Correction Shop", "start_date": "2025-04-01", "end_date": "2028-03-31",
    }, cookies=COOKIES).json()["lease_id"]
    c.post(f"/api/leases/{lease_id}/documents",
           files={"file": ("l.txt", LEASE.encode(), "text/plain")},
           data={"kind": "lease", "effective_date": "2025-04-01"}, cookies=COOKIES)
    inv = c.post(f"/api/leases/{lease_id}/documents",
                 files={"file": ("i.txt", INVOICE.encode(), "text/plain")},
                 data={"kind": "invoice", "effective_date": "2026-04-01"}, cookies=COOKIES).json()
    return c, lease_id, inv["invoice_id"]


def test_correction_changes_the_audit_result():
    c, lease_id, invoice_id = _lease_with_invoice()
    with c:
        before = c.post(f"/api/leases/{lease_id}/audit?invoice_id={invoice_id}", cookies=COOKIES).json()
        cap_before = [f for f in before["findings"] if f["check_type"] == "maintenance_cap"][0]
        assert cap_before["expected_paise"] == 400_000      # Rs 4,000 as read

        terms = c.get(f"/api/leases/{lease_id}", cookies=COOKIES).json()["terms_in_force"]
        cap_term = [t for t in terms if t["term_type"] == "maintenance_cap_paise"][0]
        assert cap_term.get("term_id"), "the UI needs a term id to offer a correction"

        fixed = c.patch(f"/api/terms/{cap_term['term_id']}",
                        json={"value": 600_000, "note": "Lease says Rs 6,000"},
                        cookies=COOKIES)
        assert fixed.status_code == 200
        assert fixed.json()["term"]["corrected_by_user"] is True

        after = c.post(f"/api/leases/{lease_id}/audit?invoice_id={invoice_id}", cookies=COOKIES).json()
        cap_after = [f for f in after["findings"] if f["check_type"] == "maintenance_cap"][0]
        assert cap_after["expected_paise"] == 600_000
        # Billed 5,200 is now BELOW the cap: an undercharge, so no excess is claimed.
        assert cap_after["status"] == "no_issue"
        assert after["impact"]["excess_detected_paise"] < before["impact"]["excess_detected_paise"]

        # The flag must reach the lease screen, otherwise the UI shows a hand-entered
        # number as if it had been read from the document.
        terms_after = c.get(f"/api/leases/{lease_id}", cookies=COOKIES).json()["terms_in_force"]
        cap_now = [t for t in terms_after if t["term_type"] == "maintenance_cap_paise"][0]
        assert cap_now["corrected_by_user"] is True
        other = [t for t in terms_after if t["term_type"] == "base_rent_paise"][0]
        assert other["corrected_by_user"] is False


def test_corrected_term_is_marked_so_it_is_not_shown_as_extracted_evidence():
    c, lease_id, _invoice_id = _lease_with_invoice()
    with c:
        terms = c.get(f"/api/leases/{lease_id}", cookies=COOKIES).json()["terms_in_force"]
        term_id = [t for t in terms if t["term_type"] == "base_rent_paise"][0]["term_id"]
        r = c.patch(f"/api/terms/{term_id}", json={"value": 4_500_000}, cookies=COOKIES)
        assert r.status_code == 200
        assert r.json()["term"]["corrected_by_user"] is True
        assert r.json()["term"]["validated"] is True


def test_cannot_correct_another_tenants_term():
    c, lease_id, _ = _lease_with_invoice()
    with c:
        terms = c.get(f"/api/leases/{lease_id}", cookies=COOKIES).json()["terms_in_force"]
        term_id = [t for t in terms if t["term_type"] == "base_rent_paise"][0]["term_id"]
        stranger = c.patch(f"/api/terms/{term_id}", json={"value": 1},
                           cookies={"lg_session": "not-a-real-session"})
        assert stranger.status_code == 404
        # And the value is unchanged for the owner.
        still = [t for t in c.get(f"/api/leases/{lease_id}", cookies=COOKIES).json()["terms_in_force"]
                 if t["term_type"] == "base_rent_paise"][0]
        assert still["value"] == 4_000_000


def test_negative_value_is_rejected():
    c, lease_id, _ = _lease_with_invoice()
    with c:
        terms = c.get(f"/api/leases/{lease_id}", cookies=COOKIES).json()["terms_in_force"]
        term_id = [t for t in terms if t["term_type"] == "base_rent_paise"][0]["term_id"]
        r = c.patch(f"/api/terms/{term_id}", json={"value": -5}, cookies=COOKIES)
        assert r.status_code == 422
        assert "negative" in r.json()["detail"]["error"]["message"].lower()


def test_decision_must_be_a_real_boolean():
    """`{"approved": "yes"}` used to return 200.

    Pydantic's lax mode coerces the string "yes" to True, and "false" (a string) to
    False. So a malformed client request or a hand-written curl silently decided a
    finding, and an approval ends up in a letter to the landlord. A typo must be a
    422, never a decision.
    """
    c, lease_id, invoice_id = _lease_with_invoice()
    with c:
        audit = c.post(f"/api/leases/{lease_id}/audit?invoice_id={invoice_id}", cookies=COOKIES).json()
        finding_id = audit["findings"][0]["id"]

        for bad in ("yes", "true", "false", 1, 0, None):
            r = c.patch(f"/api/findings/{finding_id}", json={"approved": bad}, cookies=COOKIES)
            assert r.status_code == 422, f"accepted {bad!r} as a decision"

        # A real boolean still works, in both directions.
        assert c.patch(f"/api/findings/{finding_id}", json={"approved": True}, cookies=COOKIES).status_code == 200
        assert c.patch(f"/api/findings/{finding_id}", json={"approved": False}, cookies=COOKIES).status_code == 200


def test_letter_language_must_be_supported():
    """An unsupported language used to return 200 in English.

    A Hindi-only reader would have sent `?language=hin` and received an English
    letter that looked like a success. Reject it instead of silently answering a
    different question.
    """
    c, lease_id, invoice_id = _lease_with_invoice()
    with c:
        audit = c.post(f"/api/leases/{lease_id}/audit?invoice_id={invoice_id}", cookies=COOKIES).json()
        # A letter needs at least one approved finding; that gate is deliberate.
        c.patch(f"/api/findings/{audit['findings'][0]['id']}", json={"approved": True}, cookies=COOKIES)
        for bad in ("fr", "EN", "", "hin", "en-US", " hi"):
            r = c.post(f"/api/invoices/{invoice_id}/letter?language={bad}", cookies=COOKIES)
            assert r.status_code == 422, f"accepted language={bad!r}"

        ok_en = c.post(f"/api/invoices/{invoice_id}/letter?language=en", cookies=COOKIES)
        assert ok_en.status_code == 200
        ok_hi = c.post(f"/api/invoices/{invoice_id}/letter?language=hi", cookies=COOKIES)
        assert ok_hi.status_code == 200
        assert ok_en.json()["language"] == "en"
        assert ok_hi.json()["language"] == "hi"
        # The two letters must actually differ: a "successful" Hindi request that
        # returns English text is the failure this endpoint used to hide.
        assert ok_hi.json()["text"] != ok_en.json()["text"]

        # Omitting the parameter keeps working (English default).
        assert c.post(f"/api/invoices/{invoice_id}/letter", cookies=COOKIES).status_code == 200


def test_a_blank_correction_cannot_erase_a_clause_value():
    """`PATCH /terms/{id}` with `{}` used to return 200 and write NULL.

    The store passes `value` straight into SQL, so an empty body erased the
    extracted base rent (Rs 40,000 -> nothing) and still reported success. A
    correction is an edit; if the tenant means to remove the lease there is a
    delete for that.
    """
    c, lease_id, invoice_id = _lease_with_invoice()
    with c:
        before = c.get(f"/api/leases/{lease_id}", cookies=COOKIES).json()["terms_in_force"]
        rent = [t for t in before if t["term_type"] == "base_rent_paise"][0]
        assert rent["value"] == 4_000_000

        for body in ({}, {"value": None}):
            r = c.patch(f"/api/terms/{rent['term_id']}", json=body, cookies=COOKIES)
            assert r.status_code == 422, f"accepted {body} and would have cleared the term"
            assert r.json()["detail"]["error"]["code"] == "missing_value"

        after = c.get(f"/api/leases/{lease_id}", cookies=COOKIES).json()["terms_in_force"]
        rent_after = [t for t in after if t["term_type"] == "base_rent_paise"][0]
        assert rent_after["value"] == 4_000_000, "the rejected request still changed the value"


def test_a_correction_value_must_be_a_real_number():
    """A string was coerced by pydantic, so `"40000"` silently became a correction.

    Lax coercion hides a client bug until the number is in a letter. Require an
    actual integer; the UI already sends one.
    """
    c, lease_id, invoice_id = _lease_with_invoice()
    with c:
        terms = c.get(f"/api/leases/{lease_id}", cookies=COOKIES).json()["terms_in_force"]
        term_id = [t for t in terms if t["term_type"] == "base_rent_paise"][0]["term_id"]

        for bad in ("40000", 1.5, True, [40000], {"v": 1}):
            r = c.patch(f"/api/terms/{term_id}", json={"value": bad}, cookies=COOKIES)
            assert r.status_code == 422, f"accepted {bad!r} as a term value"

        ok = c.patch(f"/api/terms/{term_id}", json={"value": 4_100_000}, cookies=COOKIES)
        assert ok.status_code == 200
        assert ok.json()["term"]["value_integer"] == 4_100_000
