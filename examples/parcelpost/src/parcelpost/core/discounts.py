"""Customer discounts. The loyalty tiers are applied before promo codes."""

from __future__ import annotations

PROMOS = {"WELCOME10": 0.10, "SPRING15": 0.15}
MAX_TOTAL_DISCOUNT = 0.25


def loyalty_discount(shipments_last_year: int) -> float:
    """Loyalty discount rate: 5% from 10 shipments, 10% from 50, 12% from 200."""
    if shipments_last_year >= 200:
        return 0.12
    if shipments_last_year >= 50:
        return 0.10
    if shipments_last_year >= 10:
        return 0.05
    return 0.0


def apply_promo(amount: float, code: str | None, loyalty_rate: float = 0.0) -> float:
    """Apply loyalty + promo discounts. The combined discount never exceeds 25%."""
    promo = PROMOS.get((code or "").strip().upper(), 0.0)
    total = min(loyalty_rate + promo, MAX_TOTAL_DISCOUNT)
    return round(amount * (1 - total), 2)
