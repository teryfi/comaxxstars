from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

from app.database.models import Order, Payment
from app.database.repositories.orders import OrderRepository
from app.database.session import create_local_schema, create_session_factory
from app.domain import (
    OrderKind,
    OrderStatus,
    PaymentStatus,
    PricedStarsOption,
    PurchaseAttemptStatus,
    StarsOption,
)
from app.payment_gateway import PaymentGateway
from app.payments.yookassa import YooKassaClient, YooKassaPayment
from app.providers.stars_purchase import TestStarsPurchaseProvider
from app.providers.test_payment import DisabledProductionPaymentProvider
from app.services.abuse import AbuseGuard
from app.services.orders import OrderService
from tests.factories import make_settings


class FakeYooKassaClient:
    def __init__(self) -> None:
        self.created = 0
        self.status = "pending"
        self.amount = Decimal("124.00")

    async def create_payment(self, **kwargs) -> YooKassaPayment:
        self.created += 1
        return self._snapshot(kwargs["order_id"], kwargs["order_number"])

    async def get_payment(self, payment_id: str) -> YooKassaPayment:
        assert payment_id == "yk-payment-1"
        return self._snapshot(1, self.order_number)

    def _snapshot(self, order_id: int, order_number: str) -> YooKassaPayment:
        self.order_number = order_number
        return YooKassaPayment(
            payment_id="yk-payment-1",
            status=self.status,
            amount=self.amount,
            currency="RUB",
            order_id=order_id,
            order_number=order_number,
            confirmation_url="https://yoomoney.ru/checkout/payments/v2/contract",
            paid=self.status == "succeeded",
            refundable=self.status == "succeeded",
        )


def _quote() -> PricedStarsOption:
    return PricedStarsOption(
        option=StarsOption(stars=100, currency="USDT", amount_minor=0),
        usd_rub_rate=Decimal("82"),
        markup_percent=Decimal("20"),
        rub_amount=Decimal("124.00"),
        unit_price=Decimal("0.0124"),
        unit_currency="USDT",
        provider_commission_percent=Decimal("0.25"),
        quote_expires_at=datetime.now(UTC) + timedelta(minutes=2),
    )


async def _build(tmp_path):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'yookassa.db').as_posix()}"
    settings = make_settings(
        DATABASE_URL=database_url,
        TEST_PAYMENT_MODE=False,
        CUSTOMER_PAYMENT_PROVIDER="yookassa",
    )
    await create_local_schema(database_url)
    factory = create_session_factory(database_url)
    client = FakeYooKassaClient()
    service = OrderService(
        settings=settings,
        session_factory=factory,
        payment_provider=DisabledProductionPaymentProvider(),
        stars_provider_factory=TestStarsPurchaseProvider,
        customer_pricing=None,
        ton_service=None,
        yookassa_client=client,  # type: ignore[arg-type]
    )
    return service, factory, client


async def _create(service: OrderService) -> int:
    return await service.create_order(
        buyer_telegram_id=10,
        buyer_username="buyer",
        kind=OrderKind.SELF,
        recipient_telegram_id=10,
        recipient_username="buyer",
        priced_option=_quote(),
        request_id="yookassa-order",
    )


async def test_payment_requires_verified_succeeded_snapshot_before_paid(tmp_path) -> None:
    service, factory, client = await _build(tmp_path)
    order_id = await _create(service)

    instructions = await service.initiate_yookassa_payment(order_id)
    assert instructions.confirmation_url.startswith("https://")
    async with factory() as session:
        order = await session.get(Order, order_id)
        payment = await session.scalar(select(Payment).where(Payment.order_id == order_id))
        assert order is not None and order.status == OrderStatus.WAITING_FOR_PAYMENT
        assert payment is not None and payment.status == PaymentStatus.PENDING
        assert payment.idempotency_key

    client.status = "succeeded"
    assert await service.sync_yookassa_payment(order_id) == OrderStatus.PAID
    assert await service.sync_yookassa_payment(order_id) == OrderStatus.PAID
    assert client.created == 1


async def test_amount_mismatch_never_marks_order_paid(tmp_path) -> None:
    service, factory, client = await _build(tmp_path)
    order_id = await _create(service)
    await service.initiate_yookassa_payment(order_id)
    client.status = "succeeded"
    client.amount = Decimal("123.00")

    with pytest.raises(ValueError, match="amount or currency"):
        await service.sync_yookassa_payment(order_id)
    async with factory() as session:
        order = await session.get(Order, order_id)
        assert order is not None and order.status == OrderStatus.WAITING_FOR_PAYMENT


async def test_refund_is_blocked_while_fragment_outcome_is_uncertain(tmp_path) -> None:
    service, factory, client = await _build(tmp_path)
    order_id = await _create(service)
    await service.initiate_yookassa_payment(order_id)
    client.status = "succeeded"
    assert await service.sync_yookassa_payment(order_id) == OrderStatus.PAID
    async with factory() as session:
        async with session.begin():
            repo = OrderRepository(session)
            order = await repo.get(order_id, for_update=True)
            assert order is not None
            await repo.transition(order, OrderStatus.REFUND_REQUIRED)
            attempt, _ = await repo.create_purchase_attempt(order, "fragment")
            attempt.status = PurchaseAttemptStatus.UNCERTAIN

    with pytest.raises(ValueError, match="Fragment purchase outcome"):
        await service.request_yookassa_refund(order_id, actor_telegram_id=-1)


def test_yookassa_parser_rejects_non_https_confirmation_url() -> None:
    with pytest.raises(RuntimeError, match="unsafe confirmation URL"):
        YooKassaClient._parse_payment(
            {
                "id": "payment-1",
                "status": "pending",
                "amount": {"value": "80.00", "currency": "RUB"},
                "metadata": {"internal_order_id": "1", "order_number": "TS-1"},
                "confirmation": {"confirmation_url": "http://example.com/pay"},
            }
        )


async def test_webhook_rejects_malformed_and_verifies_valid_event() -> None:
    class Service:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def sync_yookassa_payment_by_provider_id(
            self, payment_id: str, *, webhook_event: str
        ) -> OrderStatus:
            self.calls.append((payment_id, webhook_event))
            return OrderStatus.PAID

    service = Service()
    container = type(
        "GatewayContainer",
        (),
        {
            "settings": type(
                "GatewaySettings",
                (),
                {"webhook_max_body_bytes": 4096, "webhook_rate_limit_per_minute": 120},
            )(),
            "order_service": service,
            "abuse_guard": AbuseGuard(default_limit=120, window_seconds=60),
        },
    )()
    client = TestClient(TestServer(PaymentGateway(container).application()))
    await client.start_server()
    try:
        malformed = await client.post(
            "/webhooks/yookassa", data="not-json", headers={"Content-Type": "application/json"}
        )
        assert malformed.status == 400
        valid = await client.post(
            "/webhooks/yookassa",
            json={"event": "payment.succeeded", "object": {"id": "payment-1"}},
        )
        assert valid.status == 200
        assert service.calls == [("payment-1", "payment.succeeded")]
    finally:
        await client.close()
