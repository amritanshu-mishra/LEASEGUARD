"""Loads the labelled synthetic sample case for demos.

Synthetic data note
-------------------
All names, amounts, addresses and documents are fictional. The case is built to mirror
clause structures that are common in Indian commercial leases (monthly rent, annual
percentage escalation, a maintenance cap, and an amendment that caps escalation). Public
context on tenancy formalisation: Uttar Pradesh Regulation of Urban Premises Tenancy
Act, 2021. No real tenant, landlord or property data is used, and nothing here is
legal advice.

The documents are generated as real multi-page PDFs by scripts/make_samples.py and are
parsed back through the same PDF/text pipeline used for user uploads. That is what makes
the "page 2" / "page 4" citations in the UI verifiable: open the PDF and the quote is
on that page.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..infra import store
from ..infra.documents import DocumentParseError, parse_pages

SAMPLE_DIR = Path(__file__).resolve().parents[3] / "samples"

DOC_FILES = {
    "lease": "Lease_Agreement_Shop12_SYNTHETIC.pdf",
    "amendment": "Amendment_1_Shop12_SYNTHETIC.pdf",
    "invoice": "Invoice_April_2026_SYNTHETIC.pdf",
}


def _load_pages(kind: str) -> list[str]:
    """Read the synthetic PDF if present, else fall back to the embedded text."""
    path = SAMPLE_DIR / DOC_FILES[kind]
    if path.exists():
        try:
            return parse_pages(path.name, path.read_bytes())
        except DocumentParseError:  # pragma: no cover - only if sample file is damaged
            pass
    return [re.sub(r"\s+\n", "\n", EMBEDDED_TEXT[kind])]


EMBEDDED_TEXT = {
    "lease": """SYNTHETIC SAMPLE - NOT A REAL DOCUMENT
REGISTERED LEASE AGREEMENT
Property: Shop 12, Market Complex, Hazratganj, Lucknow, Uttar Pradesh
Lessor: M/s Sunrise Properties (Synthetic)
Lessee: Mr. Arjun Verma, Proprietor, Verma Stationery (Synthetic)

1. Term: This lease is for 36 months.
   Commencement Date: 1 April 2025
   Expiry Date: 31 March 2028

2. Rent: The Lessee shall pay a Monthly Rent of Rs. 40,000 (Rupees Forty Thousand only) payable on or before the 5th day of each month.

3. Escalation: The rent shall increase by ten percent (10%) per annum on each anniversary of the Commencement Date.

4. Maintenance: The Lessee shall pay monthly maintenance charges for common area upkeep; such monthly maintenance charges shall be capped at Rs. 4,000 (Rupees Four Thousand only) per month.

5. Late Fee: In case of delayed payment, a reasonable late charge may be applied at the discretion of the Lessor.

Signed (synthetic): Lessor / Lessee
""",
    "amendment": """SYNTHETIC SAMPLE - NOT A REAL DOCUMENT
AMENDMENT NO. 1 TO LEASE AGREEMENT
Property: Shop 12, Market Complex, Hazratganj, Lucknow, Uttar Pradesh
Original Lease Date: 1 April 2025
Effective Date: 1 January 2026

This Amendment modifies the escalation clause of the original Lease Agreement as follows:

1. Rent Escalation Cap: Notwithstanding Clause 3 of the Lease, the annual rent escalation shall not exceed five percent (5%) per annum on each anniversary of the Commencement Date.

2. All other terms and conditions of the original Lease remain in full force and effect.

Signed (synthetic): Lessor / Lessee
""",
    "invoice": """SYNTHETIC SAMPLE - NOT A REAL DOCUMENT
MONTHLY INVOICE / RENT STATEMENT
Property: Shop 12, Market Complex, Hazratganj, Lucknow
Tenant: Mr. Arjun Verma (Synthetic)
Invoice Period: April 2026
Due Date: 5 April 2026

Line Items:
- Monthly Rent (April 2026): Rs. 44,000
- Common Area Maintenance (CAM): Rs. 5,200
- GST: Rs. 0

Total Due: Rs. 49,200

Note: Rent increased by 10% as per lease terms.
""",
}

SAMPLE_TERMS = [
    # lease
    {"term_type": "base_rent_paise", "value": 4000000, "source_quote": "Monthly Rent of Rs. 40,000 (Rupees Forty Thousand only)", "page": 2, "confidence": 0.99, "validated": True, "ambiguous": False, "doc": "lease"},
    {"term_type": "escalation_rate_bp", "value": 1000, "source_quote": "rent shall increase by ten percent (10%) per annum on each anniversary of the Commencement Date", "page": 3, "confidence": 0.99, "validated": True, "ambiguous": False, "doc": "lease"},
    {"term_type": "escalation_cadence_months", "value": 12, "source_quote": "per annum on each anniversary of the Commencement Date", "page": 3, "confidence": 0.98, "validated": True, "ambiguous": False, "doc": "lease"},
    {"term_type": "maintenance_cap_paise", "value": 400000, "source_quote": "monthly maintenance charges shall be capped at Rs. 4,000 (Rupees Four Thousand only) per month", "page": 4, "confidence": 0.99, "validated": True, "ambiguous": False, "doc": "lease"},
    # amendment
    {"term_type": "escalation_rate_bp", "value": 500, "source_quote": "annual rent escalation shall not exceed five percent (5%) per annum", "page": 1, "confidence": 0.99, "validated": True, "ambiguous": False, "doc": "amendment"},
]

SAMPLE_INVOICE_ITEMS = [
    {"description": "Monthly Rent (April 2026)", "amount_paise": 4400000, "kind": "rent"},
    {"description": "Common Area Maintenance (CAM)", "amount_paise": 520000, "kind": "maintenance"},
    {"description": "GST", "amount_paise": 0, "kind": "gst"},
]


SAMPLE_LABEL = "Shop 12, Market Complex, Hazratganj, Lucknow"


def load_sample_case(user_id: str) -> dict:
    """Create the demo case, or return it unchanged if it already exists.

    Loading the sample repeatedly must be idempotent: duplicates make the dashboard
    look broken and confuse a live demo.
    """
    existing = _existing_sample(user_id)
    if existing:
        return existing

    lease = store.create_lease(user_id, SAMPLE_LABEL, "2025-04-01", "2028-03-31")
    lease_id = lease["id"]

    lease_pages = _load_pages("lease")
    amend_pages = _load_pages("amendment")
    inv_pages = _load_pages("invoice")

    lease_doc = store.create_document(
        lease_id, "lease", DOC_FILES["lease"], f"samples/{DOC_FILES['lease']}",
        "2025-04-01", f"sha-sample-lease-{store.uid()[:8]}", len(lease_pages), "\n\n".join(lease_pages),
    )
    amend_doc = store.create_document(
        lease_id, "amendment", DOC_FILES["amendment"], f"samples/{DOC_FILES['amendment']}",
        "2026-01-01", f"sha-sample-amend-{store.uid()[:8]}", len(amend_pages), "\n\n".join(amend_pages),
    )
    inv_doc = store.create_document(
        lease_id, "invoice", DOC_FILES["invoice"], f"samples/{DOC_FILES['invoice']}",
        "2026-04-01", f"sha-sample-inv-{store.uid()[:8]}", len(inv_pages), "\n\n".join(inv_pages),
    )

    lease_terms = [dict(t) for t in SAMPLE_TERMS if t["doc"] == "lease"]
    amend_terms = [dict(t) for t in SAMPLE_TERMS if t["doc"] == "amendment"]
    for t in lease_terms: t.pop("doc", None)
    for t in amend_terms: t.pop("doc", None)
    store.replace_terms_for_document(lease_doc["id"], lease_id, lease_terms)
    store.replace_terms_for_document(amend_doc["id"], lease_id, amend_terms)

    invoice = store.create_invoice(lease_id, inv_doc["id"], "2026-04-01", "April 2026", SAMPLE_INVOICE_ITEMS)

    store.log_event(user_id, "sample_loaded", "lease", lease_id)
    return {"lease": store.get_lease(lease_id, user_id), "invoice": invoice, "reused": False}


def _existing_sample(user_id: str) -> dict | None:
    for lease in store.list_leases(user_id):
        if lease.get("property_label") != SAMPLE_LABEL:
            continue
        docs = store.list_documents(lease["id"])
        if not any(d["kind"] == "invoice" for d in docs):
            continue
        invoices = store.list_invoices(lease["id"])
        if not invoices:
            continue
        return {
            "lease": store.get_lease(lease["id"], user_id),
            "invoice": invoices[0],
            "reused": True,
        }
    return None
