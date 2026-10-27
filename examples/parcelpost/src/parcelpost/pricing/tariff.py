"""Weight-band tariff and surcharges (prices in EUR)."""

# (max weight in kg, price) - a parcel uses the first band it fits in.
WEIGHT_BANDS = [(1, 4.50), (2, 6.20), (5, 9.80), (10, 15.40), (20, 24.00)]
OVERWEIGHT_PER_KG = 1.35


def base_price(weight_kg: float) -> float:
    """Price for the billable weight before zone, surcharges and discounts."""
    if weight_kg <= 0:
        raise ValueError("weight must be positive")
    for limit, price in WEIGHT_BANDS:
        if weight_kg <= limit:
            return price
    heaviest_limit, heaviest_price = WEIGHT_BANDS[-1]
    extra_kg = weight_kg - heaviest_limit
    return round(heaviest_price + extra_kg * OVERWEIGHT_PER_KG, 2)


def fuel_surcharge(amount: float, fuel_index: float) -> float:
    """Fuel surcharge: 0% up to index 100, then 0.5% per index point, capped at 15%."""
    if fuel_index <= 100:
        return 0.0
    pct = min((fuel_index - 100) * 0.005, 0.15)
    return round(amount * pct, 2)
