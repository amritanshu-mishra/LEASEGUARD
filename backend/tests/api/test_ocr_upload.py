"""API-level OCR test: a photographed lease must still produce a real audit.

This is the honest version of "OCR support": the file is a picture inside a PDF
with no text layer, the tenant is warned that a machine read it, and the audit
that comes out still matches the numbers printed in the document.
"""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_TMP = tempfile.mkdtemp(prefix="leaseguard-ocr-test-")
os.environ["DB_PATH"] = str(Path(_TMP) / "test.db")
os.environ["UPLOAD_DIR"] = str(Path(_TMP) / "uploads")
os.environ["USE_FIXTURES"] = "true"

from app.main import app  # noqa: E402
from app.infra import ocr  # noqa: E402

if ocr.installed() and not ocr.available():
    raise RuntimeError(
        "OCR is installed but cannot load: "
        f"{ocr.unavailable_reason()}. Run scripts/install_backend.sh."
    )

pytestmark = pytest.mark.skipif(not ocr.available(), reason="OCR engine not installed")


def _scanned_lease_pdf() -> bytes:
    """A PDF whose only content is a picture of a lease - i.e. a scan."""
    fitz = pytest.importorskip("pymupdf")
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 30)
    lines = [
        "COMMERCIAL LEASE DEED",
        "Monthly Rent is Rs. 40,000 per month.",
        "Rent shall increase by 10 percent per annum.",
        "Maintenance shall be capped at Rs. 4,000 per month.",
        "Commencement Date: 1 April 2025",
    ]
    img = Image.new("RGB", (1900, 400), "white")
    draw = ImageDraw.Draw(img)
    y = 30
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += 68
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    doc = fitz.open()
    page = doc.new_page(width=800, height=170)
    page.insert_image(fitz.Rect(0, 0, 800, 170), stream=buf.getvalue())
    out = io.BytesIO()
    doc.save(out)
    doc.close()
    return out.getvalue()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _lease(client: TestClient) -> str:
    r = client.post("/api/leases", json={"property_label": "Scanned Shop, Lucknow"})
    assert r.status_code in (200, 201), r.text
    return r.json()["lease"]["id"] if "lease" in r.json() else r.json()["id"]


def test_scanned_lease_is_read_and_the_audit_uses_it(client: TestClient):
    lease_id = _lease(client)
    r = client.post(
        f"/api/leases/{lease_id}/documents",
        files={"file": ("scan.pdf", _scanned_lease_pdf(), "application/pdf")},
        data={"kind": "lease", "effective_date": "2025-04-01"},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    # 1. The tenant is told a machine read a picture, not that a clean text layer
    #    was parsed. This is the difference between honest and silently wrong.
    assert body["ocr"] is True
    assert body["ocr_confidence"] is not None
    assert any("OCR" in w for w in body["warnings"]), body["warnings"]

    # 2. The headline numbers survived the round trip through pixels.
    by_type = {t["term_type"]: t for t in body["terms"]}
    assert by_type["base_rent_paise"]["value"] == 4_000_000, by_type.get("base_rent_paise")
    assert by_type["maintenance_cap_paise"]["value"] == 400_000

    # 3. And the lease screen still answers.
    lease = client.get(f"/api/leases/{lease_id}").json()
    assert lease["terms_in_force"], "audit produced no terms in force"
