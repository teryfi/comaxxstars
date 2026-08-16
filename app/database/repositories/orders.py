import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AuditEvent,
    BlockedTelegramUser,
    Order,
    OrderNotification,
    Payment,
    PurchaseAttempt,
    RuntimeSetting,
    User,
)
from app.domain import (
    CustomerPaymentType,
    OrderKind,
    OrderStatus,
    PaymentStatus,
    PricedStarsOption,
    PurchaseAttemptStatus,
    ensure_order_transition,
)

OPEN_ORDER_STATUSES = (
    OrderStatus.CREATED,
    OrderStatus.WAITING_FOR_PAYMENT,
    OrderStatus.PAYMENT_DETECTED,
    OrderStatus.PAYMENT_CONFIRMING,
    OrderStatus.PAID,
    OrderStatus.PURCHASE_PROCESSING,
    OrderStatus.STARS_SENDING,
    OrderStatus.MANUAL_REVIEW,
    OrderStatus.REFUND_REQUIRED,
    OrderStatus.WAITING_FOR_MERCHANT_BALANCE,
)

SENSITIVE_DETAIL_KEYS = frozenset(
    {"seed", "token", "password", "cookie", "credential", "private_key", "api_key"}
)
USER_NOTIFICATION_STATUSES = frozenset(
    {
        OrderStatus.PAID,
        OrderStatus.COMPLETED,
        OrderStatus.PAYMENT_EXPIRED,
        OrderStatus.PAYMENT_FAILED,
        OrderStatus.REFUND_REQUIRED,
        OrderStatus.REFUNDED,
        OrderStatus.CANCELLED,
        OrderStatus.MANUAL_REVIEW,
        OrderStatus.WAITING_FOR_MERCHANT_BALANCE,
    }
)
ADMIN_NOTIFICATION_STATUSES = frozenset(
    {
        OrderStatus.MANUAL_REVIEW,
        OrderStatus.REFUND_REQUIRED,
        OrderStatus.WAITING_FOR_MERCHANT_BALANCE,
        OrderStatus.PAID,
    }
)


@dataclass(frozen=True)
class ClaimedNotification:
    notification_id: int
    order_id: int
    buyer_telegram_id: int
    status: OrderStatus
    audience: str
    order_number: str
    stars: int
    recipient_username: str | None
    customer_chat_id: int | None
    customer_message_id: int | None
    error_code: str | None


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, order_id: int, for_update: bool = False) -> Order | None:
        statement = select(Order).where(Order.id == order_id)
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_by_idempotency_key(self, key: str) -> Order | None:
        return await self.session.scalar(select(Order).where(Order.idempotency_key == key))

    async def get_by_order_number(self, order_number: str) -> Order | None:
        return await self.session.scalar(
            select(Order).where(Order.order_number == order_number.strip().upper())
        )

    async def is_user_blocked(self, telegram_id: int) -> bool:
        now = datetime.now(UTC)
        return (
            await self.session.scalar(
                select(BlockedTelegramUser.telegram_user_id).where(
                    BlockedTelegramUser.telegram_user_id == telegram_id,
                    or_(
                        BlockedTelegramUser.expires_at.is_(None),
                        BlockedTelegramUser.expires_at > now,
                    ),
                )
            )
            is not None
        )

    async def list_by_status(self, *statuses: OrderStatus, limit: int = 200) -> list[Order]:
        statement = (
            select(Order)
            .where(Order.status.in_(statuses))
            .order_by(Order.status_changed_at.asc(), Order.id.asc())
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def list_for_user(self, telegram_id: int, *, limit: int = 20) -> list[Order]:
        statement = (
            select(Order)
            .where(Order.buyer_telegram_id == telegram_id)
            .order_by(Order.id.desc())
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def upsert_user(self, telegram_id: int, username: str | None) -> None:
        user = await self.session.scalar(
            select(User).where(User.telegram_id == telegram_id).with_for_update()
        )
        if user is None:
            try:
                async with self.session.begin_nested():
                    self.session.add(User(telegram_id=telegram_id, username=username))
                    await self.session.flush()
            except IntegrityError:
                pass
            user = await self.session.scalar(
                select(User).where(User.telegram_id == telegram_id).with_for_update()
            )
            if user is None:
                raise RuntimeError("Could not lock Telegram user record")
        user.username = username
        await self.session.flush()

    async def count_open_orders(self, telegram_id: int) -> int:
        statement = select(func.count(Order.id)).where(
            Order.buyer_telegram_id == telegram_id,
            Order.status.in_(OPEN_ORDER_STATUSES),
        )
        return int(await self.session.scalar(statement) or 0)

    async def daily_stars(self, telegram_id: int) -> int:
        since = datetime.now(UTC) - timedelta(hours=24)
        ignored = (
            OrderStatus.CANCELLED,
            OrderStatus.PAYMENT_EXPIRED,
            OrderStatus.PAYMENT_FAILED,
        )
        statement = select(func.coalesce(func.sum(Order.stars), 0)).where(
            Order.buyer_telegram_id == telegram_id,
            Order.created_at >= since,
            Order.status.not_in(ignored),
        )
        return int(await self.session.scalar(statement) or 0)

    async def create(
        self,
        *,
        idempotency_key: str,
        buyer_telegram_id: int,
        buyer_username: str | None,
        kind: OrderKind,
        recipient_telegram_id: int | None,
        recipient_username: str | None,
        priced_option: PricedStarsOption,
    ) -> Order:
        existing = await self.get_by_idempotency_key(idempotency_key)
        if existing:
            return existing

        order = Order(
            order_number=self._new_order_number(),
            idempotency_key=idempotency_key,
            buyer_telegram_id=buyer_telegram_id,
            buyer_username=buyer_username,
            kind=kind,
            recipient_telegram_id=recipient_telegram_id,
            recipient_username=recipient_username,
            stars=priced_option.option.stars,
            telegram_currency=priced_option.option.currency,
            telegram_amount_minor=priced_option.option.amount_minor,
            usd_rub_rate=priced_option.usd_rub_rate,
            markup_percent=priced_option.markup_percent,
            rub_amount=priced_option.rub_amount,
            quote_unit_price=priced_option.unit_price,
            quote_currency=priced_option.unit_currency,
            quote_commission_percent=priced_option.provider_commission_percent,
            quote_expires_at=priced_option.quote_expires_at,
            status=OrderStatus.CREATED,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(order)
                await self.session.flush()
                await self.enqueue_status_notifications(order)
        except IntegrityError:
            existing = await self.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing
            raise
        return order

    async def set_customer_message(self, order: Order, *, chat_id: int, message_id: int) -> None:
        order.customer_chat_id = chat_id
        order.customer_message_id = message_id
        await self.session.flush()

    async def setup_test_payment(self, order: Order, payment_id: str) -> None:
        if order.status != OrderStatus.CREATED:
            return
        order.payment_id = payment_id
        order.payment_status = PaymentStatus.CREATED
        order.customer_payment_type = CustomerPaymentType.TEST
        await self.ensure_payment(
            order,
            provider="test",
            provider_reference=payment_id,
            expected_amount=order.rub_amount,
            currency="RUB",
            network="test",
        )
        await self.transition(order, OrderStatus.WAITING_FOR_PAYMENT)

    async def setup_ton_payment(
        self,
        order: Order,
        *,
        ton_amount: Decimal,
        rub_amount: Decimal,
        payment_comment: str,
        destination: str,
        expires_at: datetime,
    ) -> None:
        if order.status != OrderStatus.CREATED:
            return
        order.ton_amount = ton_amount
        order.rub_amount = rub_amount
        order.payment_comment = payment_comment
        order.payment_destination = destination
        order.payment_network = "TON"
        order.payment_id = payment_comment
        order.payment_status = PaymentStatus.CREATED
        order.customer_payment_type = CustomerPaymentType.TON
        order.expires_at = expires_at
        await self.ensure_payment(
            order,
            provider="toncenter",
            provider_reference=payment_comment,
            expected_amount=ton_amount,
            currency="TON",
            network="TON",
            destination=destination,
        )
        await self.transition(order, OrderStatus.WAITING_FOR_PAYMENT)

    async def setup_yookassa_payment(
        self,
        order: Order,
        *,
        idempotency_key: str,
        expires_at: datetime,
    ) -> Payment:
        if order.status not in {OrderStatus.CREATED, OrderStatus.WAITING_FOR_PAYMENT}:
            raise ValueError("Order cannot start a YooKassa payment in its current state")
        order.payment_status = PaymentStatus.CREATED
        order.customer_payment_type = CustomerPaymentType.YOOKASSA
        order.expires_at = expires_at
        payment = await self.ensure_payment(
            order,
            provider="yookassa",
            provider_reference=f"pending:{order.id}",
            expected_amount=order.rub_amount,
            currency="RUB",
            network="yookassa",
            idempotency_key=idempotency_key,
        )
        if order.status == OrderStatus.CREATED:
            await self.transition(order, OrderStatus.WAITING_FOR_PAYMENT)
        return payment

    async def ensure_payment(
        self,
        order: Order,
        *,
        provider: str,
        provider_reference: str,
        expected_amount: Decimal,
        currency: str,
        network: str,
        destination: str | None = None,
        idempotency_key: str | None = None,
    ) -> Payment:
        payment = await self.session.scalar(select(Payment).where(Payment.order_id == order.id))
        if payment is not None:
            return payment
        payment = Payment(
            order_id=order.id,
            provider=provider,
            provider_reference=provider_reference,
            destination=destination,
            idempotency_key=idempotency_key,
            expected_amount=expected_amount,
            currency=currency,
            network=network,
            status=PaymentStatus.CREATED,
        )
        self.session.add(payment)
        await self.session.flush()
        return payment

    async def update_yookassa_payment(
        self,
        order: Order,
        *,
        payment_id: str,
        provider_status: str,
        confirmation_url: str | None,
        paid: bool,
        refundable: bool,
        webhook_event: str | None = None,
    ) -> Payment:
        payment = await self.get_payment(order.id, for_update=True)
        if payment is None or payment.provider != "yookassa":
            raise RuntimeError("YooKassa payment record is missing")
        if not payment.provider_reference.startswith("pending:") and (
            payment.provider_reference != payment_id
        ):
            raise RuntimeError("YooKassa payment ID does not match the stored payment")
        payment.provider_reference = payment_id
        payment.provider_status = provider_status
        payment.confirmation_url = confirmation_url or payment.confirmation_url
        payment.paid = paid
        payment.refundable = refundable
        payment.last_provider_sync_at = datetime.now(UTC)
        if webhook_event:
            payment.last_webhook_event = webhook_event[:64]
            payment.last_webhook_at = datetime.now(UTC)
        order.payment_id = payment_id
        await self.session.flush()
        return payment

    async def get_payment(self, order_id: int, *, for_update: bool = False) -> Payment | None:
        statement = select(Payment).where(Payment.order_id == order_id)
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_payment_by_refund_id(
        self, refund_id: str, *, for_update: bool = False
    ) -> Payment | None:
        statement = select(Payment).where(Payment.refund_id == refund_id)
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def record_payment_detection(
        self,
        order: Order,
        *,
        transaction_hash: str,
        received_amount: Decimal,
        detected_at: datetime,
    ) -> bool:
        used = await self.session.scalar(
            select(Payment).where(
                Payment.transaction_hash == transaction_hash,
                Payment.order_id != order.id,
            )
        )
        if used is not None:
            return False
        payment = await self.get_payment(order.id, for_update=True)
        if payment is None:
            raise RuntimeError("Payment record is missing")
        try:
            async with self.session.begin_nested():
                payment.transaction_hash = transaction_hash
                payment.received_amount = received_amount
                payment.detected_at = detected_at
                payment.status = PaymentStatus.DETECTED
                order.ton_tx_hash = transaction_hash
                order.payment_status = PaymentStatus.DETECTED
                await self.session.flush()
        except IntegrityError:
            await self.session.refresh(order)
            await self.session.refresh(payment)
            return False
        return True

    async def set_payment_status(self, order: Order, status: PaymentStatus) -> None:
        order.payment_status = status
        payment = await self.get_payment(order.id, for_update=True)
        if payment is not None:
            payment.status = status
            if status == PaymentStatus.SUCCEEDED:
                payment.confirmed_at = datetime.now(UTC)
        await self.session.flush()

    async def transition(
        self,
        order: Order,
        target: OrderStatus,
        *,
        error_code: str | None = None,
        reason: str | None = None,
    ) -> None:
        if order.status == target:
            return
        ensure_order_transition(order.status, target)
        previous = order.status
        order.status = target
        order.status_changed_at = datetime.now(UTC)
        if error_code is not None:
            order.error_code = error_code[:64]
        if reason is not None:
            order.failure_reason = reason[:500]
        await self.session.flush()
        await self.audit(
            "order_status_changed",
            order_id=order.id,
            details={"from": previous.value, "to": target.value},
            previous_state={"status": previous.value},
            new_state={"status": target.value},
        )
        await self.enqueue_status_notifications(order)

    async def create_purchase_attempt(
        self, order: Order, provider: str
    ) -> tuple[PurchaseAttempt, bool]:
        key = f"purchase:{order.id}"
        existing = await self.session.scalar(
            select(PurchaseAttempt).where(PurchaseAttempt.idempotency_key == key).with_for_update()
        )
        if existing is not None:
            return existing, False
        attempt = PurchaseAttempt(
            order_id=order.id,
            idempotency_key=key,
            provider=provider,
            status=PurchaseAttemptStatus.SUBMITTING,
            started_at=datetime.now(UTC),
        )
        self.session.add(attempt)
        await self.session.flush()
        return attempt, True

    async def get_purchase_attempt(
        self, order_id: int, *, for_update: bool = False
    ) -> PurchaseAttempt | None:
        statement = select(PurchaseAttempt).where(PurchaseAttempt.order_id == order_id)
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def mark_purchase_queued(
        self, order: Order, attempt: PurchaseAttempt, request_id: str
    ) -> None:
        attempt.provider_request_id = request_id
        attempt.status = PurchaseAttemptStatus.QUEUED
        order.fragment_request_id = request_id
        await self.transition(order, OrderStatus.STARS_SENDING)

    async def mark_purchase_processing(self, attempt: PurchaseAttempt) -> None:
        attempt.status = PurchaseAttemptStatus.PROCESSING
        await self.session.flush()

    async def mark_purchase_succeeded(
        self,
        order: Order,
        attempt: PurchaseAttempt,
        transaction_id: str | None,
    ) -> bool:
        if transaction_id:
            used = await self.session.scalar(
                select(PurchaseAttempt).where(
                    PurchaseAttempt.transaction_id == transaction_id,
                    PurchaseAttempt.id != attempt.id,
                )
            )
            if used is not None:
                await self.mark_purchase_failed(
                    order,
                    attempt,
                    error_code="DUPLICATE_PROVIDER_TRANSACTION",
                    uncertain=True,
                )
                return False
        try:
            async with self.session.begin_nested():
                attempt.status = PurchaseAttemptStatus.SUCCEEDED
                attempt.transaction_id = transaction_id
                attempt.completed_at = datetime.now(UTC)
                order.telegram_transaction_id = transaction_id
                await self.transition(order, OrderStatus.COMPLETED)
        except IntegrityError:
            await self.session.refresh(order)
            await self.session.refresh(attempt)
            await self.mark_purchase_failed(
                order,
                attempt,
                error_code="DUPLICATE_PROVIDER_TRANSACTION",
                uncertain=True,
            )
            return False
        return True

    async def mark_purchase_failed(
        self,
        order: Order,
        attempt: PurchaseAttempt,
        *,
        error_code: str,
        uncertain: bool,
    ) -> None:
        attempt.status = (
            PurchaseAttemptStatus.UNCERTAIN if uncertain else PurchaseAttemptStatus.FAILED
        )
        attempt.error_code = error_code[:64]
        attempt.completed_at = datetime.now(UTC)
        target = OrderStatus.MANUAL_REVIEW if uncertain else OrderStatus.REFUND_REQUIRED
        await self.transition(
            order,
            target,
            error_code=error_code,
            reason="Payment is preserved; administrator review is required",
        )

    async def mark_waiting_for_merchant_balance(
        self,
        order: Order,
        attempt: PurchaseAttempt,
        *,
        error_code: str,
    ) -> None:
        attempt.status = PurchaseAttemptStatus.FAILED
        attempt.error_code = error_code[:64]
        attempt.completed_at = datetime.now(UTC)
        await self.transition(
            order,
            OrderStatus.WAITING_FOR_MERCHANT_BALANCE,
            error_code=error_code,
            reason="Payment is preserved; merchant operational balance must be replenished",
        )

    async def audit(
        self,
        event: str,
        *,
        order_id: int | None = None,
        actor_telegram_id: int | None = None,
        details: dict[str, Any] | None = None,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
    ) -> None:
        safe_details = {
            str(key)[:64]: value
            for key, value in (details or {}).items()
            if not any(part in str(key).lower() for part in SENSITIVE_DETAIL_KEYS)
            and isinstance(value, (str, int, float, bool, type(None)))
        }
        self.session.add(
            AuditEvent(
                order_id=order_id,
                actor_telegram_id=actor_telegram_id,
                entity_type="order" if order_id is not None else "system",
                entity_id=str(order_id) if order_id is not None else None,
                correlation_id=str(uuid.uuid4()),
                previous_state=self._safe_audit_state(previous_state),
                new_state=self._safe_audit_state(new_state),
                event=event[:64],
                details=safe_details,
            )
        )
        await self.session.flush()

    @staticmethod
    def _safe_audit_state(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            str(key)[:64]: item
            for key, item in value.items()
            if not any(part in str(key).lower() for part in SENSITIVE_DETAIL_KEYS)
            and isinstance(item, (str, int, float, bool, type(None)))
        }

    async def get_runtime_bool(self, key: str, default: bool) -> bool:
        setting = await self.session.get(RuntimeSetting, key)
        if setting is None:
            return default
        return setting.value.strip().lower() in {"1", "true", "yes", "on"}

    async def set_runtime_bool(self, key: str, value: bool, actor_telegram_id: int) -> None:
        setting = await self.session.get(RuntimeSetting, key)
        serialized = "true" if value else "false"
        previous_value = setting.value if setting is not None else None
        if setting is None:
            self.session.add(
                RuntimeSetting(key=key, value=serialized, updated_by=actor_telegram_id)
            )
        else:
            setting.value = serialized
            setting.updated_by = actor_telegram_id
        await self.audit(
            "admin_runtime_setting_changed",
            actor_telegram_id=actor_telegram_id,
            details={"key": key, "value": serialized},
            previous_state={"value": previous_value},
            new_state={"value": serialized},
        )
        await self.audit(
            "admin_action",
            actor_telegram_id=actor_telegram_id,
            details={"action": f"set_{key}", "value": serialized},
        )

    async def set_system_runtime_value(self, key: str, value: str) -> None:
        setting = await self.session.get(RuntimeSetting, key)
        if setting is None:
            self.session.add(RuntimeSetting(key=key, value=value[:256], updated_by=None))
        else:
            setting.value = value[:256]
            setting.updated_by = None
        await self.session.flush()

    async def enqueue_status_notifications(self, order: Order) -> None:
        if order.status in USER_NOTIFICATION_STATUSES:
            self.session.add(
                OrderNotification(
                    order_id=order.id,
                    status=order.status.value,
                    audience="user",
                )
            )
        if order.status in ADMIN_NOTIFICATION_STATUSES or (
            order.status == OrderStatus.COMPLETED and order.stars >= 1000
        ):
            self.session.add(
                OrderNotification(
                    order_id=order.id,
                    status=order.status.value,
                    audience="admin",
                )
            )
        await self.session.flush()

    async def enqueue_user_notification(self, order: Order) -> None:
        self.session.add(
            OrderNotification(
                order_id=order.id,
                status=order.status.value,
                audience="user",
            )
        )
        await self.session.flush()

    async def claim_notifications(
        self,
        *,
        claim_token: str,
        limit: int = 100,
    ) -> list[ClaimedNotification]:
        now = datetime.now(UTC)
        stale = now - timedelta(minutes=5)
        statement = (
            select(OrderNotification, Order)
            .join(Order, Order.id == OrderNotification.order_id)
            .where(
                OrderNotification.delivered_at.is_(None),
                OrderNotification.abandoned_at.is_(None),
                OrderNotification.next_attempt_at <= now,
                or_(
                    OrderNotification.claim_token.is_(None),
                    OrderNotification.claimed_at < stale,
                ),
            )
            .order_by(OrderNotification.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = (await self.session.execute(statement)).all()
        claimed: list[ClaimedNotification] = []
        for notification, order in rows:
            notification.claim_token = claim_token
            notification.claimed_at = now
            claimed.append(
                ClaimedNotification(
                    notification_id=notification.id,
                    order_id=notification.order_id,
                    buyer_telegram_id=order.buyer_telegram_id,
                    status=OrderStatus(notification.status),
                    audience=notification.audience,
                    order_number=order.order_number,
                    stars=order.stars,
                    recipient_username=order.recipient_username,
                    customer_chat_id=order.customer_chat_id,
                    customer_message_id=order.customer_message_id,
                    error_code=order.error_code,
                )
            )
        await self.session.flush()
        return claimed

    async def finish_notification(
        self,
        notification_id: int,
        *,
        claim_token: str,
        succeeded: bool,
        error_code: str | None = None,
        retry_limit: int = 10,
        max_backoff_seconds: int = 300,
    ) -> None:
        notification = await self.session.scalar(
            select(OrderNotification)
            .where(
                OrderNotification.id == notification_id,
                OrderNotification.claim_token == claim_token,
            )
            .with_for_update()
        )
        if notification is None:
            return
        now = datetime.now(UTC)
        notification.claim_token = None
        notification.claimed_at = None
        if succeeded:
            notification.delivered_at = now
            notification.last_error_code = None
        else:
            notification.attempts += 1
            notification.last_error_code = (error_code or "DELIVERY_FAILED")[:64]
            if notification.attempts >= retry_limit:
                notification.abandoned_at = now
            else:
                delay = min(2**notification.attempts, max_backoff_seconds)
                notification.next_attempt_at = now + timedelta(seconds=delay)
        await self.session.flush()

    async def stats(self) -> dict[str, int]:
        rows = await self.session.execute(
            select(Order.status, func.count(Order.id)).group_by(Order.status)
        )
        values = {status.value: int(count) for status, count in rows}
        values["notifications_pending"] = int(
            await self.session.scalar(
                select(func.count(OrderNotification.id)).where(
                    OrderNotification.delivered_at.is_(None),
                    OrderNotification.abandoned_at.is_(None),
                )
            )
            or 0
        )
        values["purchase_attempts_attention"] = int(
            await self.session.scalar(
                select(func.count(PurchaseAttempt.id)).where(
                    PurchaseAttempt.status.in_(
                        {PurchaseAttemptStatus.FAILED, PurchaseAttemptStatus.UNCERTAIN}
                    )
                )
            )
            or 0
        )
        return values

    @staticmethod
    def _new_order_number() -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        suffix = "".join(secrets.choice(alphabet) for _ in range(8))
        return f"TS-{datetime.now(UTC):%Y%m%d}-{suffix}"
