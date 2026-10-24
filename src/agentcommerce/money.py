"""Money is integer cents. Negative, fractional, non-finite and boolean amounts are rejected."""

from __future__ import annotations

import math

from .pii import SafeError

MAX_CENTS = 10_000_000  # $100,000 hard ceiling for this lab


def cents(value: object, name: str = "amount") -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SafeError(f"{name} must be a number of cents")
    if isinstance(value, float):
        if not math.isfinite(value) or int(value) != value:
            raise SafeError(f"{name} must be a whole number of cents")
        value = int(value)
    if value < 0:
        raise SafeError(f"{name} must not be negative")
    if value > MAX_CENTS:
        raise SafeError(f"{name} exceeds the lab ceiling")
    return int(value)


def fmt(c: int) -> str:
    return f"${c // 100}.{c % 100:02d}"
