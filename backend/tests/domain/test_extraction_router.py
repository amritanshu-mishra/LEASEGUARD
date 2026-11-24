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

    async def fake_model(text, doc_kind, repair_hint=""):
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

    async def fake_model(text, doc_kind, repair_hint=""):
        return fabricated

    monkeypatch.setattr(extraction, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(extraction, "USE_FIXTURES", False)
    monkeypatch.setattr(extraction, "extract_terms_with_groq", fake_model)

    out = _run([LEASE])

    # The thing that must never happen: the invented number, or the invented
    # quote, reaching the user in any form.
    assert 9_900_000 not in [t["value"] for t in out["terms"]]
    assert "Rs. 99,000" not in json.dumps(out)

    # What happens instead, now that a rejected answer gets one retry and then
    # falls back: the deterministic reader parses the actual document and returns
    # the true figure with a quote that really is in the file. That is better for
    # the tenant than the old behaviour, which surfaced the model's empty
    # placeholder as "uncertain" and left them to type the rent in themselves.
    rent = [t for t in out["terms"] if t["term_type"] == "base_rent_paise"][0]
    assert out["extractor"] == "heuristic"
    assert rent["value"] == 4_000_000
    assert rent["value"] != 9_900_000
    assert rent["ambiguous"] is False

    # And whatever value comes back must still be backed by text in the document.
    # This is the rule the test exists for; the value above is only acceptable
    # because the quote below proves where it came from.
    from app.infra.extraction import validate_quote

    assert validate_quote(rent["source_quote"], LEASE)

    # The downgrade is reported, not hidden.
    assert "two attempts" in out["fallback_reason"]


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


class TestOneRetryWithFeedback:
    """Build guide F2: one retry with the validation error fed back, then give up."""

    def test_a_rejected_answer_is_retried_once_with_the_reason(self, monkeypatch):
        fabricated = {
            "terms": [{
                "term_type": "base_rent_paise",
                "value": 9_900_000,
                "source_quote": "Monthly Rent of Rs. 99,000",  # not in the document
                "page": 1,
            }]
        }
        good = {
            "terms": [{
                "term_type": "base_rent_paise",
                "value": 4_000_000,
                "source_quote": "Monthly Rent of Rs. 40,000",
                "page": 1,
                "confidence": 0.95,
            }]
        }
        calls = []

        async def fake_model(text, doc_kind, repair_hint=""):
            calls.append(repair_hint)
            return fabricated if len(calls) == 1 else good

        monkeypatch.setattr(extraction, "GROQ_API_KEY", "test-key")
        monkeypatch.setattr(extraction, "USE_FIXTURES", False)
        monkeypatch.setattr(extraction, "extract_terms_with_groq", fake_model)

        out = _run([LEASE])

        assert len(calls) == 2, "should call the model exactly twice"
        assert calls[0] == "", "the first call carries no hint"
        # The hint must name the rejected term, or the model cannot correct itself.
        assert "base_rent_paise" in calls[1]
        assert out["extractor"] == "groq"
        assert out.get("retried") is True
        rent = [t for t in out["terms"] if t["term_type"] == "base_rent_paise"][0]
        assert rent["value"] == 4_000_000, "the corrected answer must be the one used"

    def test_it_gives_up_after_one_retry_and_falls_back(self, monkeypatch):
        fabricated = {
            "terms": [{
                "term_type": "base_rent_paise",
                "value": 9_900_000,
                "source_quote": "Monthly Rent of Rs. 99,000",
                "page": 1,
            }]
        }
        calls = []

        async def always_wrong(text, doc_kind, repair_hint=""):
            calls.append(1)
            return fabricated

        monkeypatch.setattr(extraction, "GROQ_API_KEY", "test-key")
        monkeypatch.setattr(extraction, "USE_FIXTURES", False)
        monkeypatch.setattr(extraction, "extract_terms_with_groq", always_wrong)

        out = _run([LEASE])

        assert len(calls) == 2, "must not retry a third time - the upload would hang"
        assert out["extractor"] == "heuristic"
        assert "two attempts" in out["fallback_reason"]
        # The fabricated number still never reaches the user.
        rent = [t for t in out["terms"] if t["term_type"] == "base_rent_paise"][0]
        assert rent["value"] != 9_900_000
