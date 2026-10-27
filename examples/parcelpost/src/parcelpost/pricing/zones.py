"""Destination zones. Zone 0 is domestic; higher zones cost more."""

NEIGHBOURS = {
    "MD": {"RO", "UA"},
    "RO": {"MD", "HU", "BG", "RS", "UA"},
    "UA": {"MD", "RO", "PL", "HU", "SK"},
}
EU = {"RO", "HU", "BG", "PL", "SK", "DE", "FR", "IT", "ES", "NL", "AT", "CZ"}


def zone_for(origin: str, dest: str) -> int:
    """Return the pricing zone for a shipment.

    0 = same country, 1 = neighbouring country, 2 = EU, 3 = rest of world.
    """
    origin, dest = origin.upper(), dest.upper()
    if origin == dest:
        return 0
    if dest in NEIGHBOURS.get(origin, set()):
        return 1
    if dest in EU:
        return 2
    return 3


def zone_multiplier(zone: int) -> float:
    """Price multiplier for a zone. Unknown zones are treated as rest of world."""
    table = {0: 1.0, 1: 1.4, 2: 1.9, 3: 3.2}
    if zone < 0:
        raise ValueError("zone must be >= 0")
    return table.get(zone, table[3])
