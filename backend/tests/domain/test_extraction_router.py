"""The Groq -> heuristics router.

This path had no tests at all, and its fallback was `except Exception: pass`, so a
failing model looked exactly like a confident extraction. These tests pin both the
happy path (a quote that really appears in the document is kept) and the failure
paths (a fabricated quote is rejected, and the fallback says why).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.infra import extraction

LEASE = (
    "COMMERCIAL LEASE\n"
    "Monthly Rent of Rs. 40,000 (Rupees Forty Thousand only) per month.\n"
    "Rent shall increase by 10 percent per annum.\n"
    "Maintenance shall be capped at Rs. 4,000 per month.\n"
)


def _run(pages, kind="lease"):
    return asyncio.run(extraction.extract_terms(pages, kind))


def test_model_quote_that_exists_in_document_is_kept(monkeypatch):
    model_result = {
        "terms": [{
            "term_type": "base_rent_paise",
            "value": 4_000_000,
            "source_quote": "Monthly Rent of Rs. 40,000",
            "page": 1,
            "confidence": 0.95,
        }]
    }

    async def fake_model(text, doc_kind):
        return model_result

    monkeypatch.setattr(extraction, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(extraction, "USE_FIXTURES", False)
    monkeypatch.setattr(extraction, "extract_terms_with_groq", fake_model)

    out = _run([LEASE])
    assert out["extractor"] == "groq"
    rent = [t for t in out["terms"] if t["term_type"] == "base_rent_paise"][0]
    assert rent["value"] == 4_000_000
    assert "fallback_reason" not in out


def test_model_quote_not_in_document_is_never_used_as_evidence(monkeypatch):
    """A fabricated quote must never carry a value or be shown as the clause.

    Design note: the term is *kept* as uncertain (no value, empty quote) rather than
    dropped, so the audit can report "term not found / needs input" instead of silently
    behaving as if the rent were simply absent. What must never happen is that the
    fabricated number or quote reaches the user.
    """
    fabricated = {
        "terms": [{
            "term_type": "base_rent_paise",
            "value": 9_900_000,
            "source_quote": "Monthly Rent of Rs. 99,000",   # not in the document
            "page": 1,
            "confidence": 0.99,
        }]
    }

    async def fake_model(text, doc_kind):
        return fabricated

    monkeypatch.setattr(extraction, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(extraction, "USE_FIXTURES", False)
    monkeypatch.setattr(extraction, "extract_terms_with_groq", fake_model)

    out = _run([LEASE])
    rent = [t for t in out["terms"] if t["term_type"] == "base_rent_paise"][0]
    assert rent["value"] is None, "a fabricated quote must never yield a value"
    assert rent["source_quote"] == "", "a fabricated quote must never be shown as evidence"
    assert rent["ambiguous"] is True
    assert 9_900_000 not in [t["value"] for t in out["terms"]]
    assert "Rs. 99,000" not in json.dumps(out)


def test_model_error_falls_back_and_reports_the_reason(monkeypatch):
    async def boom(text, doc_kind):
        raise httpx_error()

    def httpx_error():
        import httpx
        return httpx.ConnectError("no route to host")

    monkeypatch.setattr(extraction, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(extraction, "USE_FIXTURES", False)
    monkeypatch.setattr(extraction, "extract_terms_with_groq", boom)

    out = _run([LEASE])
    assert out["extractor"] == "heuristic"
    assert out["fallback_reason"].startswith("the model request failed")
    assert any(t["term_type"] == "base_rent_paise" and t["value"] == 4_000_000
               for t in out["terms"])


def test_no_key_means_no_fallback_noise(monkeypatch):
    """Without a key the deterministic reader is the normal path, not a downgrade."""
    monkeypatch.setattr(extraction, "GROQ_API_KEY", "")
    out = _run([LEASE])
    assert out["extractor"] == "heuristic"
    assert "fallback_reason" not in out
