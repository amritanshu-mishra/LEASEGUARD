"""A tenant can supply a value the document could not settle.

The spec lets the user resolve an uncertain term, but until now "uncertain" was
a dead end: the screen said it could not tell, offered no way to say, and the
audit stayed blocked forever.

The interesting part is not that the endpoint stores a number. It is that a
typed number is never allowed to look like document evidence - that distinction
is the whole product.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

# A lease with no maintenance clause at all, so that term is genuinely missing
# rather than misread.
LEASE_NO_CAP = (
    "COMMERCIAL LEASE\n"
    "Monthly Rent of Rs. 40,000 per month.\n"
    "Rent shall increase by 10 percent per annum.\n"
    "Commencement Date: 1 April 2025\n"
)
INVOICE = (
    "INVOICE\nInvoice Period: April 2026\n"
    "Monthly Rent Rs. 44,000\n"
    "Common Area Maintenance Rs. 5,200\n"
)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _lease(client: TestClient, label: str = "No Cap Shop, Lucknow") -> str:
    r = client.post("/api/leases", json={"property_label": label, "start_date": "2025-04-01"})
    assert r.status_code == 200, r.text
    return r.json()["lease_id"]


def _upload(client: TestClient, lease_id: str, kind: str, text: str, name: str):
    return client.post(
        f"/api/leases/{lease_id}/documents",
        files={"file": (name, text.encode(), "text/plain")},
        data={"kind": kind, "effective_date": "2025-04-01" if kind == "lease" else "2026-04-01"},
    )


def test_a_missing_term_can_be_supplied_and_unblocks_the_audit(client: TestClient):
    lease_id = _lease(client)
    assert _upload(client, lease_id, "lease", LEASE_NO_CAP, "lease.txt").status_code == 200
    assert _upload(client, lease_id, "invoice", INVOICE, "inv.txt").status_code == 200

    before = client.get(f"/api/leases/{lease_id}").json()["terms_in_force"]
    cap = next(t for t in before if t["term_type"] == "maintenance_cap_paise")
    assert cap["status"] == "uncertain"
    assert "value" not in cap

    r = client.post(
        f"/api/leases/{lease_id}/terms",
        json={"term_type": "maintenance_cap_paise", "value": 500_000, "note": "tenant confirmed"},
    )
    assert r.status_code == 200, r.text

    after = client.get(f"/api/leases/{lease_id}").json()["terms_in_force"]
    cap = next(t for t in after if t["term_type"] == "maintenance_cap_paise")
    assert cap["status"] == "resolved"
    assert cap["value"] == 500_000

    audit = client.post(f"/api/leases/{lease_id}/audit").json()
    maintenance = next(f for f in audit["findings"] if f["check_type"] == "maintenance_cap")
    assert maintenance["status"] == "confirmed"
    # Billed 5,200 against a stated cap of 5,000 -> 200 rupees excess.
    assert audit["impact"]["excess_detected_paise"] == 20_000


def test_a_supplied_value_is_never_presented_as_document_evidence(client: TestClient):
    """The honesty rule: a typed number has no document, page or quote."""
    lease_id = _lease(client, "Honesty Shop, Lucknow")
    assert _upload(client, lease_id, "lease", LEASE_NO_CAP, "lease.txt").status_code == 200

    client.post(
        f"/api/leases/{lease_id}/terms",
        json={"term_type": "maintenance_cap_paise", "value": 400_000, "note": "from paper copy"},
    )
    row = next(
        t for t in client.get(f"/api/leases/{lease_id}").json()["terms_in_force"]
        if t["term_type"] == "maintenance_cap_paise"
    )
    assert row["entered_by_user"] is True
    assert row["document_name"] == "", "a typed value must not name a file"
    assert row["page"] is None, "a typed value must not cite a page"
    # It is still flagged as user-supplied, not as a corrected extraction.
    assert row["corrected_by_user"] is True


def test_unsupported_term_type_is_rejected(client: TestClient):
    lease_id = _lease(client, "Reject Shop, Lucknow")
    r = client.post(
        f"/api/leases/{lease_id}/terms",
        json={"term_type": "deposit_paise", "value": 100},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["error"]["code"] == "unsupported_term_type"


def test_negative_and_absurd_values_are_rejected(client: TestClient):
    lease_id = _lease(client, "Bounds Shop, Lucknow")
    negative = client.post(
        f"/api/leases/{lease_id}/terms",
        json={"term_type": "base_rent_paise", "value": -1},
    )
    assert negative.status_code == 422

    # 1000% escalation would silently blow up every future rent.
    absurd = client.post(
        f"/api/leases/{lease_id}/terms",
        json={"term_type": "escalation_rate_bp", "value": 100_000},
    )
    assert absurd.status_code == 422
    assert absurd.json()["detail"]["error"]["code"] == "invalid_value"


def test_another_tenant_cannot_supply_a_term(client: TestClient):
    a = client
    lease_id = _lease(a, "Private Shop, Lucknow")

    b = TestClient(app)
    r = b.post(
        f"/api/leases/{lease_id}/terms",
        json={"term_type": "base_rent_paise", "value": 1},
    )
    assert r.status_code == 404


def test_the_supplied_value_is_written_to_the_activity_trail(client: TestClient):
    lease_id = _lease(client, "Audited Shop, Lucknow")
    client.post(
        f"/api/leases/{lease_id}/terms",
        json={"term_type": "maintenance_cap_paise", "value": 300_000, "note": "confirmed"},
    )
    events = client.get("/api/audit-log").json()["events"]
    assert any(e.get("action") == "term_entered_by_user" for e in events), [e.get("action") for e in events]


def test_choosing_between_conflicting_clauses_resolves_the_row(client: TestClient):
    """Two amendments, same date, different rates; the tenant picks one.

    Regression: the choice was stored at the lease-start date, lost to both
    amendments under "latest clause wins", and the screen stayed uncertain - the
    button looked broken. The fix carries the chosen clause's own date, and a
    user-entered value at the controlling date now prevails over the tie.
    """
    lease_id = _lease(client, "Conflict Shop, Aminabad")
    lease_text = (
        "COMMERCIAL LEASE\nMonthly Rent of Rs. 40,000 per month.\n"
        "Rent shall increase by 10 percent per annum.\n"
        "Maintenance capped at Rs. 4,000 per month.\nCommencement Date: 1 April 2025\n"
    )
    client.post(
        f"/api/leases/{lease_id}/documents",
        files={"file": ("l.txt", lease_text.encode(), "text/plain")},
        data={"kind": "lease", "effective_date": "2025-04-01"},
    )
    for rate in ("5", "8"):
        amendment = (
            f"AMENDMENT\nEffective Date: 1 January 2026\n"
            f"Rent shall increase by {rate} percent per annum.\n"
        )
        client.post(
            f"/api/leases/{lease_id}/documents",
            files={"file": (f"a{rate}.txt", amendment.encode(), "text/plain")},
            data={"kind": "amendment", "effective_date": "2026-01-01"},
        )

    def escalation():
        rows = client.get(f"/api/leases/{lease_id}?as_of=2026-04-01").json()["terms_in_force"]
        return next(t for t in rows if t["term_type"] == "escalation_rate_bp")

    before = escalation()
    assert before["status"] == "uncertain"
    assert before["reason"] == "conflicting_terms"
    assert sorted(c["value"] for c in before["conflicts"]) == [500, 800]

    r = client.post(
        f"/api/leases/{lease_id}/terms",
        json={
            "term_type": "escalation_rate_bp",
            "value": 500,
            "note": "Use this clause: a5.txt",
            "effective_date": before["conflicts"][0]["effective_date"],
        },
    )
    assert r.status_code == 200, r.text

    after = escalation()
    assert after["status"] == "resolved", after
    assert after["value"] == 500
    assert after["entered_by_user"] is True
    assert after["document_name"] == "", "a chosen clause value must not claim a page"
