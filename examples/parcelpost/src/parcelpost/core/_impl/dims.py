"""Volumetric ("dimensional") weight. Carriers bill whichever is larger."""

VOLUMETRIC_DIVISOR = 5000  # cm^3 per kg


def volumetric_weight(dims_cm: tuple) -> float:
    """Volumetric weight in kg for (length, width, height) in centimetres."""
    if len(dims_cm) != 3 or any(d <= 0 for d in dims_cm):
        raise ValueError("dims_cm must be three positive numbers")
    length, width, height = dims_cm
    return round(length * width * height / VOLUMETRIC_DIVISOR, 2)


def billable_weight(weight_kg: float, dims_cm: tuple) -> float:
    """The weight we charge for: the larger of actual and volumetric weight, rounded up to 0.5 kg."""
    heavier = max(weight_kg, volumetric_weight(dims_cm))
    halves = int(heavier * 2)
    if halves < heavier * 2:
        halves += 1
    return halves / 2
