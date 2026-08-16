from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from unittest.mock import AsyncMock

import pytest

from app.database.models import RuntimeSetting
from app.database.repositories.orders import OrderRepository
from app.database.session import create_local_schema, create_session_factory
from app.domain import OrderKind, OrderStatus, PricedStarsOption, StarsOption
from app.providers.stars_purchase import TestStarsPurchaseProvider
from app.providers.test_payment import DisabledProductionPaymentProvider
from app.services.customer_pricing import CustomerPricingService
from app.services.orders import OrderService
from app.services.payment_monitor import PaymentMonitor
from app.services.ton_payment import TonPaymentObservation
from tests.factories import make_settings


class FakeTonService:
    def __init__(self, wallet: str = "wallet-address") -> None:
        self.wallet_address = wallet

    async def get_ton_usd_rate(self) -> Decimal:
        return Decimal("3")

    async def usd_to_ton(self, amount: Decimal) -> Decimal:
        return (amount / Decimal("3")).quantize(
            Decimal("0.000000001"),
            rounding=ROUND_CEILING,
        )

    def generate_payment_comment(self, order_id: int) -> str:
        return f"TERSTARS-{order_id}-TESTCOMMENT"


class FailingTonService(FakeTonService):
    async def find_incoming_payment(self, **kwargs):
        raise TimeoutError("Toncenter unavailable")


def _quote(stars: int = 100) -> PricedStarsOption:
    return PricedStarsOption(
        option=StarsOption(stars=stars, currency="TON", amount_minor=0),
        usd_rub_rate=Decimal("90"),
        markup_percent=Decimal("0"),
        rub_amount=Decimal("270"),
        unit_price=Decimal("0.01"),
        unit_currency="TON",
        provider_commission_percent=Decimal("0.25"),
        quote_expires_at=datetime.now(UTC) + timedelta(minutes=2),
    )


async def _build(tmp_path, *, ton_service=None):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'payments.db').as_posix()}"
    settings = make_settings(
        DATABASE_URL=database_url,
        TEST_PAYMENT_MODE=False,
        REAL_STARS_PURCHASE_ENABLED=True,
        STARS_PURCHASE_PROVIDER="fragment",
        PROCESS_ROLE="bot",
        TON_WALLET_ADDRESS="wallet-address",
        PAYMENT_CONFIRMATION_SECONDS=0,
    )
    await create_local_schema(database_url)
    session_factory = create_session_factory(database_url)
    ton_service = ton_service or FakeTonService()
    pricing = CustomerPricingService(
        settings,
        AsyncMock(),
        ton_service,
        AsyncMock(),
    )
    service = OrderService(
        settings=settings,
        session_factory=session_factory,
        payment_provider=DisabledProductionPaymentProvider(),
        stars_provider_factory=lambda: TestStarsPurchaseProvider(),
        customer_pricing=pricing,
        ton_service=ton_service,
    )
    return settings, service, session_factory, ton_service


async def _create_waiting(service: OrderService, *, token: str, buyer: int = 10) -> tuple[int, str]:
    order_id = await service.create_order(
        buyer_telegram_id=buyer,
        buyer_username=f"buyer{buyer}",
        kind=OrderKind.SELF,
        recipient_telegram_id=buyer,
        recipient_username=f"buyer{buyer}",
        priced_option=_quote(),
        request_id=token,
    )
    instructions = await service.initiate_ton_payment(order_id)
    return order_id, instructions.ton_amount


def _observation(
    *,
    tx: str = "tx-1",
    amount: Decimal = Decimal("1"),
    destination: str = "wallet-address",
    currency: str = "TON",
    network: str = "TON",
) -> TonPaymentObservation:
    return TonPaymentObservation(
        transaction_hash=tx,
        amount=amount,
        timestamp=datetime.now(UTC) - timedelta(minutes=1),
        destination=destination,
        currency=currency,
        network=network,
    )


async def test_valid_payment_moves_through_all_confirmation_states(tmp_path) -> None:
    _, service, _, _ = await _build(tmp_path)
    order_id, expected = await _create_waiting(service, token="valid")
    observation = _observation(amount=Decimal(expected))
    assert (
        await service.record_ton_observation(order_id, observation) == OrderStatus.PAYMENT_DETECTED
    )
    assert (
        await service.record_ton_observation(order_id, observation)
        == OrderStatus.PAYMENT_CONFIRMING
    )
    assert await service.record_ton_observation(order_id, observation) == OrderStatus.PAID
    assert await service.record_ton_observation(order_id, observation) == OrderStatus.PAID


@pytest.mark.parametrize(
    ("amount", "error_code"),
    [
        (Decimal("0.999999999"), "PAYMENT_UNDERPAID"),
        (Decimal("1.000000001"), "PAYMENT_OVERPAID"),
    ],
)
async def test_amount_mismatch_requires_manual_review(tmp_path, amount, error_code) -> None:
    _, service, _, _ = await _build(tmp_path)
    order_id, _ = await _create_waiting(service, token=error_code)
    assert (
        await service.record_ton_observation(
            order_id,
            _observation(amount=amount),
        )
        == OrderStatus.MANUAL_REVIEW
    )
    assert (await service.get_order_summary(order_id)).error_code == error_code


@pytest.mark.parametrize(
    "overrides",
    [
        {"destination": "wrong-wallet"},
        {"currency": "USDT"},
        {"network": "ETH"},
    ],
)
async def test_wrong_payment_route_is_rejected(tmp_path, overrides) -> None:
    _, service, _, _ = await _build(tmp_path)
    order_id, _ = await _create_waiting(service, token=str(overrides))
    status = await service.record_ton_observation(order_id, _observation(**overrides))
    assert status == OrderStatus.MANUAL_REVIEW
    assert (await service.get_order_summary(order_id)).error_code == "PAYMENT_ROUTE_MISMATCH"


async def test_duplicate_transaction_cannot_pay_two_orders(tmp_path) -> None:
    _, service, _, _ = await _build(tmp_path)
    first, _ = await _create_waiting(service, token="first", buyer=10)
    second, _ = await _create_waiting(service, token="second", buyer=11)
    observation = _observation(tx="same-tx")
    assert await service.record_ton_observation(first, observation) == OrderStatus.PAYMENT_DETECTED
    assert await service.record_ton_observation(second, observation) == OrderStatus.MANUAL_REVIEW
    assert (await service.get_order_summary(second)).error_code == "DUPLICATE_PAYMENT_TRANSACTION"


async def test_payment_after_expiration_is_persisted_for_review(tmp_path) -> None:
    _, service, session_factory, _ = await _build(tmp_path)
    order_id, _ = await _create_waiting(service, token="late")
    async with session_factory() as session:
        async with session.begin():
            repo = OrderRepository(session)
            order = await repo.get(order_id, for_update=True)
            assert order is not None
            order.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await service.expire_if_needed(order_id) == OrderStatus.PAYMENT_EXPIRED
    assert (
        await service.record_ton_observation(
            order_id,
            _observation(tx="late-tx"),
        )
        == OrderStatus.MANUAL_REVIEW
    )
    summary = await service.get_order_summary(order_id)
    assert summary.payment_tx == "late-tx"
    assert summary.error_code == "LATE_PAYMENT"


async def test_provider_timeout_does_not_mark_payment_successful(tmp_path) -> None:
    failing = FailingTonService()
    settings, service, session_factory, _ = await _build(tmp_path, ton_service=failing)
    order_id, _ = await _create_waiting(service, token="provider-timeout")
    monitor = PaymentMonitor(settings, session_factory, service, failing)  # type: ignore[arg-type]
    with pytest.raises(TimeoutError):
        await monitor.check_order_now(order_id)
    assert (await service.get_order_summary(order_id)).status == OrderStatus.WAITING_FOR_PAYMENT


async def test_payment_monitor_persists_heartbeat_even_without_open_orders(tmp_path) -> None:
    settings, service, session_factory, ton_service = await _build(tmp_path)
    monitor = PaymentMonitor(settings, session_factory, service, ton_service)
    await monitor.tick()
    async with session_factory() as session:
        heartbeat = await session.get(RuntimeSetting, "payment_monitor_heartbeat")
    assert heartbeat is not None
    assert datetime.fromisoformat(heartbeat.value).tzinfo is not None


async def test_detected_payment_that_disappears_after_expiry_requires_review(tmp_path) -> None:
    _, service, session_factory, _ = await _build(tmp_path)
    order_id, expected = await _create_waiting(service, token="confirmation-lost")
    async with session_factory() as session:
        async with session.begin():
            repo = OrderRepository(session)
            order = await repo.get(order_id, for_update=True)
            assert order is not None
            order.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    observation = _observation(amount=Decimal(expected))
    assert (
        await service.record_ton_observation(order_id, observation) == OrderStatus.PAYMENT_DETECTED
    )
    assert await service.expire_if_needed(order_id) == OrderStatus.MANUAL_REVIEW
    assert (await service.get_order_summary(order_id)).error_code == "PAYMENT_CONFIRMATION_LOST"
