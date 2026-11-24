"""Rate limits on the two expensive endpoints.

The public demo has no accounts, and uploading a document can take seconds per
page through OCR. Before this, a loop of uploads would occupy the single worker
and take the demo down in front of a judge.

The interesting properties are not "a 429 appears": they are that the limits are
per session (one tenant cannot exhaust another's allowance), that they are far
above real use, and that the window drains by itself rather than locking anyone
out permanently.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import limits
from app.main import app


class TestSlidingWindow:
    def test_allows_up_to_the_limit_then_refuses(self):
        window = limits.SlidingWindow(limit=3, window_seconds=60)
        assert [window.hit("a", now=0) for _ in range(3)] == [True, True, True]
        assert window.hit("a", now=0) is False

    def test_the_window_drains(self):
        window = limits.SlidingWindow(limit=2, window_seconds=60)
        assert window.hit("a", now=0) and window.hit("a", now=1)
        assert window.hit("a", now=30) is False
        # The first two events have left the window by t=61.
        assert window.hit("a", now=62) is True

    def test_keys_are_independent(self):
        window = limits.SlidingWindow(limit=1, window_seconds=60)
        assert window.hit("a", now=0) is True
        assert window.hit("a", now=0) is False
        assert window.hit("b", now=0) is True, "one session must not spend another's allowance"

    def test_retry_after_is_positive_and_bounded(self):
        window = limits.SlidingWindow(limit=1, window_seconds=60)
        window.hit("a", now=0)
        wait = window.retry_after("a", now=10)
        assert 0 < wait <= 60


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _lease(client: TestClient, label: str) -> str:
    r = client.post("/api/leases", json={"property_label": label, "start_date": "2025-04-01"})
    assert r.status_code == 200, r.text
    return r.json()["lease_id"]


def _upload(client: TestClient, lease_id: str, name: str):
    text = "COMMERCIAL LEASE\nMonthly Rent of Rs. 40,000 per month.\nCommencement Date: 1 April 2025\n"
    return client.post(
        f"/api/leases/{lease_id}/documents",
        files={"file": (name, text.encode(), "text/plain")},
        data={"kind": "lease", "effective_date": "2025-04-01"},
    )


def test_uploading_in_a_loop_is_eventually_refused(client: TestClient):
    lease_id = _lease(client, "Rate Limit Shop, Lucknow")
    responses = [_upload(client, lease_id, f"lease{i}.txt") for i in range(limits.UPLOAD_LIMIT.limit + 2)]
    codes = [r.status_code for r in responses]

    assert codes[0] == 200, codes[0]
    assert 429 in codes, f"no upload was ever rate limited: {codes}"

    refused = next(r for r in responses if r.status_code == 429)
    body = refused.json()["detail"]["error"]
    assert body["code"] == "rate_limited"
    # The message has to be usable by a tenant, not a bare status code.
    assert "wait" in body["message"].lower()
    assert refused.headers.get("Retry-After")


def test_one_session_cannot_exhaust_another_tenants_allowance(client: TestClient):
    """A rate limit keyed by IP would punish a shared phone. Keyed by session
    it only limits the caller."""
    lease_id = _lease(client, "Independent Shop, Lucknow")
    for i in range(limits.UPLOAD_LIMIT.limit):
        _upload(client, lease_id, f"fill{i}.txt")
    assert _upload(client, lease_id, "over.txt").status_code == 429

    other = TestClient(app)
    other_lease = _lease(other, "Second Tenant Shop, Lucknow")
    assert _upload(other, other_lease, "first.txt").status_code == 200


def test_the_limit_is_above_real_use(client: TestClient):
    """A tenant has a lease, an amendment and a bill. Three uploads must never
    be anywhere near the ceiling, or the product would punish normal use."""
    assert limits.UPLOAD_LIMIT.limit >= 10
    assert limits.AUDIT_LIMIT.limit >= 10
