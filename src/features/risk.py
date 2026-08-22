"""Pure risk primitives; I/O and spatial indexes remain outside these functions."""


def epanechnikov_weight(distance_m: float, radius_m: float) -> float:
    """Return a bounded distance weight for one observation."""
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")
    if distance_m >= radius_m:
        return 0.0
    return max(0.0, 1.0 - (distance_m / radius_m) ** 2)


def accident_severity(fatalities: int, fatality_weight: float) -> float:
    """Map an accident's fatality count to its configured risk weight."""
    return 1.0 + fatalities * fatality_weight
