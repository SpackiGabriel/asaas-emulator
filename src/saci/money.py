"""Money is stored as integer cents and only becomes a float at the JSON boundary."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def to_cents(value: Decimal | float | int | str) -> int:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"not a monetary amount: {value!r}") from exc
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_cents(cents: int) -> float:
    return float(Decimal(cents) / 100)
