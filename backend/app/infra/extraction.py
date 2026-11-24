"""Extraction router: Groq first, deterministic heuristics second, fixtures only for the sample case.

Honesty rules enforced here:
1. Every term must carry a source quote that literally appears in the document.
2. Quotes that fail validation are dropped, and the term becomes uncertain (value=None).
3. Fixture values are NEVER used for user-uploaded documents — only the labelled sample case
   (sample_service) uses fixtures, so a demo upload cannot be mistaken for real analysis.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from ..core.config import EXTRACTION_MODEL, GROQ_API_KEY, USE_FIXTURES
from . import heuristics

log = logging.getLogger(__name__)

EXTRACTION_SYSTEM = """You extract lease/invoice terms into strict JSON. Return ONLY JSON.
Schema:
{
  "terms":[
    {"term_type":"base_rent_paise|escalation_rate_bp|escalation_cadence_months|maintenance_cap_paise|lease_start_date|amendment_effective_date","value":<int or null>,"source_quote":"<exact quote from document>","page":<int>,"confidence":<0-1 float>,"ambiguous":<bool>}
  ]
}
Rules:
- Use integer paise for money (Rs 40000 = 4000000). Use basis points for percent (5% = 500).
- source_quote must be copied verbatim from the document text.
- If a value cannot be grounded in text, set value null and ambiguous true.
- No extra keys."""


# Abbreviations whose trailing period is punctuation, not a word break. OCR
# commonly renders "Rs. 50,000" with no space ("Rs.50,000"); a naive whitespace
# normalisation then treats the period as a sentence end and inserts a space the
# document never had, so a quote that IS verbatim fails validation - and BR6
# then nulls a perfectly good value. Measured on a scanned lease: base rent came
# back as `None` while every other term came through.
_ABBREV = re.compile(r"\b(Rs|INR|Mr|Mrs|Ms|Dr|No|Smt|Shri)\.", re.IGNORECASE)


def _normalize_ws(s: str) -> str:
    """Collapse whitespace, but treat "Rs." + amount as one token.

    "Rs. 50,000" and "Rs.50,000" both normalise to "Rs.50,000", so a quote is
    accepted when it is verbatim apart from that one spacing difference. This
    deliberately does not loosen anything else: real differences still fail.
    """
    s = _ABBREV.sub(lambda m: m.group(1) + "\x00", s)   # protect the period
    s = re.sub(r"\s+", " ", s)                          # collapse whitespace
    s = re.sub(r"(\x00) ", r"\1", s)                    # drop space after it
    return s.replace("\x00", ".").strip()


def validate_quote(quote: str, document_text: str) -> bool:
    if not quote or not quote.strip():
        return False
    nq = _normalize_ws(quote).lower()
    nd = _normalize_ws(document_text).lower()
    return nq in nd


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    raise ValueError("No JSON found in model output")


async def extract_terms_with_groq(document_text: str, doc_kind: str) -> dict[str, Any]:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not configured")
    prompt = f"Document kind: {doc_kind}\n\nDocument text:\n{document_text[:12000]}"
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": EXTRACTION_MODEL,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": EXTRACTION_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return _extract_json(content)


def _sanitize_model_terms(result: dict[str, Any], document_text: str) -> tuple[list[dict], list[str]]:
    """Drop any term whose quote is not verbatim in the document. Never trust silently."""
    kept: list[dict] = []
    rejected: list[str] = []
    for t in result.get("terms", []) or []:
        term_type = str(t.get("term_type", "")).strip()
        if not term_type:
            continue
        quote = str(t.get("source_quote", "") or "")
        ok_quote = validate_quote(quote, document_text)
        value = t.get("value")
        try:
            value = int(value) if value is not None else None
        except (TypeError, ValueError):
            value = None
        ambiguous = bool(t.get("ambiguous")) or value is None
        if not ok_quote:
            rejected.append(term_type)
            # keep the term but as uncertain with no quote so the UI shows "needs input"
            kept.append({
                "term_type": term_type,
                "value": None,
                "source_quote": "",
                "page": int(t.get("page") or 1),
                "confidence": 0.0,
                "ambiguous": True,
            })
            continue
        kept.append({
            "term_type": term_type,
            "value": None if ambiguous else value,
            "source_quote": _normalize_ws(quote),
            "page": int(t.get("page") or 1),
            "confidence": float(t.get("confidence") or (0.9 if ok_quote else 0.2)),
            "ambiguous": ambiguous,
        })
    return kept, rejected


async def extract_terms(pages: list[str], doc_kind: str) -> dict[str, Any]:
    """Extract terms from uploaded document pages.

    Order: Groq (if key configured) -> deterministic heuristics. Fixtures are only used
    for the synthetic sample case, which does not call this function.
    """
    full_text = "\n\n".join(pages)
    fallback_reason = ""
    if GROQ_API_KEY and not USE_FIXTURES:
        try:
            result = await extract_terms_with_groq(full_text, doc_kind)
            kept, rejected = _sanitize_model_terms(result, full_text)
            if kept:
                return {"terms": kept, "extractor": "groq"}
            fallback_reason = (
                f"the model returned no quote that appears in the document "
                f"({len(rejected)} rejected)"
            )
        except Exception as exc:  # never fail the upload, but never hide the reason
            log.warning("Groq extraction failed, falling back to heuristics: %s", exc)
            fallback_reason = f"the model request failed ({type(exc).__name__})"

    result = heuristics.extract_terms_heuristic(pages, doc_kind)
    # Hard guarantee: every quote must exist in the document.
    for t in result["terms"]:
        if t.get("source_quote") and not validate_quote(t["source_quote"], full_text):
            t["value"] = None
            t["ambiguous"] = True
            t["confidence"] = 0.0
    out = {"terms": result["terms"], "extractor": "heuristic"}
    if fallback_reason:
        # Surface it: a silent downgrade to heuristics looks identical to a confident
        # model extraction, which hides real extraction failures during a demo.
        out["fallback_reason"] = fallback_reason
    return out


def extract_invoice(pages: list[str]) -> dict[str, Any]:
    """Read billed amounts, and verify every one against the document text.

    Invoice figures used to go straight into the audit unchecked, while lease
    terms were gated. That was the weakest link in the honesty story: a single
    misread bill amount would produce a confident, wrong discrepancy with no way
    for a judge - or a tenant - to tell it apart from a real one.

    A line item now carries the text it came from, and the same `validate_quote`
    gate that protects terms is applied to it. Anything that fails is returned
    with `verified: False` and a confidence of 0, and the caller surfaces it
    rather than billing it as evidence.
    """
    full_text = "\n\n".join(pages)
    items = heuristics.parse_invoice_line_items(pages)
    period_label = heuristics.parse_period_label(full_text)

    unverified: list[str] = []
    for item in items:
        quote = item.get("source_quote", "")
        ok = bool(quote) and validate_quote(quote, full_text)
        # Check the number itself, not only the line: a quote could pass while
        # the parsed amount was read from the wrong token.
        if ok:
            number = f"{item['amount_paise'] // 100:,}"
            ok = number in quote or number.replace(",", "") in quote.replace(",", "")
        item["verified"] = ok
        item["confidence"] = 1.0 if ok else 0.0
        if not ok:
            unverified.append(item.get("description", ""))

    return {
        "line_items": items,
        "period_label": period_label,
        "extractor": "heuristic",
        "unverified_items": unverified,
    }


# --- Fixtures (used ONLY by the labelled synthetic sample case) ---------------

FIXTURE_TERMS = {
    "lease": [
        {"term_type": "base_rent_paise", "value": 4000000, "source_quote": "Monthly Rent of Rs. 40,000 (Rupees Forty Thousand only)", "page": 2, "confidence": 0.99, "ambiguous": False},
        {"term_type": "escalation_rate_bp", "value": 1000, "source_quote": "rent shall increase by ten percent (10%) per annum on each anniversary of the Commencement Date", "page": 3, "confidence": 0.99, "ambiguous": False},
        {"term_type": "escalation_cadence_months", "value": 12, "source_quote": "per annum on each anniversary of the Commencement Date", "page": 3, "confidence": 0.98, "ambiguous": False},
        {"term_type": "maintenance_cap_paise", "value": 400000, "source_quote": "monthly maintenance charges shall be capped at Rs. 4,000 (Rupees Four Thousand only) per month", "page": 4, "confidence": 0.99, "ambiguous": False},
        {"term_type": "lease_start_date", "value": 20250401, "source_quote": "Commencement Date: 1 April 2025", "page": 1, "confidence": 0.99, "ambiguous": False},
    ],
    "amendment": [
        {"term_type": "escalation_rate_bp", "value": 500, "source_quote": "annual rent escalation shall not exceed five percent (5%) per annum", "page": 1, "confidence": 0.99, "ambiguous": False},
        {"term_type": "amendment_effective_date", "value": 20260101, "source_quote": "Effective Date: 1 January 2026", "page": 1, "confidence": 0.99, "ambiguous": False},
    ],
    "invoice": {
        "period_start": "2026-04-01",
        "period_label": "April 2026",
        "line_items": [
            {"description": "Monthly Rent (April 2026)", "amount_paise": 4400000, "kind": "rent"},
            {"description": "Common Area Maintenance (CAM)", "amount_paise": 520000, "kind": "maintenance"},
            {"description": "GST", "amount_paise": 0, "kind": "gst"},
        ],
    },
}


def fixture_terms_for(kind: str) -> dict[str, Any]:
    if kind == "invoice":
        return FIXTURE_TERMS["invoice"]
    return {"terms": FIXTURE_TERMS.get(kind, [])}
