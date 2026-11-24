"""Reason/status codes for findings and term validation."""
from enum import Enum


class FindingStatus(str, Enum):
    CONFIRMED = "confirmed"
    UNCERTAIN = "uncertain"
    NO_ISSUE = "no_issue"


class ReasonCode(str, Enum):
    QUOTE_UNVERIFIED = "quote_unverified"
    CLAUSE_AMBIGUOUS = "clause_ambiguous"
    CONFLICTING_TERMS = "conflicting_terms"
    TERM_NOT_FOUND = "term_not_found"
    EXTRACTION_FAILED = "extraction_failed"
    LOW_CONFIDENCE = "low_confidence"


REASON_MESSAGES = {
    ReasonCode.QUOTE_UNVERIFIED: "The quoted text could not be verified in the document. Please confirm or enter the value.",
    ReasonCode.CLAUSE_AMBIGUOUS: "We could not tell what this clause means, so we have not calculated an amount. Please check the text below.",
    ReasonCode.CONFLICTING_TERMS: "Two terms with the same effective date have different values. Please choose which one applies.",
    ReasonCode.TERM_NOT_FOUND: "No applicable term was found for this charge. Nothing was assumed.",
    ReasonCode.EXTRACTION_FAILED: "We could not read this value reliably. You can retry or enter it manually.",
    ReasonCode.LOW_CONFIDENCE: "This value was hard to read. Please confirm it.",
}
