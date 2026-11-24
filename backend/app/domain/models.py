"""Domain models. Pure data structures - no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

from .reasons import FindingStatus, ReasonCode


class DocumentKind(str, Enum):
    LEASE = "lease"
    AMENDMENT = "amendment"
    INVOICE = "invoice"


class TermType(str, Enum):
    BASE_RENT_PAISE = "base_rent_paise"
    ESCALATION_RATE_BP = "escalation_rate_bp"
    ESCALATION_CADENCE_MONTHS = "escalation_cadence_months"
    MAINTENANCE_CAP_PAISE = "maintenance_cap_paise"
    LEASE_START_DATE = "lease_start_date"
    AMENDMENT_EFFECTIVE_DATE = "amendment_effective_date"


class CheckType(str, Enum):
    RENT_ESCALATION = "rent_escalation"
    MAINTENANCE_CAP = "maintenance_cap"


@dataclass
class Term:
    id: str
    document_id: str
    document_name: str
    document_kind: DocumentKind
    effective_date: date
    term_type: TermType
    value: Optional[int]  # paise, basis points, or months; None if ambiguous
    source_quote: str
    page: int
    confidence: float = 1.0
    validated: bool = True
    ambiguous: bool = False
    # True when the tenant typed this value in themselves. Kept distinct from a
    # machine-extracted quote so the UI never presents a hand-entered number as
    # document evidence.
    corrected_by_user: bool = False


@dataclass
class LineItem:
    description: str
    amount_paise: int
    kind: str  # rent, maintenance, gst, other


@dataclass
class Invoice:
    id: str
    lease_id: str
    document_id: str
    period_start: date
    period_label: str
    line_items: list[LineItem] = field(default_factory=list)


@dataclass
class ControllingTermInfo:
    term_id: str
    document_name: str
    document_kind: str
    effective_date: str
    quote: str
    page: int
    value: Optional[int]


@dataclass
class Finding:
    id: str
    invoice_id: str
    check_type: CheckType
    status: FindingStatus
    billed_paise: int
    expected_paise: Optional[int]
    difference_paise: Optional[int]
    reason: Optional[ReasonCode]
    message: str
    controlling_term: Optional[ControllingTermInfo]
    calculation: str = ""
    approved: Optional[bool] = None  # None=pending, True=approved, False=rejected
    # BR8: undercharges/matches are informational rows, not discrepancies. They are
    # persisted so the tenant can see the charge was checked and found correct, but
    # they are excluded from approvals, letters and impact totals.
    informational: bool = False
