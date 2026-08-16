from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.domain import StarsOption
from app.services.customer_pricing import CustomerPricingService
from app.services.fragment_client import FragmentStarsUnitPrice
from tests.factories import make_settings


def _service(
    *,
    markup_percent: Decimal = Decimal("0"),
    standard_order_markup_percent: Decimal | None = None,
    standard_order_threshold: int = 100,
    large_order_markup_percent: Decimal | None = None,
    large_order_threshold: int = 10_000,
) -> tuple[CustomerPricingService, AsyncMock, AsyncMock, AsyncMock]:
    settings = make_settings(
        STARS_MARKUP_PERCENT=str(markup_percent),
        STARS_STANDARD_ORDER_MARKUP_PERCENT=(
            str(standard_order_markup_percent)
            if standard_order_markup_percent is not None
            else None
        ),
        STARS_STANDARD_ORDER_THRESHOLD=standard_order_threshold,
        STARS_LARGE_ORDER_MARKUP_PERCENT=(
            str(large_order_markup_percent) if large_order_markup_percent is not None else None
        ),
        STARS_LARGE_ORDER_THRESHOLD=large_order_threshold,
        STARS_PURCHASE_PROVIDER="fragment",
        FRAGMENT_PAYMENT_METHOD="ton",
    )
    fragment_client = AsyncMock()
    fragment_client.get_stars_unit_price.return_value = FragmentStarsUnitPrice(
        amount=Decimal("0.01"),
        currency="TON",
        commission_percent=Decimal("0.25"),
    )
    ton_service = AsyncMock()
    ton_service.get_ton_usd_rate.return_value = Decimal("3")
    ton_service.usd_to_ton.side_effect = lambda amount: amount / Decimal("3")
    exchange_rate_service = AsyncMock()
    exchange_rate_service.get_usd_rub_rate.return_value = Decimal("90")
    service = CustomerPricingService(
        settings,
        fragment_client,
        ton_service,
        exchange_rate_service,
    )
    return service, fragment_client, ton_service, exchange_rate_service


async def test_fragment_options_use_one_live_price_request_without_markup() -> None:
    service, fragment_client, ton_service, exchange_rate_service = _service()
    options = [
        StarsOption(stars=100, currency="RUB", amount_minor=99999),
        StarsOption(stars=500, currency="RUB", amount_minor=99999),
    ]

    priced = await service.price_options(options)

    assert [item.rub_amount for item in priced] == [Decimal("270"), Decimal("1350")]
    assert all(item.markup_percent == Decimal("0") for item in priced)
    fragment_client.get_stars_unit_price.assert_awaited_once()
    ton_service.get_ton_usd_rate.assert_awaited_once()
    exchange_rate_service.get_usd_rub_rate.assert_awaited_once()


async def test_ton_payment_quote_uses_live_fragment_price() -> None:
    service, fragment_client, _, _ = _service()
    quote = await service.quote_ton_payment(stars=123)
    assert quote.ton_amount == Decimal("1.230000000")
    assert quote.usd_amount == Decimal("3.69")
    assert quote.rub_amount == Decimal("332")
    fragment_client.get_stars_unit_price.assert_awaited_once()


async def test_confirmed_quote_is_not_repriced_for_payment() -> None:
    service, fragment_client, _, _ = _service()
    quote = await service.ton_payment_from_quote(
        stars=50,
        unit_price=Decimal("0.0150375"),
        unit_currency="USDT",
        commission_percent=Decimal("0.25"),
        markup_percent=Decimal("0"),
        rub_amount=Decimal("62"),
        quote_expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    assert quote.usd_amount == Decimal("0.75")
    assert quote.rub_amount == Decimal("62")
    fragment_client.get_stars_unit_price.assert_not_awaited()


async def test_fragment_mode_applies_markup_to_live_price_for_ton_quote() -> None:
    service, _, _, _ = _service(markup_percent=Decimal("10"))
    quote = await service.quote_ton_payment(stars=100)
    assert quote.ton_amount == Decimal("1.100000000")
    assert quote.rub_amount == Decimal("297")


async def test_usdt_fragment_price_does_not_use_ton_rate_for_rub_display() -> None:
    service, fragment_client, ton_service, _ = _service()
    fragment_client.get_stars_unit_price.return_value = FragmentStarsUnitPrice(
        amount=Decimal("0.0150375"),
        currency="USDT",
        commission_percent=Decimal("0.25"),
    )
    priced = await service.price_options(
        [StarsOption(stars=100, currency="RUB", amount_minor=99999)]
    )
    assert priced[0].rub_amount == Decimal("135")
    ton_service.get_ton_usd_rate.assert_not_awaited()


async def test_fragment_mode_applies_markup_to_live_price() -> None:
    service, fragment_client, ton_service, exchange_rate_service = _service(
        markup_percent=Decimal("15")
    )
    fragment_client.get_stars_unit_price.return_value = FragmentStarsUnitPrice(
        amount=Decimal("0.015"),
        currency="USDT",
        commission_percent=Decimal("0.25"),
    )
    priced = await service.price_options(
        [StarsOption(stars=100, currency="RUB", amount_minor=99999)]
    )
    assert priced[0].rub_amount == Decimal("155")
    assert priced[0].markup_percent == Decimal("15")
    exchange_rate_service.get_usd_rub_rate.assert_awaited_once()
    ton_service.get_ton_usd_rate.assert_not_awaited()


async def test_fragment_mode_uses_smooth_volume_discount_for_every_amount() -> None:
    service, _, _, _ = _service(
        markup_percent=Decimal("31.15"),
        standard_order_markup_percent=Decimal("25.41"),
        large_order_markup_percent=Decimal("24.59"),
    )
    amounts = [50, 51, 100, 250, 500, 1000, 2500, 5000, 9999, 10_000, 20_000]

    markups = [(await service.quote_ton_payment(stars=stars)).markup_percent for stars in amounts]

    assert markups[0] == Decimal("31.15")
    assert markups[-2:] == [Decimal("24.59"), Decimal("24.59")]
    assert all(left >= right for left, right in zip(markups, markups[1:], strict=False))
    assert len(set(markups[1:-2])) == len(markups[1:-2])


async def test_volume_discount_matches_requested_reference_prices() -> None:
    service, fragment_client, ton_service, exchange_rate_service = _service(
        markup_percent=Decimal("31.15"),
        standard_order_markup_percent=Decimal("25.41"),
        large_order_markup_percent=Decimal("24.59"),
    )
    fragment_client.get_stars_unit_price.return_value = FragmentStarsUnitPrice(
        amount=Decimal("1.22"),
        currency="USDT",
        commission_percent=Decimal("0.25"),
    )
    exchange_rate_service.get_usd_rub_rate.return_value = Decimal("1")

    small = await service.quote_option(50)
    standard = await service.quote_option(100)
    custom = await service.quote_option(777)
    large = await service.quote_option(10_000)

    assert small.rub_amount == Decimal("80")
    assert standard.rub_amount == Decimal("153")
    assert custom.markup_percent < standard.markup_percent
    assert custom.markup_percent > large.markup_percent
    assert large.rub_amount == Decimal("15200")
    ton_service.get_ton_usd_rate.assert_not_awaited()


async def test_quote_above_configured_rub_limit_is_rejected() -> None:
    service, fragment_client, _, _ = _service()
    service.settings.max_order_rub_amount = Decimal("100")
    fragment_client.get_stars_unit_price.return_value = FragmentStarsUnitPrice(
        amount=Decimal("1"),
        currency="USDT",
        commission_percent=Decimal("0"),
    )
    with pytest.raises(ValueError, match="transaction limits"):
        await service.quote_option(50)


async def test_ton_payment_above_configured_limit_is_rejected() -> None:
    service, _, _, _ = _service()
    service.settings.max_payment_ton = Decimal("0.5")
    with pytest.raises(ValueError, match="transaction limits"):
        await service.quote_ton_payment(stars=100)
