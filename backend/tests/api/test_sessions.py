"""Tenant isolation: two browsers on one deployment must not see each other's data.

Before sessions existed every request ran as `demo-user`, so anyone with the URL could
read every lease, every invoice and the entire activity trail. These tests pin the
property that matters to a tenant: my rent numbers are mine.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

LEASE = (
    "COMMERCIAL LEASE\n"
    "Monthly Rent of Rs. 40,000 per month.\n"
    "Rent shall increase by 10 percent per annum.\n"
    "Maintenance shall be capped at Rs. 4,000 per month.\n"
    "Commencement Date: 1 April 2025\n"
)


def _new_browser():
    """A distinct client with its own cookie jar models a separate person."""
    return TestClient(app)


def _make_lease(c):
    created = c.post("/api/leases", json={
        "property_label": "Private Shop", "start_date": "2025-04-01", "end_date": "2028-03-31",
    })
    assert created.status_code == 200
    lease_id = created.json()["lease_id"]
    up = c.post(
        f"/api/leases/{lease_id}/documents",
        files={"file": ("lease.txt", LEASE.encode(), "text/plain")},
        data={"kind": "lease", "effective_date": "2025-04-01"},
    )
    assert up.status_code == 200
    return lease_id


def test_each_browser_gets_its_own_identity():
    a, b = _new_browser(), _new_browser()
    with a, b:
        a.get("/api/leases")
        b.get("/api/leases")
        assert a.cookies.get("lg_session") and b.cookies.get("lg_session")
        assert a.cookies.get("lg_session") != b.cookies.get("lg_session")


def test_leases_are_not_visible_across_browsers():
    a, b = _new_browser(), _new_browser()
    with a, b:
        lease_id = _make_lease(a)

        mine = a.get("/api/leases").json()["leases"]
        theirs = b.get("/api/leases").json()["leases"]
        assert any(l["id"] == lease_id for l in mine)
        assert all(l["id"] != lease_id for l in theirs), "another browser saw my lease"

        # Direct id access must 404, not leak.
        assert a.get(f"/api/leases/{lease_id}").status_code == 200
        assert b.get(f"/api/leases/{lease_id}").status_code == 404


def test_audit_and_letter_are_scoped_to_the_owner():
    a, b = _new_browser(), _new_browser()
    with a, b:
        lease_id = _make_lease(a)
        inv = a.post(
            f"/api/leases/{lease_id}/documents",
            files={"file": ("inv.txt", b"INVOICE\nInvoice Period: April 2026\nMonthly Rent Rs. 44,000\n", "text/plain")},
            data={"kind": "invoice", "effective_date": "2026-04-01"},
        ).json()
        invoice_id = inv["invoice_id"]
        a.post(f"/api/leases/{lease_id}/audit?invoice_id={invoice_id}")

        assert b.get(f"/api/invoices/{invoice_id}/findings").status_code == 404
        assert b.post(f"/api/invoices/{invoice_id}/letter").status_code == 404
        assert b.post(f"/api/leases/{lease_id}/audit").status_code == 404
        assert b.delete(f"/api/leases/{lease_id}").status_code == 404


def test_activity_trail_is_per_tenant():
    a, b = _new_browser(), _new_browser()
    with a, b:
        _make_lease(a)
        assert a.get("/api/audit-log").json()["events"], "owner should have events"
        assert b.get("/api/audit-log").json()["events"] == [], "trail leaked across tenants"


def test_session_cookie_is_httponly():
    a = _new_browser()
    with a:
        r = a.get("/api/leases")
        cookie = r.headers.get("set-cookie", "")
        assert "httponly" in cookie.lower(), "session cookie must not be readable by JS"
        # Plain HTTP locally is same-site, so Lax is right there; over TLS the
        # cookie must be SameSite=None so it is sent inside a cross-origin iframe.
        assert "samesite=lax" in cookie.lower().replace(" ", "")


def test_cannot_delete_another_tenants_documents_from_disk():
    """Ownership is checked before the file purge, so a stranger's delete does nothing."""
    a, b = _new_browser(), _new_browser()
    from app.core.config import UPLOAD_DIR

    with a, b:
        lease_id = _make_lease(a)
        before = len(list(UPLOAD_DIR.iterdir()))
        assert b.delete(f"/api/leases/{lease_id}").status_code == 404
        assert len(list(UPLOAD_DIR.iterdir())) == before, "a stranger's request removed files"


def test_cookie_is_secure_behind_a_tls_proxy_and_not_on_plain_http():
    """A hardcoded secure=False breaks a deployed HTTPS URL.

    Behind a TLS proxy the browser is on https:// while uvicorn sees plain HTTP,
    so the flag has to come from X-Forwarded-Proto. Getting this wrong is silent:
    the cookie is not stored, every request becomes a new visitor, and the demo
    looks like it loses data.
    """
    c = TestClient(app)
    https = c.get("/api/leases", headers={"X-Forwarded-Proto": "https"})
    assert "Secure" in "; ".join(https.headers.get_list("set-cookie"))

    plain = TestClient(app).get("/api/leases")
    assert "Secure" not in "; ".join(plain.headers.get_list("set-cookie"))


def test_framed_https_app_gets_a_third_party_usable_cookie():
    """The preview renders the app in a cross-origin iframe.

    A SameSite=Lax cookie is not attached to requests from a framed page, so the
    browser is a new visitor on every call: loading the sample succeeds and the
    next page 404s. Secure+None is the only pairing browsers send in that context.
    """
    c = TestClient(app)
    r = c.get("/api/leases", headers={"X-Forwarded-Proto": "https"})
    cookie = "; ".join(r.headers.get_list("set-cookie")).lower().replace(" ", "")
    assert "samesite=none" in cookie, cookie
    assert "secure" in cookie


def test_identity_survives_when_the_browser_drops_the_cookie():
    """Embedded/ITP browsers discard third-party cookies.

    The token is also returned in a header so the page can replay it; that must be
    enough to keep reading the same lease after the cookie is gone.
    """
    with TestClient(app) as a:
        created = a.post("/api/leases", json={"property_label": "No-Cookie Shop"}).json()
        lease_id = created["lease_id"]
        token = a.get("/api/leases").headers.get("X-Session-Token")
        assert token, "server must also hand the token back in a header"

    # A fresh client that has never stored a cookie, only the header.
    b = TestClient(app)
    with b:
        b.cookies.clear()
        assert b.get(f"/api/leases/{lease_id}", headers={"X-Session-Token": token}).status_code == 200
        # ...and the successful header request re-issued the cookie, repairing the browser.
        assert b.get(f"/api/leases/{lease_id}").status_code == 200

    # Control: the header is what did it. Without it, the same id 404s.
    stranger = TestClient(app)
    with stranger:
        assert stranger.get(f"/api/leases/{lease_id}").status_code == 404


def test_a_stale_header_token_is_not_trusted():
    """An unknown token must not create a shared identity or leak anything."""
    b = TestClient(app)
    with b:
        r = b.get("/api/leases", headers={"X-Session-Token": "not-a-real-token"})
        assert r.status_code == 200
        assert r.json()["leases"] == []
        fresh = r.headers.get("X-Session-Token")
        assert fresh and fresh != "not-a-real-token"
