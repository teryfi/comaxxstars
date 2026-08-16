import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.database.models import Order
from app.database.repositories.orders import OrderRepository
from app.domain import CustomerPaymentType, OrderStatus
from app.services.orders import OrderService
from app.services.ton_payment import TonPaymentService

logger = logging.getLogger(__name__)


class PaymentMonitor:
    def __init__(
        self,
        settings: Settings,
        session_factory,
        order_service: OrderService,
        ton_service: TonPaymentService,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.order_service = order_service
        self.ton_service = ton_service
        self._running = False
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event = asyncio.Event()
        logger.info("Payment monitor started", extra={"event": "payment_monitor_started"})
        try:
            while self._running:
                try:
                    await self.tick()
                except Exception:
                    logger.exception(
                        "Payment monitor tick failed",
                        extra={"event": "payment_monitor_tick_failed"},
                    )
                await self._sleep(self.settings.payment_monitor_interval_seconds)
        finally:
            self._running = False
            self._stop_event = None
            logger.info("Payment monitor stopped", extra={"event": "payment_monitor_stopped"})

    async def stop(self) -> None:
        self._running = False
        if self._stop_event:
            self._stop_event.set()

    async def tick(self) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                await OrderRepository(session).set_system_runtime_value(
                    "payment_monitor_heartbeat", datetime.now(UTC).isoformat()
                )
        async with self.session_factory() as session:
            repo = OrderRepository(session)
            pending = await repo.list_by_status(
                OrderStatus.WAITING_FOR_PAYMENT,
                OrderStatus.PAYMENT_DETECTED,
                OrderStatus.PAYMENT_CONFIRMING,
                OrderStatus.PAYMENT_EXPIRED,
                OrderStatus.CANCELLED,
                limit=self.settings.payment_monitor_batch_size,
            )
            order_ids = [
                order.id
                for order in pending
                if order.customer_payment_type == CustomerPaymentType.TON
                and self._within_late_scan_window(order)
            ]

        for order_id in order_ids:
            try:
                await self._process_ton_order(order_id)
            except Exception:
                logger.exception(
                    "TON payment check failed",
                    extra={"event": "payment_provider_error", "order_id": order_id},
                )

    async def check_order_now(self, order_id: int) -> tuple[OrderStatus, str | None, str | None]:
        await self._process_ton_order(order_id)
        return await self.order_service.get_order_snapshot(order_id)

    async def _process_ton_order(self, order_id: int) -> None:
        async with self.session_factory() as session:
            repo = OrderRepository(session)
            order = await repo.get(order_id)
            if order is None or order.customer_payment_type != CustomerPaymentType.TON:
                return
            if order.status not in {
                OrderStatus.WAITING_FOR_PAYMENT,
                OrderStatus.PAYMENT_DETECTED,
                OrderStatus.PAYMENT_CONFIRMING,
                OrderStatus.PAYMENT_EXPIRED,
                OrderStatus.CANCELLED,
            }:
                return
            if not order.payment_comment:
                return
            created_at = order.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            since_timestamp = int(created_at.timestamp())
            comment = order.payment_comment

        observation = await self.ton_service.find_incoming_payment(
            comment=comment,
            since_timestamp=since_timestamp,
        )
        if observation is not None:
            await self.order_service.record_ton_observation(order_id, observation)
            return
        await self.order_service.expire_if_needed(order_id)

    def _within_late_scan_window(self, order: Order) -> bool:
        if (
            order.status not in {OrderStatus.PAYMENT_EXPIRED, OrderStatus.CANCELLED}
            or not order.expires_at
        ):
            return True
        expires_at = order.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at + timedelta(hours=self.settings.late_payment_scan_hours) > datetime.now(
            UTC
        )

    async def _sleep(self, seconds: int) -> None:
        if not self._stop_event:
            await asyncio.sleep(seconds)
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except TimeoutError:
            pass
