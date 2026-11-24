"""Money helpers. All monetary amounts are integer paise (1/100 rupee)."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


PAISE_PER_RUPEE = 100


def round_half_up_paise(value: int | Decimal) -> int:
    """Round a paisa-scale value to the nearest integer paise, half up."""
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def rupees_to_paise(rupees: int | float | Decimal | str) -> int:
    return round_half_up_paise(Decimal(str(rupees)) * PAISE_PER_RUPEE)


def paise_to_rupees_str(paise: int) -> str:
    """Format paise as Indian-grouped rupee string, e.g. 125000 -> '1,250.00'."""
    negative = paise < 0
    paise = abs(paise)
    rupees = paise // 100
    p = paise % 100
    s = str(rupees)
    # Indian grouping: last 3, then groups of 2
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        parts = []
        while rest:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        s = ",".join(parts + [last3])
    out = f"{s}.{p:02d}"
    return f"-{out}" if negative else out


def format_inr(paise: int) -> str:
    return f"Rs. {paise_to_rupees_str(paise)}"


def apply_rate(base_paise: int, rate_bp: int) -> int:
    """Apply a basis-point rate: base * (10000 + rate_bp) / 10000, half-up to paise."""
    return round_half_up_paise((Decimal(base_paise) * (Decimal(10000) + Decimal(rate_bp))) / Decimal(10000))
