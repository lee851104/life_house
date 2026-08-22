import pytest

from src.features.risk import accident_severity, epanechnikov_weight


def test_epanechnikov_weight_is_zero_outside_radius():
    assert epanechnikov_weight(500, 500) == 0
    assert epanechnikov_weight(600, 500) == 0
    assert epanechnikov_weight(0, 500) == 1


def test_risk_primitives_validate_and_weight_fatalities():
    assert accident_severity(2, 20) == 41
    with pytest.raises(ValueError):
        epanechnikov_weight(1, 0)
