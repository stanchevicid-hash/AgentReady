"""Public entry point: a full quote."""

from __future__ import annotations

from .core._impl.dims import billable_weight
from .core.discounts import apply_promo, loyalty_discount
from .pricing.tariff import base_price, fuel_surcharge
from .pricing.zones import zone_for, zone_multiplier


def quote(weight_kg: float, dims_cm: tuple, origin: str, dest: str, fuel_index: float = 100,
          shipments_last_year: int = 0, promo: str | None = None) -> dict:
    """Return a price breakdown in EUR."""
    weight = billable_weight(weight_kg, dims_cm)
    zone = zone_for(origin, dest)
    price = round(base_price(weight) * zone_multiplier(zone), 2)
    surcharge = fuel_surcharge(price, fuel_index)
    subtotal = round(price + surcharge, 2)
    total = apply_promo(subtotal, promo, loyalty_discount(shipments_last_year))
    return {
        "billable_weight": weight, "zone": zone, "price": price,
        "fuel_surcharge": surcharge, "subtotal": subtotal, "total": total,
    }
