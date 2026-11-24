"""Generate the synthetic demo documents as real, multi-page PDFs.

Why: the demo cites "page 2", "page 4" as evidence. Those page numbers must be
verifiable by anyone who opens the PDF — so the sample case is built from actual
PDF files, parsed back with the same code path used for user uploads.

Everything here is fictional. No real tenant, landlord, or property is referenced.
Run:  python scripts/make_samples.py
"""
from __future__ import annotations

import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"


def _esc(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _wrap(lines: list[str], width: int = 92) -> list[str]:
    out: list[str] = []
    for line in lines:
        if len(line) <= width:
            out.append(line)
            continue
        words = line.split()
        cur = ""
        for w in words:
            if len(cur) + len(w) + 1 > width:
                out.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            out.append(cur)
    return out


def _content(objects: list[str], lines: list[str]) -> str:
    body = ["BT", "/F1 11 Tf", "16 TL", "60 780 Td"]
    for line in _wrap(lines):
        safe = _esc(line)
        if safe.strip() == "":
            body.append("T*")
        else:
            body.append(f"({safe}) Tj T*")
    body.append("ET")
    return "\n".join(body)


def build_pdf(pages: list[list[str]]) -> bytes:
    """Return bytes for a text-only PDF with one list of lines per page."""
    objects: list[bytes] = []

    def add(obj: str) -> int:
        objects.append(obj.encode("latin-1", errors="replace"))
        return len(objects)  # 1-based object number

    # Reserve object numbers: 1 catalog, 2 pages, 3 font, then page/content pairs.
    n_pages = len(pages)
    page_ids = [4 + 2 * i for i in range(n_pages)]
    content_ids = [5 + 2 * i for i in range(n_pages)]

    add("<< /Type /Catalog /Pages 2 0 R >>")  # 1
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    add(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>")  # 2
    add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")  # 3

    for i, page_lines in enumerate(pages):
        stream = _content(objects, page_lines)
        compressed = zlib.compress(stream.encode("latin-1", errors="replace"))
        add(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_ids[i]} 0 R >>"
        )
        objects.append(
            b"<< /Length " + str(len(compressed)).encode() + b" /Filter /FlateDecode >>\nstream\n"
            + compressed + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


# --- Synthetic document content (fictional) ---------------------------------

LEASE_PAGES = [
    [
        "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT",
        "REGISTERED LEASE AGREEMENT",
        "",
        "Property: Shop 12, Market Complex, Hazratganj, Lucknow, Uttar Pradesh",
        "Lessor: M/s Sunrise Properties (Synthetic)",
        "Lessee: Mr. Arjun Verma, Proprietor, Verma Stationery (Synthetic)",
        "",
        "This lease is executed between the parties named above. All names,",
        "amounts and addresses in this document are fictional and exist only",
        "for software demonstration.",
    ],
    [
        "1. TERM",
        "The lease is granted for a period of 36 (thirty-six) months.",
        "Commencement Date: 1 April 2025",
        "Expiry Date: 31 March 2028",
        "",
        "2. MONTHLY RENT",
        "The Lessee shall pay a Monthly Rent of Rs. 40,000 (Rupees Forty Thousand",
        "only) payable on or before the 5th day of each English calendar month.",
        "Rent shall be paid by bank transfer to the account nominated by the Lessor.",
    ],
    [
        "3. ANNUAL ESCALATION",
        "The rent shall increase by ten percent (10%) per annum on each anniversary",
        "of the Commencement Date.",
        "",
        "4. UTILITIES",
        "Electricity and water charges shall be borne by the Lessee as per actual",
        "consumption and the relevant utility bills.",
    ],
    [
        "5. MAINTENANCE",
        "The Lessee shall pay monthly maintenance charges for common area upkeep;",
        "such monthly maintenance charges shall be capped at Rs. 4,000 (Rupees Four",
        "Thousand only) per month.",
        "",
        "6. LATE PAYMENT",
        "In case of delayed payment, a reasonable late charge may be applied at the",
        "discretion of the Lessor.",
        "",
        "Signed (synthetic): ____________________  Lessor",
        "Signed (synthetic): ____________________  Lessee",
    ],
]

AMENDMENT_PAGES = [
    [
        "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT",
        "AMENDMENT NO. 1 TO LEASE AGREEMENT",
        "",
        "Property: Shop 12, Market Complex, Hazratganj, Lucknow, Uttar Pradesh",
        "Original Lease Date: 1 April 2025",
        "Effective Date: 1 January 2026",
        "",
        "This Amendment modifies the escalation clause of the original Lease",
        "Agreement as follows:",
        "",
        "1. RENT ESCALATION CAP",
        "Notwithstanding Clause 3 of the Lease, the annual rent escalation shall not",
        "exceed five percent (5%) per annum on each anniversary of the Commencement",
        "Date.",
        "",
        "2. OTHER TERMS",
        "All other terms and conditions of the original Lease remain in full force",
        "and effect.",
        "",
        "Signed (synthetic): ____________________  Lessor",
        "Signed (synthetic): ____________________  Lessee",
    ],
]

INVOICE_PAGES = [
    [
        "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT",
        "MONTHLY INVOICE / RENT STATEMENT",
        "",
        "Property: Shop 12, Market Complex, Hazratganj, Lucknow",
        "Tenant: Mr. Arjun Verma (Synthetic)",
        "Invoice Period: April 2026",
        "Due Date: 5 April 2026",
        "",
        "LINE ITEMS",
        "Monthly Rent (April 2026)              Rs. 44,000",
        "Common Area Maintenance (CAM)          Rs. 5,200",
        "GST                                     Rs. 0",
        "",
        "TOTAL DUE                              Rs. 49,200",
        "",
        "Note: Rent increased by 10% as per lease terms.",
        "This invoice is synthetic and is not a demand for payment.",
    ],
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "Lease_Agreement_Shop12_SYNTHETIC.pdf": LEASE_PAGES,
        "Amendment_1_Shop12_SYNTHETIC.pdf": AMENDMENT_PAGES,
        "Invoice_April_2026_SYNTHETIC.pdf": INVOICE_PAGES,
    }
    for name, pages in files.items():
        data = build_pdf(pages)
        (OUT / name).write_bytes(data)
        print(f"wrote samples/{name} ({len(data)} bytes, {len(pages)} pages)")


if __name__ == "__main__":
    main()
