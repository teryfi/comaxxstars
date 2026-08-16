import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.database.models import OrderNotification, Payment
from app.database.repositories.orders import OrderRepository
from app.database.session import create_local_schema, create_session_factory
from app.domain import OrderKind, OrderStatus, PaymentStatus, PricedStarsOption, StarsOption
from app.providers.stars_purchase import StarsPurchaseResult
from app.providers.test_payment import TestPaymentProvider
from app.services.orders import OrderService
from tests.factories import make_settings


class CountingProvider:
    provider_name = "counting"

    def __init__(self, result: StarsPurchaseResult | None = None) -> None:
        self.calls = 0
        self.result = result or StarsPurchaseResult(succeeded=True, transaction_id="purchase-1")

    async def purchase_for_self(self, order) -> StarsPurchaseResult:
        self.calls += 1
        await asyncio.sleep(0.01)
        return self.result

    async def purchase_as_gift(self, order) -> StarsPurchaseResult:
        return await self.purchase_for_self(order)


class RaisingProvider(CountingProvider):
    async def purchase_for_self(self, order) -> StarsPurchaseResult:
        self.calls += 1
        raise TimeoutError("provider response was lost")


class QueuedProvider(CountingProvider):
    provider_name = "queued"

    def __init__(self) -> None:
        super().__init__()
        self.status_checks = 0

    async def start_purchase(self, order) -> StarsPurchaseResult:
        self.calls += 1
        return StarsPurchaseResult(succeeded=False, pending=True, request_id="request-1")

    async def check_purchase(self, request_id: str) -> StarsPurchaseResult:
        self.status_checks += 1
        return StarsPurchaseResult(succeeded=True, transaction_id="queued-transaction-1")


def _quote(stars: int = 100) -> PricedStarsOption:
    return PricedStarsOption(
        option=StarsOption(stars=stars, currency="USDT", amount_minor=0),
        usd_rub_rate=Decimal("82.1665"),
        markup_percent=Decimal("0"),
        rub_amount=Decimal("124"),
        unit_price=Decimal("0.0150375"),
        unit_currency="USDT",
        provider_commission_percent=Decimal("0.25"),
        quote_expires_at=datetime.now(UTC) + timedelta(minutes=2),
    )


async def _build(tmp_path, provider):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'orders.db').as_posix()}"
    settings = make_settings(DATABASE_URL=database_url)
    await create_local_schema(database_url)
    session_factory = create_session_factory(database_url)
    service = OrderService(
        settings=settings,
        session_factory=session_factory,
        payment_provider=TestPaymentProvider(),
        stars_provider_factory=lambda: provider,
        customer_pricing=None,
        ton_service=None,
    )
    return service, session_factory


async def _create_paid(service: OrderService, *, token: str, buyer: int = 10) -> int:
    order_id = await service.create_order(
        buyer_telegram_id=buyer,
        buyer_username="buyer",
        kind=OrderKind.SELF,
        recipient_telegram_id=buyer,
        recipient_username="buyer",
        priced_option=_quote(),
        request_id=token,
    )
    assert await service.confirm_test_payment(order_id, buyer_telegram_id=buyer) == OrderStatus.PAID
    return order_id


async def test_double_click_and_parallel_delivery_call_provider_once(tmp_path) -> None:
    provider = CountingProvider()
    service, session_factory = await _build(tmp_path, provider)
    order_id = await _create_paid(service, token="same-token")
    duplicate_id = await service.create_order(
        buyer_telegram_id=10,
        buyer_username="buyer",
        kind=OrderKind.SELF,
        recipient_telegram_id=10,
        recipient_username="buyer",
        priced_option=_quote(),
        request_id="same-token",
    )
    assert duplicate_id == order_id

    results = await asyncio.gather(
        service.deliver_paid_order(order_id),
        service.deliver_paid_order(order_id),
    )

    assert provider.calls == 1
    assert all(status == OrderStatus.COMPLETED for status, _ in results)
    async with session_factory() as session:
        statuses = list(
            await session.scalars(
                select(OrderNotification.status)
                .where(
                    OrderNotification.order_id == order_id,
                    OrderNotification.audience == "user",
                )
                .order_by(OrderNotification.id)
            )
        )
    assert statuses == ["paid", "completed"]


async def test_unpaid_order_never_calls_purchase_provider(tmp_path) -> None:
    provider = CountingProvider()
    service, _ = await _build(tmp_path, provider)
    order_id = await service.create_order(
        buyer_telegram_id=10,
        buyer_username="buyer",
        kind=OrderKind.SELF,
        recipient_telegram_id=10,
        recipient_username="buyer",
        priced_option=_quote(),
        request_id="unpaid",
    )
    status, _ = await service.deliver_paid_order(order_id)
    assert status == OrderStatus.WAITING_FOR_PAYMENT
    assert provider.calls == 0


async def test_paid_status_with_unconfirmed_payment_record_never_calls_provider(tmp_path) -> None:
    provider = CountingProvider()
    service, session_factory = await _build(tmp_path, provider)
    order_id = await _create_paid(service, token="inconsistent-payment")
    async with session_factory() as session:
        async with session.begin():
            payment = await session.scalar(select(Payment).where(Payment.order_id == order_id))
            assert payment is not None
            payment.status = PaymentStatus.FAILED

    status, _ = await service.deliver_paid_order(order_id)

    assert status == OrderStatus.MANUAL_REVIEW
    assert provider.calls == 0
    assert (await service.get_order_summary(order_id)).error_code == "PAYMENT_NOT_CONFIRMED"
    async with session_factory() as session:
        admin_alerts = list(
            await session.scalars(
                select(OrderNotification).where(
                    OrderNotification.order_id == order_id,
                    OrderNotification.audience == "admin",
                    OrderNotification.status == "manual_review",
                )
            )
        )
    assert len(admin_alerts) == 1


async def test_restart_with_unpersisted_provider_response_goes_to_manual_review(tmp_path) -> None:
    provider = CountingProvider()
    service, session_factory = await _build(tmp_path, provider)
    order_id = await _create_paid(service, token="restart")
    async with session_factory() as session:
        async with session.begin():
            repo = OrderRepository(session)
            order = await repo.get(order_id, for_update=True)
            assert order is not None
            await repo.create_purchase_attempt(order, provider.provider_name)
            await repo.transition(order, OrderStatus.PURCHASE_PROCESSING)

    restarted = OrderService(
        settings=service.settings,
        session_factory=session_factory,
        payment_provider=TestPaymentProvider(),
        stars_provider_factory=lambda: provider,
        customer_pricing=None,
        ton_service=None,
    )
    status, _ = await restarted.deliver_paid_order(order_id)
    assert status == OrderStatus.MANUAL_REVIEW
    assert provider.calls == 0


async def test_queued_request_is_reconciled_without_second_purchase(tmp_path) -> None:
    provider = QueuedProvider()
    service, session_factory = await _build(tmp_path, provider)
    order_id = await _create_paid(service, token="queued")
    first, _ = await service.deliver_paid_order(order_id)
    assert first == OrderStatus.STARS_SENDING

    restarted = OrderService(
        settings=service.settings,
        session_factory=session_factory,
        payment_provider=TestPaymentProvider(),
        stars_provider_factory=lambda: provider,
        customer_pricing=None,
        ton_service=None,
    )
    second, _ = await restarted.deliver_paid_order(order_id)
    assert second == OrderStatus.COMPLETED
    assert provider.calls == 1
    assert provider.status_checks == 1


async def test_timeout_without_request_id_is_never_retried_automatically(tmp_path) -> None:
    provider = RaisingProvider()
    service, _ = await _build(tmp_path, provider)
    order_id = await _create_paid(service, token="timeout")
    first, _ = await service.deliver_paid_order(order_id)
    second, _ = await service.deliver_paid_order(order_id)
    assert first == second == OrderStatus.MANUAL_REVIEW
    assert provider.calls == 1


async def test_success_without_provider_reference_is_manual_review(tmp_path) -> None:
    provider = CountingProvider(StarsPurchaseResult(succeeded=True))
    service, _ = await _build(tmp_path, provider)
    order_id = await _create_paid(service, token="missing-reference")
    status, _ = await service.deliver_paid_order(order_id)
    assert status == OrderStatus.MANUAL_REVIEW


async def test_provider_transaction_cannot_complete_two_orders(tmp_path) -> None:
    provider = CountingProvider(
        StarsPurchaseResult(succeeded=True, transaction_id="same-provider-transaction")
    )
    service, _ = await _build(tmp_path, provider)
    first = await _create_paid(service, token="first", buyer=10)
    second = await _create_paid(service, token="second", buyer=11)
    assert (await service.deliver_paid_order(first))[0] == OrderStatus.COMPLETED
    assert (await service.deliver_paid_order(second))[0] == OrderStatus.MANUAL_REVIEW


async def test_insufficient_merchant_balance_preserves_paid_order_for_safe_retry(tmp_path) -> None:
    provider = CountingProvider(
        StarsPurchaseResult(
            succeeded=False,
            error_code="INSUFFICIENT_WALLET_BALANCE",
            reason="merchant wallet is empty",
        )
    )
    service, _ = await _build(tmp_path, provider)
    order_id = await _create_paid(service, token="merchant-balance")
    status, _ = await service.deliver_paid_order(order_id)
    summary = await service.get_order_summary(order_id)
    assert status == OrderStatus.WAITING_FOR_MERCHANT_BALANCE
    assert summary.status == OrderStatus.WAITING_FOR_MERCHANT_BALANCE
    assert summary.error_code == "INSUFFICIENT_WALLET_BALANCE"
    assert provider.calls == 1
    provider.result = StarsPurchaseResult(succeeded=True, transaction_id="after-top-up")
    retried = await service.safe_retry_order(order_id, actor_telegram_id=1)
    assert retried == OrderStatus.COMPLETED
    assert provider.calls == 2


async def test_admin_can_queue_a_safe_repeat_of_the_current_customer_status(tmp_path) -> None:
    provider = CountingProvider()
    service, session_factory = await _build(tmp_path, provider)
    order_id = await _create_paid(service, token="resend-status")

    status = await service.admin_resend_notification(order_id, actor_telegram_id=-1)

    assert status == OrderStatus.PAID
    async with session_factory() as session:
        statuses = list(
            await session.scalars(
                select(OrderNotification.status)
                .where(
                    OrderNotification.order_id == order_id,
                    OrderNotification.audience == "user",
                )
                .order_by(OrderNotification.id)
            )
        )
    assert statuses == ["paid", "paid"]
