import base64
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database.models import Order
from app.domain import OrderKind
from app.providers.fragment_stars import FRAGMENT_MIN_STARS, FragmentStarsPurchaseProvider
from app.services.fragment_client import (
    FragmentClient,
    FragmentPurchaseResponse,
    normalize_fragment_cookies,
    normalize_username,
    normalize_wallet_seed,
)
from tests.factories import TEST_SEED, make_settings


def _order(**kwargs: object) -> Order:
    defaults: dict[str, object] = {
        "id": 1,
        "buyer_username": "buyer",
        "recipient_username": None,
        "stars": 100,
        "kind": OrderKind.SELF,
    }
    defaults.update(kwargs)
    order = MagicMock(spec=Order)
    for key, value in defaults.items():
        setattr(order, key, value)
    return order


def _fragment_settings(*, method: str = "ton", mode: str = "no_kyc"):
    return make_settings(
        REAL_STARS_PURCHASE_ENABLED=True,
        STARS_PURCHASE_PROVIDER="fragment",
        FRAGMENT_WALLET_SEED=TEST_SEED,
        FRAGMENT_PAYMENT_METHOD=method,
        FRAGMENT_API_MODE=mode,
    )


def test_normalize_username_adds_at_sign() -> None:
    assert normalize_username("buyer") == "@buyer"
    assert normalize_username("@buyer") == "@buyer"


@pytest.mark.parametrize("value", ["bad", "<b>buyer</b>", "user-name", "a" * 33])
def test_normalize_username_rejects_malformed_input(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_username(value)


def test_normalize_wallet_seed_accepts_only_24_words() -> None:
    encoded = normalize_wallet_seed(TEST_SEED)
    assert normalize_wallet_seed(encoded) == encoded
    with pytest.raises(ValueError):
        normalize_wallet_seed("one two three")


def _encoded_fragment_cookies(**overrides: str) -> str:
    payload = {
        "stel_token": "token",
        "stel_ssid": "ssid",
        "stel_ton_token": "ton-token",
        "stel_dt": "-180",
    }
    payload.update(overrides)
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def test_fragment_kyc_cookies_require_base64_json_and_all_fields() -> None:
    encoded = _encoded_fragment_cookies()
    assert normalize_fragment_cookies(encoded) == encoded
    with pytest.raises(ValueError, match="base64-encoded JSON"):
        normalize_fragment_cookies("not-base64")
    with pytest.raises(ValueError, match="stel_ton_token"):
        normalize_fragment_cookies(_encoded_fragment_cookies(stel_ton_token=""))


def test_fragment_client_accepts_valid_kyc_cookie_bundle() -> None:
    settings = make_settings(
        FRAGMENT_API_MODE="kyc",
        FRAGMENT_COOKIES=_encoded_fragment_cookies(),
    )
    client = FragmentClient(settings)
    assert client.uses_kyc is True


async def test_fragment_rejects_orders_below_minimum() -> None:
    provider = FragmentStarsPurchaseProvider(_fragment_settings(), AsyncMock())
    result = await provider.purchase_for_self(_order(stars=FRAGMENT_MIN_STARS - 1))
    assert result.succeeded is False
    assert result.error_code == "AMOUNT_BELOW_MINIMUM"


async def test_fragment_requires_buyer_username() -> None:
    provider = FragmentStarsPurchaseProvider(_fragment_settings(), AsyncMock())
    result = await provider.purchase_for_self(_order(buyer_username=None))
    assert result.succeeded is False
    assert result.error_code == "RECIPIENT_USERNAME_MISSING"


async def test_fragment_starts_purchase_without_waiting() -> None:
    client = AsyncMock(spec=FragmentClient)
    client.start_stars_purchase.return_value = FragmentPurchaseResponse(
        success=False,
        pending=True,
        request_id="req123",
    )
    provider = FragmentStarsPurchaseProvider(_fragment_settings(), client)
    result = await provider.purchase_for_self(_order())
    assert result.pending is True
    assert result.request_id == "req123"
    client.start_stars_purchase.assert_awaited_once_with(username="buyer", amount=100)


async def test_fragment_maps_definitive_api_error() -> None:
    client = AsyncMock(spec=FragmentClient)
    client.start_stars_purchase.return_value = FragmentPurchaseResponse(
        success=False,
        error_code="INSUFFICIENT_BALANCE",
        error="Fragment purchase failed",
    )
    provider = FragmentStarsPurchaseProvider(_fragment_settings(), client)
    result = await provider.purchase_as_gift(
        _order(kind=OrderKind.GIFT, recipient_username="friend")
    )
    assert result.succeeded is False
    assert result.uncertain is False
    assert result.error_code == "INSUFFICIENT_BALANCE"


async def test_fragment_reads_live_ton_price_with_api_commission() -> None:
    client = FragmentClient(_fragment_settings(method="ton"))
    client.get_prices = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "stars": {
                "price_per_star_ton": "0.011218000",
                "price_with_commission_no_kyc": "0.011246045",
            },
            "commission_rate_no_kyc": 0.25,
            "cached_at": "2026-08-10T13:35:23.876Z",
        }
    )
    price = await client.get_stars_unit_price()
    assert price.currency == "TON"
    assert price.amount == Decimal("0.011246045")
    assert price.commission_percent == Decimal("0.25")


async def test_fragment_calculates_usdt_price_with_commission() -> None:
    client = FragmentClient(_fragment_settings(method="usdt_ton"))
    client.get_prices = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "stars": {"price_per_star_usdt_ton": "0.015000"},
            "commission_rate_no_kyc": 0.25,
        }
    )
    price = await client.get_stars_unit_price()
    assert price.currency == "USDT"
    assert price.amount == Decimal("0.0150375000")


async def test_fragment_rejects_invalid_price_or_commission() -> None:
    client = FragmentClient(_fragment_settings(method="usdt_ton"))
    client.get_prices = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "stars": {"price_per_star_usdt_ton": "0.015"},
            "commission_rate_no_kyc": "NaN",
        }
    )
    with pytest.raises(RuntimeError, match="commission"):
        await client.get_stars_unit_price()


async def test_status_network_error_is_safe_to_retry_by_request_id() -> None:
    client = FragmentClient(_fragment_settings())
    sdk = MagicMock()
    sdk.__enter__.return_value.get_status.side_effect = TimeoutError
    with patch("fragment_api.FragmentAPIClient", return_value=sdk):
        result = await client.get_purchase_status("req-1")
    assert result.pending is True
    assert result.uncertain is False
    assert result.request_id == "req-1"


async def test_completed_status_without_reference_is_uncertain() -> None:
    client = FragmentClient(_fragment_settings())
    status = MagicMock(status="completed", result={"success": True})
    sdk = MagicMock()
    sdk.__enter__.return_value.get_status.return_value = status
    with patch("fragment_api.FragmentAPIClient", return_value=sdk):
        result = await client.get_purchase_status("req-2")
    assert result.uncertain is True
    assert result.error_code == "FRAGMENT_PURCHASE_REFERENCE_MISSING"


async def test_failed_status_preserves_insufficient_merchant_balance_code() -> None:
    client = FragmentClient(_fragment_settings())
    status = MagicMock(
        status="failed",
        result=None,
        error="[INSUFFICIENT_BALANCE] Wallet has insufficient balance",
    )
    sdk = MagicMock()
    sdk.__enter__.return_value.get_status.return_value = status
    with patch("fragment_api.FragmentAPIClient", return_value=sdk):
        result = await client.get_purchase_status("req-balance")
    assert result.uncertain is False
    assert result.error_code == "INSUFFICIENT_BALANCE"


async def test_completed_failure_payload_is_not_treated_as_success() -> None:
    client = FragmentClient(_fragment_settings())
    status = MagicMock(
        status="completed",
        result={
            "success": False,
            "error": {"error_code": "USER_NOT_FOUND", "message": "not found"},
        },
    )
    sdk = MagicMock()
    sdk.__enter__.return_value.get_status.return_value = status
    with patch("fragment_api.FragmentAPIClient", return_value=sdk):
        result = await client.get_purchase_status("req-missing-user")
    assert result.success is False
    assert result.uncertain is False
    assert result.error_code == "USER_NOT_FOUND"
