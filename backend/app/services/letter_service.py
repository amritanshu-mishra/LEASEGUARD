from __future__ import annotations

from datetime import date

from ..domain.money import format_inr
from ..infra import store

CHECK_LABELS = {
    "rent_escalation": "Rent Escalation",
    "maintenance_cap": "Maintenance Cap",
}

CHECK_LABELS_HI = {
    "rent_escalation": "किराया वृद्धि",
    "maintenance_cap": "रखरखाव शुल्क सीमा",
}


def _fmt_amount(paise) -> str:
    return format_inr(int(paise)) if paise is not None else "N/A"


def _findings_block(approved: list[dict], hindi: bool) -> tuple[list[str], int]:
    lines: list[str] = []
    total = 0
    for i, f in enumerate(approved, 1):
        ct = f.get("controlling_term") or {}
        check_type = f.get("check_type", "")
        check = (CHECK_LABELS_HI if hindi else CHECK_LABELS).get(
            check_type, check_type.replace("_", " ").title()
        )
        billed = _fmt_amount(f.get("billed_paise"))
        expected = _fmt_amount(f.get("expected_paise"))
        diff_paise = int(f.get("difference_paise") or 0)
        diff = _fmt_amount(f.get("difference_paise"))
        total += diff_paise
        doc = ct.get("document_name") or ("दस्तावेज़" if hindi else "Document")
        page = ct.get("page", "?")
        eff = ct.get("effective_date", "—")
        quote = ct.get("quote", "")
        calc = f.get("calculation", "")

        if hindi:
            lines.append(f"{i}. {check}")
            lines.append(f"   बिल: {billed} | अपेक्षित: {expected} | अंतर: {diff}")
            lines.append(f"   आधार: {doc}, पृष्ठ {page}, प्रभावी {eff}")
            if quote:
                lines.append(f"   क्लॉज़ का हवाला: \"{quote}\"")
            if calc:
                lines.append(f"   गणना: {calc}")
        else:
            lines.append(f"{i}. {check}")
            lines.append(f"   Billed: {billed} | Expected: {expected} | Difference: {diff}")
            lines.append(f"   Basis: {doc}, page {page}, effective {eff}")
            if quote:
                lines.append(f"   Clause quote: \"{quote}\"")
            if calc:
                lines.append(f"   Calculation: {calc}")
        lines.append("")
    return lines, total


def build_letter(
    lease: dict,
    invoice: dict,
    findings: list[dict],
    user_name: str = "Tenant",
    language: str = "en",
) -> dict:
    """Build a dispute letter from approved findings only.

    `language` selects the letter body language so the tenant can forward the
    Hindi version on WhatsApp. Evidence (quotes, page numbers, calculations) is
    identical in both languages — only the surrounding text is translated, because
    the clause quote must stay exactly as it appears in the document.
    """
    approved = [f for f in findings if f.get("approved") is True]
    if not approved:
        raise ValueError("No approved findings")

    hindi = language.lower().startswith("hi")
    period = invoice.get("period_label", invoice.get("period_start", ""))
    prop = lease.get("property_label", "the leased premises")
    today = date.today().isoformat()

    lines: list[str] = []
    if hindi:
        lines.append("सेवा में: मकान मालिक / संपत्ति प्रबंधक")
        lines.append(f"संपत्ति: {prop}")
        lines.append(f"दिनांक: {today}")
        lines.append(f"विषय: {period} का बिल — समीक्षा हेतु विसंगतियां")
        lines.append("")
        lines.append("महोदय,")
        lines.append("")
        lines.append(
            f"मैंने {prop} के लिए {period} के बिल की पंजीकृत लीज़ और उसके संशोधनों के अनुसार जांच की है। "
            "निम्नलिखित विसंगतियां समीक्षा हेतु चिह्नित हैं:"
        )
    else:
        lines.append("To: The Landlord / Property Manager")
        lines.append(f"Property: {prop}")
        lines.append(f"Date: {today}")
        lines.append(f"Subject: Invoice {period} - Discrepancies for Review")
        lines.append("")
        lines.append("Dear Sir/Madam,")
        lines.append("")
        lines.append(
            f"I have reviewed the invoice for {period} for {prop} against the registered lease "
            "and its amendments. The following discrepancies are flagged for review:"
        )
    lines.append("")

    block, total = _findings_block(approved, hindi)
    lines.extend(block)

    if hindi:
        lines.append(f"समीक्षा हेतु कुल राशि: {format_inr(total)}")
        lines.append("")
        lines.append(
            "ये विसंगतियां समीक्षा हेतु हैं, कोई कानूनी निष्कर्ष नहीं। कृपया उपरोक्त की जांच करें "
            "और सुधारा हुआ बिल या स्पष्टीकरण भेजें।"
        )
        lines.append("")
        lines.append("सादर,")
        lines.append(user_name)
        lines.append("")
        lines.append("[यह पत्र लीज़गार्ड से बनाया गया है; जहां लागू हो सिंथेटिक/डेमो डेटा का उपयोग हुआ है। भेजने से पहले जांचें।]")
        subject = f"{period} का बिल — समीक्षा हेतु विसंगतियां"
    else:
        lines.append(f"Total amount flagged for review: {format_inr(total)}")
        lines.append("")
        lines.append(
            "These are discrepancies for review, not legal conclusions. I request you to review "
            "the above and share a corrected invoice or clarification."
        )
        lines.append("")
        lines.append("Sincerely,")
        lines.append(user_name)
        lines.append("")
        lines.append("[This letter was prepared with LeaseGuard using synthetic/demo data where applicable. Verify before sending.]")
        subject = f"Invoice {period} - Discrepancies for Review"

    text = "\n".join(lines)
    return {
        "subject": subject,
        "language": "hi" if hindi else "en",
        "text": text,
        "approved_count": len(approved),
        "total_flagged_paise": total,
        "total_flagged_display": format_inr(total),
    }
