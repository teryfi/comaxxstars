from decimal import Decimal

import pytest

from app.domain import parse_stars_amount, validate_stars_amount


@pytest.mark.parametrize("value", [49, 0, -1, 100_001])
def test_invalid_numeric_quantity_is_rejected(value: int) -> None:
    with pytest.raises(ValueError):
        validate_stars_amount(value, minimum=50, maximum=100_000)


@pytest.mark.parametrize("value", [50, 51, 100_000])
def test_valid_quantity_is_accepted(value: int) -> None:
    assert validate_stars_amount(value, minimum=50, maximum=100_000) == value


@pytest.mark.parametrize("value", ["", "text", "50.5", "-50", "５０", "1e3", "999999999"])
def test_malformed_quantity_text_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        parse_stars_amount(value, minimum=50, maximum=100_000)


@pytest.mark.parametrize("value", [50.0, Decimal("50"), True])
def test_non_integer_python_values_are_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        validate_stars_amount(value, minimum=50, maximum=100_000)  # type: ignore[arg-type]
