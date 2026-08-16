import base64
import binascii
import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)
TELEGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
REQUIRED_FRAGMENT_COOKIE_KEYS = frozenset(
    {"stel_token", "stel_ssid", "stel_ton_token", "stel_dt"}
)

DEFINITIVE_FRAGMENT_ERRORS = frozenset(
    {
        "VALIDATION_ERROR",
        "INVALID_USERNAME_FORMAT",
        "INVALID_SEED",
        "INVALID_WALLET_SEED",
        "INVALID_FRAGMENT_COOKIES",
        "INVALID_FRAGMENT_LOCAL_STORAGE",
        "INSUFFICIENT_BALANCE",
        "INSUFFICIENT_WALLET_BALANCE",
        "USER_NOT_FOUND",
        "TELEGRAM_USER_NOT_FOUND",
        "FRAGMENT_ADDITIONAL_VERIFICATION_REQUIRED",
    }
)


def _fragment_error(error: Any, payload: Any = None) -> tuple[str, str]:
    """Extract a stable provider code without exposing the raw response."""
    candidates: list[Any] = [payload, error]
    for candidate in candidates:
        if isinstance(candidate, dict):
            nested = candidate.get("error")
            code = _pick(candidate, "error_code", "code")
            if code is None and isinstance(nested, dict):
                code = _pick(nested, "error_code", "code")
            if code:
                normalized = str(code).strip().upper()
                if normalized:
                    return normalized[:64], "Fragment purchase failed"

    if isinstance(error, str):
        raw = error.strip()
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            decoded = None
        if isinstance(decoded, dict):
            return _fragment_error(decoded)
        upper = raw.upper()
        for code in DEFINITIVE_FRAGMENT_ERRORS:
            if code in upper:
                return code, "Fragment purchase failed"
        if "INSUFFICIENT" in upper and "BALANCE" in upper:
            return "INSUFFICIENT_BALANCE", "Fragment purchase failed"

    return "FRAGMENT_PURCHASE_FAILED", "Fragment purchase failed"


@dataclass(frozen=True)
class FragmentHealthResult:
    ok: bool
    message: str
    rates: dict[str, float] | None = None


@dataclass(frozen=True)
class FragmentPurchaseResponse:
    success: bool
    pending: bool = False
    uncertain: bool = False
    transaction_id: str | None = None
    request_id: str | None = None
    error_code: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class FragmentStarsUnitPrice:
    amount: Decimal
    currency: str
    commission_percent: Decimal
    cached_at: str | None = None

    def total_for(self, stars: int) -> Decimal:
        if stars <= 0:
            raise ValueError("stars must be positive")
        return self.amount * Decimal(stars)


def normalize_wallet_seed(seed: str) -> str:
    value = seed.strip()
    if not value:
        raise ValueError("FRAGMENT_WALLET_SEED is empty")
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
        if len(decoded.split()) == 24:
            return value
    except (binascii.Error, UnicodeDecodeError):
        pass
    if len(value.split()) != 24:
        raise ValueError("FRAGMENT_WALLET_SEED must be a 24-word phrase or its base64 encoding")
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def normalize_fragment_cookies(cookies: str) -> str:
    """Validate the opaque KYC cookie bundle without exposing its values."""
    value = cookies.strip()
    if not value:
        raise ValueError("FRAGMENT_COOKIES is empty")
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
        payload = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("FRAGMENT_COOKIES must be base64-encoded JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("FRAGMENT_COOKIES JSON must be an object")
    missing = sorted(
        key
        for key in REQUIRED_FRAGMENT_COOKIE_KEYS
        if not isinstance(payload.get(key), str) or not payload[key].strip()
    )
    if missing:
        raise ValueError(
            "FRAGMENT_COOKIES is missing required values: " + ", ".join(missing)
        )
    return value


def normalize_username(username: str) -> str:
    clean = username.strip().lstrip("@")
    if not TELEGRAM_USERNAME_RE.fullmatch(clean):
        raise ValueError("username must contain 5-32 ASCII letters, digits, or underscores")
    return f"@{clean}"


def _pick(data: Any, *keys: str, default: Any = None) -> Any:
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
    else:
        for key in keys:
            value = getattr(data, key, None)
            if value not in (None, ""):
                return value
    return default


class FragmentClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        seed = settings.secret_value(settings.fragment_wallet_seed)
        self._seed = normalize_wallet_seed(seed) if seed else None
        raw_cookies = settings.secret_value(settings.fragment_cookies)
        self._cookies = (
            normalize_fragment_cookies(raw_cookies)
            if settings.fragment_api_mode == "kyc" and raw_cookies
            else None
        )
        self._local_storage = settings.secret_value(settings.fragment_local_storage)

    @property
    def uses_kyc(self) -> bool:
        return self.settings.fragment_api_mode == "kyc"

    async def check_health(self) -> FragmentHealthResult:
        import asyncio

        def _sync() -> dict[str, float]:
            from fragment_api import FragmentAPIClient

            with FragmentAPIClient(
                self.settings.fragment_api_base_url,
                poll_timeout=float(self.settings.fragment_poll_timeout_seconds),
            ) as client:
                rates = client.get_rates()
                return {
                    "no_kyc_percent": float(_pick(rates, "rate_no_kyc", default=0.25)),
                    "kyc_percent": float(_pick(rates, "rate_with_kyc", default=0.0)),
                }

        try:
            return FragmentHealthResult(
                ok=True,
                message="Fragment API is reachable",
                rates=await asyncio.to_thread(_sync),
            )
        except Exception as exc:
            logger.warning(
                "Fragment health check failed",
                extra={"event": "fragment_health_failed", "error_type": exc.__class__.__name__},
            )
            return FragmentHealthResult(ok=False, message="Fragment API health check failed")

    async def get_prices(self) -> dict[str, Any]:
        import asyncio

        def _sync() -> dict[str, Any]:
            from fragment_api import FragmentAPIClient

            with FragmentAPIClient(self.settings.fragment_api_base_url) as client:
                return client.get_prices()

        return await asyncio.to_thread(_sync)

    async def get_rates(self) -> dict[str, float]:
        health = await self.check_health()
        if not health.ok or not health.rates:
            raise RuntimeError(health.message)
        return health.rates

    async def get_stars_unit_price(self) -> FragmentStarsUnitPrice:
        prices = await self.get_prices()
        stars_prices = _pick(prices, "stars")
        if stars_prices is None:
            raise RuntimeError("Fragment prices response does not contain Stars prices")

        commission_key = "commission_rate_with_kyc" if self.uses_kyc else "commission_rate_no_kyc"
        commission = Decimal(str(_pick(prices, commission_key, default="0")))
        if not commission.is_finite() or commission < 0 or commission > 100:
            raise RuntimeError("Fragment returned an invalid commission rate")
        cached_at = _pick(prices, "cached_at")
        if self.settings.fragment_payment_method == "ton":
            price_key = (
                "price_with_commission_kyc" if self.uses_kyc else "price_with_commission_no_kyc"
            )
            value = _pick(stars_prices, price_key)
            if value is None:
                base_value = _pick(stars_prices, "price_per_star_ton")
                if base_value is None:
                    raise RuntimeError(
                        "Fragment prices response does not contain a TON Stars price"
                    )
                value = Decimal(str(base_value)) * (Decimal("1") + commission / Decimal("100"))
            price = FragmentStarsUnitPrice(
                amount=Decimal(str(value)),
                currency="TON",
                commission_percent=commission,
                cached_at=str(cached_at) if cached_at else None,
            )
        else:
            value = _pick(stars_prices, "price_per_star_usdt_ton")
            if value is None:
                raise RuntimeError(
                    "Fragment prices response does not contain a USDT-on-TON Stars price"
                )
            price = FragmentStarsUnitPrice(
                amount=Decimal(str(value)) * (Decimal("1") + commission / Decimal("100")),
                currency="USDT",
                commission_percent=commission,
                cached_at=str(cached_at) if cached_at else None,
            )
        if not price.amount.is_finite() or price.amount <= 0:
            raise RuntimeError("Fragment returned an invalid Stars unit price")
        logger.info(
            "Fragment Stars price loaded",
            extra={
                "event": "fragment_price_loaded",
                "currency": price.currency,
                "commission_percent": str(price.commission_percent),
            },
        )
        return price

    async def start_stars_purchase(self, *, username: str, amount: int) -> FragmentPurchaseResponse:
        import asyncio

        if not self._seed:
            return FragmentPurchaseResponse(
                success=False,
                error_code="WALLET_SEED_MISSING",
                error="Real Fragment purchase is not configured",
            )
        normalized = normalize_username(username)

        def _sync() -> Any:
            from fragment_api import FragmentAPIClient

            with FragmentAPIClient(
                self.settings.fragment_api_base_url,
                poll_timeout=float(self.settings.fragment_poll_timeout_seconds),
            ) as client:
                return client.buy_stars(
                    username=normalized,
                    amount=amount,
                    seed=self._seed,
                    cookies=self._cookies,
                    local_storage=self._local_storage,
                    payment_method=self.settings.fragment_payment_method,
                    wait=False,
                )

        try:
            result = await asyncio.to_thread(_sync)
        except Exception as exc:
            return self._exception_response(exc)
        request_id = _pick(result, "request_id")
        if not request_id:
            return FragmentPurchaseResponse(
                success=False,
                uncertain=True,
                error_code="FRAGMENT_REQUEST_ID_MISSING",
                error="Fragment accepted an unexpected response",
            )
        return FragmentPurchaseResponse(
            success=False,
            pending=True,
            request_id=str(request_id),
        )

    async def get_purchase_status(self, request_id: str) -> FragmentPurchaseResponse:
        import asyncio

        def _sync() -> Any:
            from fragment_api import FragmentAPIClient

            with FragmentAPIClient(self.settings.fragment_api_base_url) as client:
                return client.get_status(request_id)

        try:
            result = await asyncio.to_thread(_sync)
        except Exception as exc:
            response = self._exception_response(exc)
            definitive = bool(response.error_code in DEFINITIVE_FRAGMENT_ERRORS)
            return FragmentPurchaseResponse(
                success=False,
                pending=not definitive,
                uncertain=False,
                request_id=request_id,
                error_code=response.error_code,
                error=response.error,
            )

        status = str(_pick(result, "status", default="")).split(".")[-1].lower()
        if status in {"queued", "processing", "pending"}:
            return FragmentPurchaseResponse(success=False, pending=True, request_id=request_id)
        if status == "completed":
            payload = _pick(result, "result", default={}) or {}
            nested = _pick(payload, "data", default=payload) or payload
            if _pick(nested, "success", default=_pick(payload, "success", default=True)) is False:
                error = _pick(nested, "error", default=_pick(payload, "error"))
                error_code, message = _fragment_error(error, nested)
                return FragmentPurchaseResponse(
                    success=False,
                    request_id=request_id,
                    error_code=error_code,
                    error=message,
                )
            tx = _pick(nested, "transaction_hash", "transaction_id", "invoice_id")
            if not tx:
                return FragmentPurchaseResponse(
                    success=False,
                    uncertain=True,
                    request_id=request_id,
                    error_code="FRAGMENT_PURCHASE_REFERENCE_MISSING",
                    error="Fragment completion reference is missing",
                )
            return FragmentPurchaseResponse(
                success=True,
                transaction_id=str(tx) if tx else None,
                request_id=request_id,
            )
        if status == "failed":
            error = _pick(result, "error", default=_pick(result, "result"))
            error_code, message = _fragment_error(error, _pick(result, "result"))
            return FragmentPurchaseResponse(
                success=False,
                request_id=request_id,
                error_code=error_code,
                error=message,
            )
        return FragmentPurchaseResponse(
            success=False,
            uncertain=True,
            request_id=request_id,
            error_code="FRAGMENT_STATUS_UNCERTAIN",
            error="Fragment purchase status is uncertain",
        )

    @staticmethod
    def _exception_response(exc: Exception) -> FragmentPurchaseResponse:
        error_code = str(getattr(exc, "error_code", exc.__class__.__name__))[:64]
        definitive = error_code in DEFINITIVE_FRAGMENT_ERRORS
        logger.warning(
            "Fragment request failed",
            extra={"event": "fragment_request_failed", "error_code": error_code},
        )
        return FragmentPurchaseResponse(
            success=False,
            uncertain=not definitive,
            error_code=error_code,
            error="Fragment request failed",
        )
