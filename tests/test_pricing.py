from decimal import Decimal

from app.domain import StarsOption, calculate_rub_price


def test_calculate_rub_price_uses_minor_amount_and_markup() -> None:
    option = StarsOption(stars=1000, currency="USD", amount_minor=1299)

    price = calculate_rub_price(option, Decimal("90.25"), Decimal("15"))

    assert price == Decimal("1348")


def test_stars_option_rejects_non_usd_pricing() -> None:
    option = StarsOption(stars=100, currency="EUR", amount_minor=100)

    try:
        _ = option.usd_price
    except ValueError as exc:
        assert "Unsupported" in str(exc)
    else:
        raise AssertionError("Expected unsupported currency error")


def test_calculate_rub_price_uses_rub_amount_directly() -> None:
    option = StarsOption(stars=1000, currency="RUB", amount_minor=150000)

    price = calculate_rub_price(option, Decimal("90.25"), Decimal("10"))

    assert price == Decimal("1650")
