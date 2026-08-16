import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp

from app.config import Settings

logger = logging.getLogger(__name__)

PAYMENT_STATUSES = frozenset({"pending", "waiting_for_capture", "succeeded", "canceled"})


class YooKassaError(RuntimeError):
    """A safe provider error which never includes credentials or raw response bodies."""


@dataclass(frozen=True)
class YooKassaPayment:
    payment_id: str
    status: str
    amount: Decimal
    currency: str
    order_id: int
    order_number: str
    confirmation_url: str | None
    paid: bool
    refundable: bool


@dataclass(frozen=True)
class YooKassaRefund:
    refund_id: str
    payment_id: str
    status: str
    amount: Decimal
    currency: str


class YooKassaClient:
    """Small client for the documented YooKassa Payments HTTP API."""

    def __init__(self, settings: Settings) -> None:
        shop_id = settings.yookassa_shop_id
        secret_key = settings.secret_value(settings.yookassa_secret_key)
        if not shop_id or not secret_key:
            raise RuntimeError("YooKassa credentials are not configured")
        if not settings.yookassa_return_url:
            raise RuntimeError("YOOKASSA_RETURN_URL is not configured")
        if settings.yookassa_receipt_mode == "owner_decision_required":
            raise RuntimeError("YooKassa receipt configuration REQUIRES OWNER DECISION")
        self._base_url = settings.yookassa_api_base_url
        self._auth = aiohttp.BasicAuth(shop_id, secret_key)
        self._return_url = settings.yookassa_return_url
        self._timeout = aiohttp.ClientTimeout(total=settings.yookassa_timeout_seconds)

    async def create_payment(
        self,
        *,
        order_id: int,
        order_number: str,
        amount_rub: Decimal,
        idempotency_key: str,
    ) -> YooKassaPayment:
        if not idempotency_key or len(idempotency_key) > 64:
            raise ValueError("Invalid YooKassa idempotency key")
        payload = {
            "amount": {"value": self._amount_value(amount_rub), "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": self._return_url},
            "description": f"Telegram Stars order {order_number}"[:128],
            "metadata": {
                "internal_order_id": str(order_id),
                "order_number": order_number,
            },
        }
        data = await self._request(
            "POST",
            "/payments",
            json=payload,
            headers={"Idempotence-Key": idempotency_key},
        )
        return self._parse_payment(data)

    async def get_payment(self, payment_id: str) -> YooKassaPayment:
        if not payment_id or len(payment_id) > 128:
            raise ValueError("Invalid YooKassa payment ID")
        data = await self._request("GET", f"/payments/{payment_id}")
        return self._parse_payment(data)

    async def create_refund(
        self,
        *,
        payment_id: str,
        amount_rub: Decimal,
        idempotency_key: str,
    ) -> YooKassaRefund:
        """Create a refund only after an explicit owner-confirmed admin action."""
        payload = {
            "payment_id": payment_id,
            "amount": {"value": self._amount_value(amount_rub), "currency": "RUB"},
        }
        data = await self._request(
            "POST",
            "/refunds",
            json=payload,
            headers={"Idempotence-Key": idempotency_key},
        )
        return self._parse_refund(data)

    async def get_refund(self, refund_id: str) -> YooKassaRefund:
        if not refund_id or len(refund_id) > 128:
            raise ValueError("Invalid YooKassa refund ID")
        return self._parse_refund(await self._request("GET", f"/refunds/{refund_id}"))

    async def check_health(self) -> bool:
        """Credentials are verified by fetching a deliberately unknown payment."""
        try:
            await self.get_payment("00000000-0000-0000-0000-000000000000")
        except YooKassaError as exc:
            return str(exc) == "YooKassa HTTP 404"
        return True

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            async with aiohttp.ClientSession(
                auth=self._auth,
                timeout=self._timeout,
                raise_for_status=False,
            ) as session:
                async with session.request(
                    method,
                    f"{self._base_url}{path}",
                    json=json,
                    headers=headers,
                ) as response:
                    if response.status == 401:
                        raise YooKassaError("YooKassa unauthorized")
                    if response.status < 200 or response.status >= 300:
                        logger.warning(
                            "YooKassa request failed",
                            extra={
                                "event": "yookassa_request_failed",
                                "method": method,
                                "status": response.status,
                            },
                        )
                        raise YooKassaError(f"YooKassa HTTP {response.status}")
                    data = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise YooKassaError("YooKassa request is temporarily unavailable") from exc
        except ValueError as exc:
            raise YooKassaError("YooKassa returned malformed JSON") from exc
        if not isinstance(data, dict):
            raise YooKassaError("YooKassa returned an invalid response")
        return data

    @classmethod
    def _parse_payment(cls, data: dict[str, Any]) -> YooKassaPayment:
        try:
            payment_id = str(data["id"])
            status = str(data["status"])
            amount_data = data["amount"]
            metadata = data["metadata"]
            amount = Decimal(str(amount_data["value"]))
            currency = str(amount_data["currency"]).upper()
            order_id = int(metadata["internal_order_id"])
            order_number = str(metadata["order_number"])
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise YooKassaError("YooKassa payment response is incomplete") from exc
        if status not in PAYMENT_STATUSES:
            raise YooKassaError("YooKassa returned an unknown payment status")
        if not payment_id or len(payment_id) > 128 or amount <= 0 or not amount.is_finite():
            raise YooKassaError("YooKassa payment response contains invalid values")
        confirmation = data.get("confirmation")
        confirmation_url = (
            str(confirmation.get("confirmation_url"))
            if isinstance(confirmation, dict) and confirmation.get("confirmation_url")
            else None
        )
        if confirmation_url and not confirmation_url.startswith("https://"):
            raise YooKassaError("YooKassa returned an unsafe confirmation URL")
        return YooKassaPayment(
            payment_id=payment_id,
            status=status,
            amount=amount,
            currency=currency,
            order_id=order_id,
            order_number=order_number,
            confirmation_url=confirmation_url,
            paid=bool(data.get("paid", False)),
            refundable=bool(data.get("refundable", False)),
        )

    @staticmethod
    def _amount_value(amount: Decimal) -> str:
        normalized = amount.quantize(Decimal("0.01"))
        if normalized <= 0 or not normalized.is_finite():
            raise ValueError("Payment amount must be positive")
        return f"{normalized:.2f}"

    @staticmethod
    def _parse_refund(data: dict[str, Any]) -> YooKassaRefund:
        try:
            refund_id = str(data["id"])
            payment_id = str(data["payment_id"])
            status = str(data["status"])
            amount_data = data["amount"]
            amount = Decimal(str(amount_data["value"]))
            currency = str(amount_data["currency"]).upper()
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise YooKassaError("YooKassa refund response is incomplete") from exc
        if status not in {"pending", "succeeded", "canceled"}:
            raise YooKassaError("YooKassa returned an unknown refund status")
        if not refund_id or not payment_id or amount <= 0 or not amount.is_finite():
            raise YooKassaError("YooKassa refund response contains invalid values")
        return YooKassaRefund(
            refund_id=refund_id,
            payment_id=payment_id,
            status=status,
            amount=amount,
            currency=currency,
        )
