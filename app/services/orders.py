import logging
import weakref
from asyncio import Lock
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.database.models import Order, Payment, PurchaseAttempt
from app.database.repositories.orders import OrderRepository
from app.domain import (
    CustomerPaymentType,
    OrderKind,
    OrderStatus,
    PaymentStatus,
    PricedStarsOption,
    PurchaseAttemptStatus,
    validate_stars_amount,
)
from app.payments.yookassa import YooKassaClient, YooKassaPayment
from app.providers.payment import PaymentProvider
from app.providers.stars_purchase import StarsPurchaseProvider, StarsPurchaseResult
from app.services.customer_pricing import CustomerPricingService
from app.services.ton_payment import TonPaymentObservation, TonPaymentService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TonPaymentInstructions:
    order_id: int
    order_number: str
    ton_amount: str
    wallet_address: str
    payment_comment: str
    rub_amount: str
    expires_at: datetime


@dataclass(frozen=True)
class YooKassaPaymentInstructions:
    order_id: int
    order_number: str
    confirmation_url: str
    rub_amount: str


@dataclass(frozen=True)
class OrderSummary:
    order_id: int
    order_number: str
    buyer_telegram_id: int
    recipient_username: str | None
    stars: int
    status: OrderStatus
    error_code: str | None
    payment_tx: str | None
    purchase_request_id: str | None
    purchase_tx: str | None


class OrderService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        payment_provider: PaymentProvider,
        stars_provider_factory: Callable[[], StarsPurchaseProvider],
        customer_pricing: CustomerPricingService | None,
        ton_service: TonPaymentService | None,
        yookassa_client: YooKassaClient | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.payment_provider = payment_provider
        self.stars_provider_factory = stars_provider_factory
        self.customer_pricing = customer_pricing
        self.ton_service = ton_service
        self.yookassa_client = yookassa_client
        self._delivery_locks: weakref.WeakValueDictionary[int, Lock] = weakref.WeakValueDictionary()

    async def create_order(
        self,
        *,
        buyer_telegram_id: int,
        buyer_username: str | None,
        kind: OrderKind,
        recipient_telegram_id: int | None,
        recipient_username: str | None,
        priced_option: PricedStarsOption,
        request_id: str | None = None,
    ) -> int:
        if isinstance(buyer_telegram_id, bool) or buyer_telegram_id <= 0:
            raise ValueError("Buyer Telegram ID must be a positive integer")
        if recipient_telegram_id is None or recipient_telegram_id <= 0:
            raise ValueError("Recipient Telegram ID must be a positive integer")
        if kind == OrderKind.SELF and recipient_telegram_id != buyer_telegram_id:
            raise ValueError("Self order recipient must match the buyer")
        validate_stars_amount(
            priced_option.option.stars,
            minimum=self.settings.min_stars,
            maximum=self.settings.max_stars,
        )
        if priced_option.quote_expires_at and priced_option.quote_expires_at <= datetime.now(UTC):
            raise ValueError("Price quote has expired")
        if (
            not priced_option.rub_amount.is_finite()
            or priced_option.rub_amount <= 0
            or priced_option.rub_amount > self.settings.max_order_rub_amount
        ):
            raise ValueError("Order total is outside configured transaction limits")
        key = self._build_idempotency_key(
            buyer_telegram_id=buyer_telegram_id,
            kind=kind,
            recipient_telegram_id=recipient_telegram_id,
            stars=priced_option.option.stars,
            request_id=request_id,
        )
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                existing = await repo.get_by_idempotency_key(key)
                if existing is not None:
                    return existing.id
                if await repo.get_runtime_bool("maintenance_mode", self.settings.maintenance_mode):
                    raise RuntimeError("New orders are temporarily disabled")
                if await repo.is_user_blocked(buyer_telegram_id):
                    raise PermissionError("Telegram user is blocked")
                await repo.upsert_user(buyer_telegram_id, buyer_username)
                if (
                    await repo.count_open_orders(buyer_telegram_id)
                    >= self.settings.max_open_orders_per_user
                ):
                    raise RuntimeError("Too many open orders")
                if (
                    await repo.daily_stars(buyer_telegram_id) + priced_option.option.stars
                    > self.settings.daily_stars_limit_per_user
                ):
                    raise RuntimeError("Daily Stars limit exceeded")
                order = await repo.create(
                    idempotency_key=key,
                    buyer_telegram_id=buyer_telegram_id,
                    buyer_username=buyer_username,
                    kind=kind,
                    recipient_telegram_id=recipient_telegram_id,
                    recipient_username=recipient_username,
                    priced_option=priced_option,
                )
                await repo.audit(
                    "order_created",
                    order_id=order.id,
                    actor_telegram_id=buyer_telegram_id,
                    details={"stars": order.stars, "kind": order.kind.value},
                )
                if order.status == OrderStatus.CREATED and self.settings.test_payment_mode:
                    payment = await self.payment_provider.create_payment(
                        order_id=order.id,
                        amount_rub=order.rub_amount,
                    )
                    await repo.setup_test_payment(order, payment.payment_id)
                    await repo.audit(
                        "payment_created", order_id=order.id, details={"provider": "test"}
                    )
                return order.id

    async def initiate_yookassa_payment(self, order_id: int) -> YooKassaPaymentInstructions:
        if self.yookassa_client is None:
            raise RuntimeError("YooKassa payment is not configured")
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = await repo.get(order_id, for_update=True)
                if order is None:
                    raise ValueError(f"Order {order_id} not found")
                if order.customer_payment_type not in {None, CustomerPaymentType.YOOKASSA}:
                    raise ValueError("Order uses another customer payment provider")
                if order.status not in {OrderStatus.CREATED, OrderStatus.WAITING_FOR_PAYMENT}:
                    raise ValueError(
                        f"Order {order_id} cannot start payment in {order.status.value}"
                    )
                payment = await repo.get_payment(order.id, for_update=True)
                if order.expires_at and self._as_utc(order.expires_at) <= datetime.now(UTC):
                    raise ValueError("Order payment window has expired")
                if payment and payment.confirmation_url:
                    return self._yookassa_instructions(order, payment.confirmation_url)
                idempotency_key = (
                    payment.idempotency_key
                    if payment and payment.idempotency_key
                    else str(
                        uuid5(NAMESPACE_URL, f"terstars:yookassa:payment:{order.order_number}")
                    )
                )
                await repo.setup_yookassa_payment(
                    order,
                    idempotency_key=idempotency_key,
                    expires_at=self.customer_pricing.order_expires_at()
                    if self.customer_pricing
                    else datetime.now(UTC) + timedelta(minutes=self.settings.order_timeout_minutes),
                )
                order_number = order.order_number
                amount_rub = order.rub_amount

        snapshot = await self.yookassa_client.create_payment(
            order_id=order_id,
            order_number=order_number,
            amount_rub=amount_rub,
            idempotency_key=idempotency_key,
        )
        await self.apply_yookassa_snapshot(snapshot)
        if not snapshot.confirmation_url:
            raise RuntimeError("YooKassa did not return a payment confirmation URL")
        return YooKassaPaymentInstructions(
            order_id=order_id,
            order_number=order_number,
            confirmation_url=snapshot.confirmation_url,
            rub_amount=f"{amount_rub:.2f}",
        )

    async def sync_yookassa_payment(
        self,
        order_id: int,
        *,
        webhook_event: str | None = None,
    ) -> OrderStatus:
        if self.yookassa_client is None:
            raise RuntimeError("YooKassa payment is not configured")
        async with self.session_factory() as session:
            repo = OrderRepository(session)
            order = await repo.get(order_id)
            payment = await repo.get_payment(order_id)
            if order is None or payment is None or payment.provider != "yookassa":
                raise ValueError("YooKassa payment not found")
            payment_id = payment.provider_reference
        if payment_id.startswith("pending:"):
            await self.initiate_yookassa_payment(order_id)
            async with self.session_factory() as session:
                payment = await OrderRepository(session).get_payment(order_id)
                if payment is None or payment.provider_reference.startswith("pending:"):
                    raise RuntimeError("YooKassa payment creation is still pending")
                payment_id = payment.provider_reference
        snapshot = await self.yookassa_client.get_payment(payment_id)
        return await self.apply_yookassa_snapshot(snapshot, webhook_event=webhook_event)

    async def sync_yookassa_payment_by_provider_id(
        self,
        payment_id: str,
        *,
        webhook_event: str,
    ) -> OrderStatus:
        if self.yookassa_client is None:
            raise RuntimeError("YooKassa payment is not configured")
        snapshot = await self.yookassa_client.get_payment(payment_id)
        return await self.apply_yookassa_snapshot(snapshot, webhook_event=webhook_event)

    async def apply_yookassa_snapshot(
        self,
        snapshot: YooKassaPayment,
        *,
        webhook_event: str | None = None,
    ) -> OrderStatus:
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = await repo.get(snapshot.order_id, for_update=True)
                if order is None or order.order_number != snapshot.order_number:
                    raise ValueError("YooKassa metadata does not match an order")
                if snapshot.currency != "RUB" or snapshot.amount != order.rub_amount:
                    raise ValueError("YooKassa payment amount or currency does not match the order")
                payment = await repo.get_payment(order.id, for_update=True)
                if payment is None or payment.provider != "yookassa":
                    raise ValueError("YooKassa payment record does not match the order")
                if payment.status == PaymentStatus.SUCCEEDED and snapshot.status != "succeeded":
                    raise ValueError("YooKassa payment status cannot regress after success")
                await repo.update_yookassa_payment(
                    order,
                    payment_id=snapshot.payment_id,
                    provider_status=snapshot.status,
                    confirmation_url=snapshot.confirmation_url,
                    paid=snapshot.paid,
                    refundable=snapshot.refundable,
                    webhook_event=webhook_event,
                )
                await self._transition_yookassa_status(repo, order, snapshot)
                await repo.audit(
                    "yookassa_payment_synced",
                    order_id=order.id,
                    details={
                        "provider_status": snapshot.status,
                        "payment_id": snapshot.payment_id,
                        "source": "webhook" if webhook_event else "api",
                    },
                )
                return order.status

    async def _transition_yookassa_status(
        self,
        repo: OrderRepository,
        order: Order,
        snapshot: YooKassaPayment,
    ) -> None:
        if snapshot.status == "pending":
            await repo.set_payment_status(order, PaymentStatus.PENDING)
            return
        if snapshot.status == "waiting_for_capture":
            if order.status == OrderStatus.WAITING_FOR_PAYMENT:
                await repo.transition(order, OrderStatus.PAYMENT_DETECTED)
            if order.status == OrderStatus.PAYMENT_DETECTED:
                await repo.transition(order, OrderStatus.PAYMENT_CONFIRMING)
            await repo.set_payment_status(order, PaymentStatus.WAITING_FOR_CAPTURE)
            return
        if snapshot.status == "canceled":
            if order.status == OrderStatus.PAYMENT_DETECTED:
                await repo.transition(order, OrderStatus.PAYMENT_CONFIRMING)
            await repo.set_payment_status(order, PaymentStatus.CANCELED)
            if order.status in {OrderStatus.WAITING_FOR_PAYMENT, OrderStatus.PAYMENT_CONFIRMING}:
                await repo.transition(
                    order,
                    OrderStatus.PAYMENT_FAILED,
                    error_code="YOOKASSA_PAYMENT_CANCELED",
                    reason="YooKassa payment was canceled",
                )
            return
        if snapshot.status == "succeeded":
            if not snapshot.paid:
                raise ValueError("YooKassa succeeded payment is not marked paid")
            if order.status in {OrderStatus.PAYMENT_EXPIRED, OrderStatus.CANCELLED}:
                await repo.set_payment_status(order, PaymentStatus.SUCCEEDED)
                await repo.transition(
                    order,
                    OrderStatus.MANUAL_REVIEW,
                    error_code="YOOKASSA_LATE_PAYMENT",
                    reason="Payment succeeded after the local order was closed",
                )
                return
            if order.status == OrderStatus.WAITING_FOR_PAYMENT:
                await repo.transition(order, OrderStatus.PAYMENT_DETECTED)
            if order.status == OrderStatus.PAYMENT_DETECTED:
                await repo.transition(order, OrderStatus.PAYMENT_CONFIRMING)
            await repo.set_payment_status(order, PaymentStatus.SUCCEEDED)
            if order.status == OrderStatus.PAYMENT_CONFIRMING:
                await repo.transition(order, OrderStatus.PAID)

    @staticmethod
    def _yookassa_instructions(order: Order, confirmation_url: str) -> YooKassaPaymentInstructions:
        return YooKassaPaymentInstructions(
            order_id=order.id,
            order_number=order.order_number,
            confirmation_url=confirmation_url,
            rub_amount=f"{order.rub_amount:.2f}",
        )

    async def initiate_ton_payment(self, order_id: int) -> TonPaymentInstructions:
        if (
            not self.customer_pricing
            or not self.ton_service
            or not self.settings.ton_wallet_address
        ):
            raise RuntimeError("TON payment is not configured")

        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                confirmed_order = await repo.get(order_id, for_update=True)
                if confirmed_order is None:
                    raise ValueError(f"Order {order_id} not found")
                order = confirmed_order
                if order.status == OrderStatus.WAITING_FOR_PAYMENT:
                    return self._payment_instructions(order)
                if order.status != OrderStatus.CREATED:
                    raise ValueError(
                        f"Order {order_id} cannot start payment in {order.status.value}"
                    )

                if (
                    order.quote_unit_price is None
                    or not order.quote_currency
                    or order.quote_expires_at is None
                ):
                    raise RuntimeError("Confirmed price quote is incomplete")
                quote = await self.customer_pricing.ton_payment_from_quote(
                    stars=order.stars,
                    unit_price=order.quote_unit_price,
                    unit_currency=order.quote_currency,
                    commission_percent=order.quote_commission_percent or Decimal("0"),
                    markup_percent=order.markup_percent,
                    rub_amount=order.rub_amount,
                    quote_expires_at=self._as_utc(order.quote_expires_at),
                )
                comment = self.ton_service.generate_payment_comment(order.id)
                expires_at = self.customer_pricing.order_expires_at()
                await repo.setup_ton_payment(
                    order,
                    ton_amount=quote.ton_amount,
                    rub_amount=quote.rub_amount,
                    payment_comment=comment,
                    destination=self.settings.ton_wallet_address,
                    expires_at=expires_at,
                )
                await repo.audit(
                    "payment_address_created",
                    order_id=order.id,
                    details={"currency": "TON", "network": "TON"},
                )
                return self._payment_instructions(order)

    async def confirm_test_payment(self, order_id: int, *, buyer_telegram_id: int) -> OrderStatus:
        if not self.settings.test_payment_mode:
            raise RuntimeError("Test payment is disabled")
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = self._require_owner(
                    await repo.get(order_id, for_update=True), buyer_telegram_id
                )
                if order.status in {
                    OrderStatus.PAID,
                    OrderStatus.PURCHASE_PROCESSING,
                    OrderStatus.STARS_SENDING,
                    OrderStatus.COMPLETED,
                }:
                    return order.status
                if order.status != OrderStatus.WAITING_FOR_PAYMENT or not order.payment_id:
                    return order.status
                await repo.transition(order, OrderStatus.PAYMENT_DETECTED)
                await repo.set_payment_status(order, PaymentStatus.DETECTED)
                await repo.audit(
                    "payment_detected", order_id=order.id, details={"provider": "test"}
                )
                await repo.transition(order, OrderStatus.PAYMENT_CONFIRMING)
                await repo.set_payment_status(order, PaymentStatus.CONFIRMING)
                payment_id = order.payment_id

        result = await self.payment_provider.confirm_payment(payment_id=payment_id)
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                payment_order = await repo.get(order_id, for_update=True)
                if payment_order is None:
                    raise ValueError(f"Order {order_id} not found")
                if payment_order.status != OrderStatus.PAYMENT_CONFIRMING:
                    return payment_order.status
                if not result.succeeded:
                    await repo.set_payment_status(payment_order, PaymentStatus.FAILED)
                    await repo.transition(
                        payment_order,
                        OrderStatus.PAYMENT_FAILED,
                        error_code="TEST_PAYMENT_FAILED",
                        reason="Payment confirmation failed",
                    )
                    return payment_order.status
                await repo.set_payment_status(payment_order, PaymentStatus.SUCCEEDED)
                await repo.transition(payment_order, OrderStatus.PAID)
                await repo.audit(
                    "payment_confirmed",
                    order_id=payment_order.id,
                    details={"provider": "test"},
                )
                return payment_order.status

    async def record_ton_observation(
        self,
        order_id: int,
        observation: TonPaymentObservation,
    ) -> OrderStatus:
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = await repo.get(order_id, for_update=True)
                if order is None:
                    raise ValueError(f"Order {order_id} not found")
                if order.status == OrderStatus.COMPLETED:
                    return order.status
                if (
                    order.customer_payment_type != CustomerPaymentType.TON
                    or order.ton_amount is None
                ):
                    return order.status
                if order.status not in {
                    OrderStatus.WAITING_FOR_PAYMENT,
                    OrderStatus.PAYMENT_DETECTED,
                    OrderStatus.PAYMENT_CONFIRMING,
                    OrderStatus.PAYMENT_EXPIRED,
                    OrderStatus.CANCELLED,
                }:
                    return order.status

                if (
                    observation.destination != order.payment_destination
                    or observation.currency.upper() != "TON"
                    or observation.network.upper() != "TON"
                ):
                    await repo.set_payment_status(order, PaymentStatus.MANUAL_REVIEW)
                    await repo.transition(
                        order,
                        OrderStatus.MANUAL_REVIEW,
                        error_code="PAYMENT_ROUTE_MISMATCH",
                        reason="Payment destination, currency, or network is invalid",
                    )
                    await repo.audit("payment_route_mismatch", order_id=order.id)
                    return order.status

                if not order.ton_tx_hash:
                    attached = await repo.record_payment_detection(
                        order,
                        transaction_hash=observation.transaction_hash,
                        received_amount=observation.amount,
                        detected_at=observation.timestamp,
                    )
                    if not attached:
                        await repo.transition(
                            order,
                            OrderStatus.MANUAL_REVIEW,
                            error_code="DUPLICATE_PAYMENT_TRANSACTION",
                            reason="Payment transaction is already linked to another order",
                        )
                        await repo.audit("duplicate_payment_detected", order_id=order.id)
                        return order.status
                elif order.ton_tx_hash != observation.transaction_hash:
                    await repo.transition(
                        order,
                        OrderStatus.MANUAL_REVIEW,
                        error_code="MULTIPLE_PAYMENT_TRANSACTIONS",
                        reason="Multiple transactions require review",
                    )
                    return order.status

                if observation.amount != order.ton_amount:
                    code = (
                        "PAYMENT_UNDERPAID"
                        if observation.amount < order.ton_amount
                        else "PAYMENT_OVERPAID"
                    )
                    await repo.set_payment_status(order, PaymentStatus.MANUAL_REVIEW)
                    await repo.transition(
                        order,
                        OrderStatus.MANUAL_REVIEW,
                        error_code=code,
                        reason="Payment amount differs from the invoice",
                    )
                    await repo.audit(
                        "payment_amount_mismatch", order_id=order.id, details={"code": code}
                    )
                    return order.status
                if order.status in {OrderStatus.PAYMENT_EXPIRED, OrderStatus.CANCELLED}:
                    await repo.set_payment_status(order, PaymentStatus.MANUAL_REVIEW)
                    await repo.transition(
                        order,
                        OrderStatus.MANUAL_REVIEW,
                        error_code="LATE_PAYMENT",
                        reason="Payment arrived after order closure",
                    )
                    await repo.audit("late_payment_detected", order_id=order.id)
                    return order.status
                if order.expires_at and observation.timestamp > self._as_utc(order.expires_at):
                    await repo.set_payment_status(order, PaymentStatus.MANUAL_REVIEW)
                    await repo.transition(
                        order,
                        OrderStatus.MANUAL_REVIEW,
                        error_code="LATE_PAYMENT",
                        reason="Payment arrived after expiration",
                    )
                    await repo.audit("late_payment_detected", order_id=order.id)
                    return order.status

                if order.status == OrderStatus.WAITING_FOR_PAYMENT:
                    await repo.transition(order, OrderStatus.PAYMENT_DETECTED)
                    await repo.audit(
                        "payment_detected", order_id=order.id, details={"network": "TON"}
                    )
                    return order.status
                if order.status == OrderStatus.PAYMENT_DETECTED:
                    await repo.transition(order, OrderStatus.PAYMENT_CONFIRMING)
                    await repo.set_payment_status(order, PaymentStatus.CONFIRMING)
                    return order.status
                age = (datetime.now(UTC) - observation.timestamp).total_seconds()
                if age < self.settings.payment_confirmation_seconds:
                    return order.status

                await repo.set_payment_status(order, PaymentStatus.SUCCEEDED)
                await repo.transition(order, OrderStatus.PAID)
                await repo.audit("payment_confirmed", order_id=order.id, details={"network": "TON"})
                logger.info(
                    "TON payment confirmed",
                    extra={
                        "event": "payment_confirmed",
                        "order_id": order.id,
                        "latency_seconds": max(
                            0,
                            int((datetime.now(UTC) - observation.timestamp).total_seconds()),
                        ),
                    },
                )
                return order.status

    async def deliver_paid_order(self, order_id: int) -> tuple[OrderStatus, str | None]:
        lock = self._delivery_locks.get(order_id)
        if lock is None:
            lock = Lock()
            self._delivery_locks[order_id] = lock
        async with lock:
            return await self._deliver_paid_order(order_id)

    async def _deliver_paid_order(self, order_id: int) -> tuple[OrderStatus, str | None]:
        provider = self.stars_provider_factory()
        attempt: PurchaseAttempt | None = None
        is_new = False
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = await repo.get(order_id, for_update=True)
                if order is None:
                    raise ValueError(f"Order {order_id} not found")
                if order.status == OrderStatus.COMPLETED:
                    return order.status, None
                if order.status == OrderStatus.PAID:
                    if self.settings.test_payment_mode and self.settings.test_payment_status_delay_seconds:
                        paid_at = self._as_utc(order.status_changed_at)
                        elapsed = (datetime.now(UTC) - paid_at).total_seconds()
                        if elapsed < self.settings.test_payment_status_delay_seconds:
                            return order.status, "Test payment status delay"
                    enabled = await repo.get_runtime_bool(
                        "purchases_enabled", self.settings.purchases_enabled
                    )
                    if not enabled:
                        return order.status, "Purchases are paused"
                    payment = await repo.get_payment(order.id, for_update=True)
                    validation_error = self._purchase_validation_error(order, payment)
                    if validation_error is not None:
                        await repo.transition(
                            order,
                            OrderStatus.MANUAL_REVIEW,
                            error_code=validation_error,
                            reason="Paid order failed the final purchase validation",
                        )
                        await repo.audit(
                            "manual_review",
                            order_id=order.id,
                            details={"error_code": validation_error},
                        )
                        return order.status, order.failure_reason
                    attempt, is_new = await repo.create_purchase_attempt(
                        order, provider.provider_name
                    )
                    await repo.transition(order, OrderStatus.PURCHASE_PROCESSING)
                    await repo.audit(
                        "purchase_started",
                        order_id=order.id,
                        details={"provider": provider.provider_name},
                    )
                elif order.status in {OrderStatus.PURCHASE_PROCESSING, OrderStatus.STARS_SENDING}:
                    attempt = await repo.get_purchase_attempt(order.id, for_update=True)
                    if attempt is None:
                        await repo.transition(
                            order,
                            OrderStatus.MANUAL_REVIEW,
                            error_code="PURCHASE_ATTEMPT_MISSING",
                            reason="Purchase state cannot be reconciled automatically",
                        )
                        return order.status, order.failure_reason
                    is_new = bool(
                        attempt.provider_request_id is None
                        and attempt.status == PurchaseAttemptStatus.SUBMITTING
                        and attempt.error_code == "MERCHANT_BALANCE_RETRY_APPROVED"
                    )
                else:
                    return order.status, order.failure_reason

        if attempt is None:
            return await self._mark_uncertain(order_id, "PURCHASE_ATTEMPT_MISSING")
        if attempt.provider_request_id:
            checker = getattr(provider, "check_purchase", None)
            if checker is None:
                return await self._mark_uncertain(
                    order_id,
                    "PROVIDER_RECONCILIATION_UNAVAILABLE",
                )
            result = await checker(attempt.provider_request_id)
            return await self._apply_purchase_result(order_id, result)

        if not is_new:
            return await self._mark_uncertain(order_id, "PURCHASE_RESPONSE_NOT_PERSISTED")

        starter = getattr(provider, "start_purchase", None)
        try:
            if starter is not None:
                result = await starter(order)
            else:
                result = (
                    await provider.purchase_for_self(order)
                    if order.kind == OrderKind.SELF
                    else await provider.purchase_as_gift(order)
                )
        except Exception:
            logger.exception(
                "Stars provider call ended without a persisted response",
                extra={"event": "purchase_response_uncertain", "order_id": order_id},
            )
            return await self._mark_uncertain(order_id, "PURCHASE_PROVIDER_RESPONSE_UNCERTAIN")
        return await self._apply_purchase_result(order_id, result)

    async def _apply_purchase_result(
        self,
        order_id: int,
        result: StarsPurchaseResult,
    ) -> tuple[OrderStatus, str | None]:
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = await repo.get(order_id, for_update=True)
                if order is None:
                    raise ValueError(f"Order {order_id} not found")
                attempt = await repo.get_purchase_attempt(order_id, for_update=True)
                if attempt is None:
                    raise RuntimeError("Purchase attempt is missing")
                if order.status == OrderStatus.COMPLETED:
                    return order.status, None
                if result.pending and result.request_id:
                    if order.status in {
                        OrderStatus.PURCHASE_PROCESSING,
                        OrderStatus.MANUAL_REVIEW,
                    }:
                        await repo.mark_purchase_queued(order, attempt, result.request_id)
                        await repo.audit(
                            "purchase_queued",
                            order_id=order.id,
                            details={"provider": attempt.provider},
                        )
                    else:
                        await repo.mark_purchase_processing(attempt)
                    return order.status, None
                if result.succeeded:
                    if not result.transaction_id:
                        await repo.mark_purchase_failed(
                            order,
                            attempt,
                            error_code="PURCHASE_REFERENCE_MISSING",
                            uncertain=True,
                        )
                        await repo.audit(
                            "manual_review",
                            order_id=order.id,
                            details={"error_code": "PURCHASE_REFERENCE_MISSING"},
                        )
                        return order.status, order.failure_reason
                    if order.status == OrderStatus.PURCHASE_PROCESSING:
                        await repo.transition(order, OrderStatus.STARS_SENDING)
                        await repo.audit(
                            "stars_sending",
                            order_id=order.id,
                            details={"provider": attempt.provider},
                        )
                    persisted = await repo.mark_purchase_succeeded(
                        order,
                        attempt,
                        result.transaction_id,
                    )
                    if not persisted:
                        await repo.audit(
                            "manual_review",
                            order_id=order.id,
                            details={"error_code": "DUPLICATE_PROVIDER_TRANSACTION"},
                        )
                        return order.status, order.failure_reason
                    await repo.audit(
                        "purchase_success",
                        order_id=order.id,
                        details={"provider": attempt.provider},
                    )
                    logger.info(
                        "Order completed",
                        extra={"event": "order_completed", "order_id": order.id},
                    )
                    return order.status, None
                error_code = result.error_code or "PURCHASE_FAILED"
                if error_code in {"INSUFFICIENT_BALANCE", "INSUFFICIENT_WALLET_BALANCE"}:
                    await repo.mark_waiting_for_merchant_balance(
                        order,
                        attempt,
                        error_code=error_code,
                    )
                    await repo.audit(
                        "merchant_balance_insufficient",
                        order_id=order.id,
                        details={"error_code": error_code},
                    )
                    return order.status, order.failure_reason
                await repo.mark_purchase_failed(
                    order,
                    attempt,
                    error_code=error_code,
                    uncertain=result.uncertain,
                )
                await repo.audit(
                    "manual_review" if result.uncertain else "purchase_failed",
                    order_id=order.id,
                    details={"error_code": error_code},
                )
                return order.status, order.failure_reason

    async def _mark_uncertain(
        self, order_id: int, error_code: str
    ) -> tuple[OrderStatus, str | None]:
        return await self._apply_purchase_result(
            order_id,
            StarsPurchaseResult(
                succeeded=False,
                uncertain=True,
                error_code=error_code,
                reason="Purchase result is uncertain",
            ),
        )

    async def reconcile_order(self, order_id: int, *, actor_telegram_id: int) -> OrderStatus:
        provider = self.stars_provider_factory()
        checker = getattr(provider, "check_purchase", None)
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = await repo.get(order_id, for_update=True)
                if order is None:
                    raise ValueError(f"Order {order_id} not found")
                attempt = await repo.get_purchase_attempt(order_id, for_update=True)
                await repo.audit(
                    "reconciliation_started",
                    order_id=order_id,
                    actor_telegram_id=actor_telegram_id,
                )
                await repo.audit(
                    "admin_action",
                    order_id=order_id,
                    actor_telegram_id=actor_telegram_id,
                    details={"action": "reconcile"},
                )
                if order.status == OrderStatus.COMPLETED:
                    return order.status
                if checker is None or attempt is None or not attempt.provider_request_id:
                    if order.status != OrderStatus.MANUAL_REVIEW:
                        await repo.transition(
                            order,
                            OrderStatus.MANUAL_REVIEW,
                            error_code="RECONCILIATION_DATA_MISSING",
                            reason="Provider request id is unavailable",
                        )
                    return order.status
                request_id = attempt.provider_request_id

        result = await checker(request_id)
        status, _ = await self._apply_purchase_result(order_id, result)
        return status

    async def process_recoverable_orders(self) -> None:
        async with self.session_factory() as session:
            repo = OrderRepository(session)
            orders = await repo.list_by_status(
                OrderStatus.PAID,
                OrderStatus.PURCHASE_PROCESSING,
                OrderStatus.STARS_SENDING,
                limit=self.settings.worker_batch_size,
            )
            order_ids = [order.id for order in orders]
        for order_id in order_ids:
            try:
                await self.deliver_paid_order(order_id)
            except Exception:
                logger.exception(
                    "Recoverable order processing failed",
                    extra={"event": "order_processing_failed", "order_id": order_id},
                )

    async def expire_if_needed(self, order_id: int) -> OrderStatus:
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = await repo.get(order_id, for_update=True)
                if order is None:
                    raise ValueError(f"Order {order_id} not found")
                if order.status not in {
                    OrderStatus.WAITING_FOR_PAYMENT,
                    OrderStatus.PAYMENT_DETECTED,
                    OrderStatus.PAYMENT_CONFIRMING,
                }:
                    return order.status
                if order.expires_at and self._as_utc(order.expires_at) <= datetime.now(UTC):
                    if order.status == OrderStatus.WAITING_FOR_PAYMENT:
                        await repo.set_payment_status(order, PaymentStatus.EXPIRED)
                        await repo.transition(order, OrderStatus.PAYMENT_EXPIRED)
                        await repo.audit("payment_expired", order_id=order.id)
                    else:
                        await repo.set_payment_status(order, PaymentStatus.MANUAL_REVIEW)
                        await repo.transition(
                            order,
                            OrderStatus.MANUAL_REVIEW,
                            error_code="PAYMENT_CONFIRMATION_LOST",
                            reason="Previously detected payment is no longer visible after expiration",
                        )
                        await repo.audit(
                            "manual_review",
                            order_id=order.id,
                            details={"error_code": "PAYMENT_CONFIRMATION_LOST"},
                        )
                return order.status

    async def cancel_order(self, order_id: int, *, buyer_telegram_id: int) -> OrderStatus:
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = self._require_owner(
                    await repo.get(order_id, for_update=True), buyer_telegram_id
                )
                if order.status not in {OrderStatus.CREATED, OrderStatus.WAITING_FOR_PAYMENT}:
                    return order.status
                payment = await repo.get_payment(order.id, for_update=True)
                if payment is not None and payment.provider == "yookassa":
                    return order.status
                if order.ton_tx_hash:
                    return order.status
                await repo.transition(order, OrderStatus.CANCELLED)
                await repo.audit(
                    "order_cancelled",
                    order_id=order.id,
                    actor_telegram_id=buyer_telegram_id,
                )
                return order.status

    async def is_order_owner(self, order_id: int, telegram_id: int) -> bool:
        async with self.session_factory() as session:
            repo = OrderRepository(session)
            order = await repo.get(order_id)
            return order is not None and order.buyer_telegram_id == telegram_id

    async def get_order_snapshot(self, order_id: int) -> tuple[OrderStatus, str | None, str | None]:
        async with self.session_factory() as session:
            repo = OrderRepository(session)
            order = await repo.get(order_id)
            if order is None:
                raise ValueError(f"Order {order_id} not found")
            return order.status, order.failure_reason, order.telegram_transaction_id

    async def get_order_summary(self, order_id: int) -> OrderSummary:
        async with self.session_factory() as session:
            repo = OrderRepository(session)
            order = await repo.get(order_id)
            if order is None:
                raise ValueError(f"Order {order_id} not found")
            return OrderSummary(
                order_id=order.id,
                order_number=order.order_number,
                buyer_telegram_id=order.buyer_telegram_id,
                recipient_username=order.recipient_username,
                stars=order.stars,
                status=order.status,
                error_code=order.error_code,
                payment_tx=order.ton_tx_hash,
                purchase_request_id=order.fragment_request_id,
                purchase_tx=order.telegram_transaction_id,
            )

    async def list_status(self, status: OrderStatus, *, limit: int = 20) -> list[OrderSummary]:
        async with self.session_factory() as session:
            repo = OrderRepository(session)
            orders = await repo.list_by_status(status, limit=limit)
            return [
                OrderSummary(
                    order_id=order.id,
                    order_number=order.order_number,
                    buyer_telegram_id=order.buyer_telegram_id,
                    recipient_username=order.recipient_username,
                    stars=order.stars,
                    status=order.status,
                    error_code=order.error_code,
                    payment_tx=order.ton_tx_hash,
                    purchase_request_id=order.fragment_request_id,
                    purchase_tx=order.telegram_transaction_id,
                )
                for order in orders
            ]

    async def list_user_orders(self, telegram_id: int, *, limit: int = 20) -> list[OrderSummary]:
        async with self.session_factory() as session:
            orders = await OrderRepository(session).list_for_user(telegram_id, limit=limit)
            return [self._summary(order) for order in orders]

    async def list_stuck_orders(self, *, limit: int = 20) -> list[OrderSummary]:
        async with self.session_factory() as session:
            orders = await OrderRepository(session).list_by_status(
                OrderStatus.PAYMENT_CONFIRMING,
                OrderStatus.PAID,
                OrderStatus.PURCHASE_PROCESSING,
                OrderStatus.STARS_SENDING,
                OrderStatus.MANUAL_REVIEW,
                OrderStatus.REFUND_REQUIRED,
                OrderStatus.WAITING_FOR_MERCHANT_BALANCE,
                limit=limit,
            )
            return [self._summary(order) for order in orders]

    async def safe_retry_order(self, order_id: int, *, actor_telegram_id: int) -> OrderStatus:
        summary = await self.get_order_summary(order_id)
        if summary.status == OrderStatus.MANUAL_REVIEW and summary.purchase_request_id:
            return await self.reconcile_order(order_id, actor_telegram_id=actor_telegram_id)
        if summary.status in {
            OrderStatus.PAID,
            OrderStatus.PURCHASE_PROCESSING,
            OrderStatus.STARS_SENDING,
        }:
            async with self.session_factory() as session:
                async with session.begin():
                    await OrderRepository(session).audit(
                        "retry_started",
                        order_id=order_id,
                        actor_telegram_id=actor_telegram_id,
                    )
                    await OrderRepository(session).audit(
                        "admin_action",
                        order_id=order_id,
                        actor_telegram_id=actor_telegram_id,
                        details={"action": "safe_retry"},
                    )
            status, _ = await self.deliver_paid_order(order_id)
            return status
        if summary.status == OrderStatus.WAITING_FOR_MERCHANT_BALANCE:
            async with self.session_factory() as session:
                async with session.begin():
                    repo = OrderRepository(session)
                    order = await repo.get(order_id, for_update=True)
                    if order is None:
                        raise ValueError("Order not found")
                    attempt = await repo.get_purchase_attempt(order_id, for_update=True)
                    if attempt is None or attempt.provider_request_id:
                        raise ValueError("Balance retry is not safe for this order")
                    attempt.status = PurchaseAttemptStatus.SUBMITTING
                    attempt.error_code = "MERCHANT_BALANCE_RETRY_APPROVED"
                    attempt.completed_at = None
                    await repo.transition(order, OrderStatus.PURCHASE_PROCESSING)
                    await repo.audit(
                        "merchant_balance_retry",
                        order_id=order_id,
                        actor_telegram_id=actor_telegram_id,
                    )
            status, _ = await self.deliver_paid_order(order_id)
            return status
        raise ValueError("Order has no safe retry path")

    async def admin_cancel_order(self, order_id: int, *, actor_telegram_id: int) -> OrderStatus:
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = await repo.get(order_id, for_update=True)
                if order is None:
                    raise ValueError(f"Order {order_id} not found")
                if order.status not in {OrderStatus.CREATED, OrderStatus.WAITING_FOR_PAYMENT}:
                    raise ValueError("Only unpaid orders can be cancelled")
                payment = await repo.get_payment(order.id, for_update=True)
                if payment is not None and payment.provider == "yookassa":
                    raise ValueError("YooKassa payment must be reconciled, not locally canceled")
                if order.ton_tx_hash:
                    raise ValueError("A detected payment prevents cancellation")
                await repo.transition(order, OrderStatus.CANCELLED)
                await repo.audit(
                    "admin_action",
                    order_id=order.id,
                    actor_telegram_id=actor_telegram_id,
                    details={"action": "cancel_order"},
                )
                return order.status

    async def admin_move_to_manual_review(
        self, order_id: int, *, actor_telegram_id: int
    ) -> OrderStatus:
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = await repo.get(order_id, for_update=True)
                if order is None:
                    raise ValueError(f"Order {order_id} not found")
                if order.status in {
                    OrderStatus.COMPLETED,
                    OrderStatus.REFUNDED,
                    OrderStatus.PAYMENT_FAILED,
                }:
                    raise ValueError("Terminal order cannot move to manual review")
                await repo.transition(
                    order,
                    OrderStatus.MANUAL_REVIEW,
                    error_code="ADMIN_MANUAL_REVIEW",
                    reason="Administrator moved the order to manual review",
                )
                await repo.audit(
                    "manual_review",
                    order_id=order.id,
                    actor_telegram_id=actor_telegram_id,
                    details={"source": "admin"},
                )
                return order.status

    async def admin_resend_notification(
        self,
        order_id: int,
        *,
        actor_telegram_id: int,
    ) -> OrderStatus:
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = await repo.get(order_id, for_update=True)
                if order is None:
                    raise ValueError(f"Order {order_id} not found")
                await repo.enqueue_user_notification(order)
                await repo.audit(
                    "notification_resent",
                    order_id=order.id,
                    actor_telegram_id=actor_telegram_id,
                    details={"audience": "user", "status": order.status.value},
                )
                return order.status

    async def mark_refunded(self, order_id: int, *, actor_telegram_id: int) -> OrderStatus:
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = await repo.get(order_id, for_update=True)
                if order is None:
                    raise ValueError(f"Order {order_id} not found")
                if order.status != OrderStatus.REFUND_REQUIRED:
                    raise ValueError("Only REFUND_REQUIRED orders can be marked refunded")
                payment = await repo.get_payment(order.id, for_update=True)
                if (
                    payment is not None
                    and payment.provider == "yookassa"
                    and payment.refund_status != "succeeded"
                ):
                    raise ValueError("YooKassa refund must be verified through the provider API")
                await repo.transition(order, OrderStatus.REFUNDED)
                await repo.audit(
                    "refund",
                    order_id=order.id,
                    actor_telegram_id=actor_telegram_id,
                    details={"mode": "manual_confirmation"},
                )
                await repo.audit(
                    "admin_action",
                    order_id=order.id,
                    actor_telegram_id=actor_telegram_id,
                    details={"action": "mark_refunded"},
                )
                return order.status

    async def request_yookassa_refund(
        self,
        order_id: int,
        *,
        actor_telegram_id: int,
    ) -> OrderStatus:
        """Issue an owner-confirmed refund; this method is never called automatically."""
        if self.yookassa_client is None:
            raise RuntimeError("YooKassa is not configured")
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = await repo.get(order_id, for_update=True)
                payment = await repo.get_payment(order_id, for_update=True)
                attempt = await repo.get_purchase_attempt(order_id, for_update=True)
                if order is None or payment is None or payment.provider != "yookassa":
                    raise ValueError("YooKassa payment not found")
                if order.status != OrderStatus.REFUND_REQUIRED:
                    raise ValueError("Only REFUND_REQUIRED orders can be refunded")
                unsafe_attempts = {
                    PurchaseAttemptStatus.SUBMITTING,
                    PurchaseAttemptStatus.QUEUED,
                    PurchaseAttemptStatus.PROCESSING,
                    PurchaseAttemptStatus.SUCCEEDED,
                    PurchaseAttemptStatus.UNCERTAIN,
                }
                if attempt is not None and attempt.status in unsafe_attempts:
                    raise ValueError(
                        "Refund is blocked until the Fragment purchase outcome is definitive"
                    )
                if payment.status != PaymentStatus.SUCCEEDED or not payment.refundable:
                    raise ValueError("YooKassa payment is not refundable")
                idempotency_key = payment.refund_idempotency_key or str(
                    uuid5(NAMESPACE_URL, f"terstars:yookassa:refund:{order.order_number}")
                )
                payment.refund_idempotency_key = idempotency_key
                payment_id = payment.provider_reference
                amount = payment.expected_amount

        refund = await self.yookassa_client.create_refund(
            payment_id=payment_id,
            amount_rub=amount,
            idempotency_key=idempotency_key,
        )
        if refund.payment_id != payment_id or refund.amount != amount or refund.currency != "RUB":
            raise ValueError("YooKassa refund does not match the stored payment")
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = await repo.get(order_id, for_update=True)
                payment = await repo.get_payment(order_id, for_update=True)
                if order is None or payment is None:
                    raise ValueError("Order disappeared during refund")
                payment.refund_id = refund.refund_id
                payment.refund_status = refund.status
                payment.refund_amount = refund.amount
                if refund.status == "succeeded" and order.status == OrderStatus.REFUND_REQUIRED:
                    await repo.transition(order, OrderStatus.REFUNDED)
                await repo.audit(
                    "yookassa_refund_synced",
                    order_id=order.id,
                    actor_telegram_id=actor_telegram_id,
                    details={"refund_id": refund.refund_id, "status": refund.status},
                )
                return order.status

    async def sync_yookassa_refund(self, refund_id: str) -> OrderStatus:
        if self.yookassa_client is None:
            raise RuntimeError("YooKassa is not configured")
        refund = await self.yookassa_client.get_refund(refund_id)
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                payment = await repo.get_payment_by_refund_id(refund.refund_id, for_update=True)
                if payment is None:
                    raise ValueError("YooKassa refund does not match an internal payment")
                order = await repo.get(payment.order_id, for_update=True)
                if order is None:
                    raise ValueError("Refund order not found")
                if (
                    refund.payment_id != payment.provider_reference
                    or refund.amount != payment.expected_amount
                    or refund.currency != "RUB"
                ):
                    raise ValueError("YooKassa refund does not match the stored payment")
                payment.refund_status = refund.status
                payment.refund_amount = refund.amount
                if refund.status == "succeeded" and order.status == OrderStatus.REFUND_REQUIRED:
                    await repo.transition(order, OrderStatus.REFUNDED)
                await repo.audit(
                    "yookassa_refund_synced",
                    order_id=order.id,
                    details={
                        "refund_id": refund.refund_id,
                        "status": refund.status,
                        "source": "webhook",
                    },
                )
                return order.status

    async def stats(self) -> dict[str, int]:
        async with self.session_factory() as session:
            return await OrderRepository(session).stats()

    async def get_runtime_controls(self) -> tuple[bool, bool]:
        async with self.session_factory() as session:
            repo = OrderRepository(session)
            return (
                await repo.get_runtime_bool("maintenance_mode", self.settings.maintenance_mode),
                await repo.get_runtime_bool("purchases_enabled", self.settings.purchases_enabled),
            )

    async def set_runtime_control(self, key: str, value: bool, *, actor_telegram_id: int) -> None:
        if key not in {"maintenance_mode", "purchases_enabled"}:
            raise ValueError("Unsupported runtime control")
        async with self.session_factory() as session:
            async with session.begin():
                await OrderRepository(session).set_runtime_bool(key, value, actor_telegram_id)

    async def audit_admin_action(
        self,
        action: str,
        *,
        actor_telegram_id: int,
        order_id: int | None = None,
    ) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                await OrderRepository(session).audit(
                    "admin_action",
                    order_id=order_id,
                    actor_telegram_id=actor_telegram_id,
                    details={"action": action[:64]},
                )

    async def set_customer_message(self, order_id: int, *, chat_id: int, message_id: int) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                order = await repo.get(order_id, for_update=True)
                if order is None:
                    raise ValueError("Order not found")
                await repo.set_customer_message(order, chat_id=chat_id, message_id=message_id)

    async def get_order_number(self, order_id: int) -> str:
        return (await self.get_order_summary(order_id)).order_number

    async def get_payment_confirmation_url(self, order_id: int) -> str | None:
        async with self.session_factory() as session:
            payment = await OrderRepository(session).get_payment(order_id)
            return payment.confirmation_url if payment else None

    def _payment_instructions(self, order: Order) -> TonPaymentInstructions:
        if not order.ton_amount or not order.payment_comment or not order.expires_at:
            raise RuntimeError("Order payment instructions are incomplete")
        return TonPaymentInstructions(
            order_id=order.id,
            order_number=order.order_number,
            ton_amount=format(order.ton_amount, "f"),
            wallet_address=order.payment_destination or self.settings.ton_wallet_address or "",
            payment_comment=order.payment_comment,
            rub_amount=format(order.rub_amount, "f"),
            expires_at=self._as_utc(order.expires_at),
        )

    def _purchase_validation_error(self, order: Order, payment: Payment | None) -> str | None:
        try:
            validate_stars_amount(
                order.stars,
                minimum=self.settings.min_stars,
                maximum=self.settings.max_stars,
            )
        except ValueError:
            return "STARS_AMOUNT_INVALID"
        if order.recipient_telegram_id is None or order.recipient_telegram_id <= 0:
            return "RECIPIENT_VALIDATION_FAILED"
        if order.kind == OrderKind.SELF and order.recipient_telegram_id != order.buyer_telegram_id:
            return "RECIPIENT_VALIDATION_FAILED"
        if self.settings.is_fragment_mode and not (
            order.buyer_username if order.kind == OrderKind.SELF else order.recipient_username
        ):
            return "RECIPIENT_USERNAME_MISSING"
        if payment is None or payment.order_id != order.id:
            return "PAYMENT_RECORD_MISSING"
        if (
            payment.status != PaymentStatus.SUCCEEDED
            or order.payment_status != PaymentStatus.SUCCEEDED
        ):
            return "PAYMENT_NOT_CONFIRMED"
        if order.customer_payment_type == CustomerPaymentType.TON:
            if not payment.transaction_hash or payment.transaction_hash != order.ton_tx_hash:
                return "PAYMENT_TRANSACTION_MISMATCH"
            if order.ton_amount is None or payment.received_amount != order.ton_amount:
                return "PAYMENT_AMOUNT_MISMATCH"
            if (
                payment.destination != order.payment_destination
                or payment.currency.upper() != "TON"
                or payment.network.upper() != "TON"
            ):
                return "PAYMENT_ROUTE_MISMATCH"
        return None

    @staticmethod
    def _summary(order: Order) -> OrderSummary:
        return OrderSummary(
            order_id=order.id,
            order_number=order.order_number,
            buyer_telegram_id=order.buyer_telegram_id,
            recipient_username=order.recipient_username,
            stars=order.stars,
            status=order.status,
            error_code=order.error_code,
            payment_tx=order.ton_tx_hash,
            purchase_request_id=order.fragment_request_id,
            purchase_tx=order.telegram_transaction_id,
        )

    @staticmethod
    def _require_owner(order: Order | None, buyer_telegram_id: int) -> Order:
        if order is None:
            raise ValueError("Order not found")
        if order.buyer_telegram_id != buyer_telegram_id:
            raise PermissionError("Order belongs to another user")
        return order

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _build_idempotency_key(
        *,
        buyer_telegram_id: int,
        kind: OrderKind,
        recipient_telegram_id: int | None,
        stars: int,
        request_id: str | None = None,
    ) -> str:
        recipient = (
            recipient_telegram_id if recipient_telegram_id is not None else buyer_telegram_id
        )
        scope = request_id or "default"
        return f"{buyer_telegram_id}:{kind.value}:{recipient}:{stars}:{scope}"
