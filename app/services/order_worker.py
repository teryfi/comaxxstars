import asyncio
import logging
import uuid
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.orders import completed_order_keyboard
from app.bot.order_messages import format_status_message
from app.config import Settings
from app.database.repositories.orders import ClaimedNotification, OrderRepository
from app.domain import OrderStatus
from app.services.orders import OrderService

logger = logging.getLogger(__name__)


class OrderWorker:
    def __init__(
        self,
        settings: Settings,
        session_factory,
        order_service: OrderService,
        *,
        process_orders: bool,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.order_service = order_service
        self.process_orders = process_orders
        self.bot: Bot | None = None
        self._running = False
        self._stop_event: asyncio.Event | None = None
        self._claim_token = str(uuid.uuid4())

    def set_bot(self, bot: Bot) -> None:
        self.bot = bot

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event = asyncio.Event()
        logger.info("Order worker started", extra={"event": "order_worker_started"})
        try:
            while self._running:
                try:
                    await self.tick()
                except Exception:
                    logger.exception(
                        "Order worker tick failed",
                        extra={"event": "order_worker_tick_failed"},
                    )
                await self._sleep(self.settings.order_worker_interval_seconds)
        finally:
            self._running = False
            self._stop_event = None
            logger.info("Order worker stopped", extra={"event": "order_worker_stopped"})

    async def stop(self) -> None:
        self._running = False
        if self._stop_event:
            self._stop_event.set()

    async def tick(self) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                repo = OrderRepository(session)
                heartbeat = datetime.now(UTC).isoformat()
                if self.bot is not None:
                    await repo.set_system_runtime_value(
                        "notification_worker_heartbeat",
                        heartbeat,
                    )
                if self.process_orders:
                    await repo.set_system_runtime_value(
                        "purchase_worker_heartbeat",
                        heartbeat,
                    )
        await self._deliver_notifications()
        if self.process_orders:
            await self.order_service.process_recoverable_orders()
        await self._deliver_notifications()

    async def _deliver_notifications(self) -> None:
        if not self.bot:
            return
        async with self.session_factory() as session:
            async with session.begin():
                claimed = await OrderRepository(session).claim_notifications(
                    claim_token=self._claim_token,
                    limit=self.settings.worker_batch_size,
                )
        for notification in claimed:
            await self._deliver_notification(notification)

    async def _deliver_notification(self, notification: ClaimedNotification) -> None:
        succeeded = False
        error_code: str | None = None
        bot = self.bot
        if bot is None:
            return
        try:
            if notification.audience == "user":
                text = format_status_message(
                    order_number=notification.order_number,
                    stars=notification.stars,
                    recipient_username=notification.recipient_username,
                    status=notification.status,
                )
                reply_markup = self._user_status_keyboard(notification.status)
                if notification.customer_chat_id and notification.customer_message_id:
                    try:
                        await bot.edit_message_text(
                            text,
                            chat_id=notification.customer_chat_id,
                            message_id=notification.customer_message_id,
                            parse_mode="HTML",
                            reply_markup=reply_markup,
                        )
                    except TelegramBadRequest as exc:
                        if "message is not modified" not in str(exc).lower():
                            raise
                    # A stored customer message is the single source of truth.
                    # If Telegram temporarily rejects an edit, retry the notification
                    # instead of creating a second status message.
                else:
                    await bot.send_message(
                        notification.buyer_telegram_id,
                        text,
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                    )
                succeeded = True
            elif notification.audience == "admin":
                succeeded = await self._notify_admins(notification)
                if not succeeded:
                    error_code = "NO_ADMIN_NOTIFICATION_DELIVERED"
            else:
                error_code = "UNKNOWN_NOTIFICATION_AUDIENCE"
        except Exception as exc:
            error_code = exc.__class__.__name__
            logger.exception(
                "Order notification failed",
                extra={
                    "event": "order_notification_failed",
                    "order_id": notification.order_id,
                    "error_code": error_code,
                },
            )
        async with self.session_factory() as session:
            async with session.begin():
                await OrderRepository(session).finish_notification(
                    notification.notification_id,
                    claim_token=self._claim_token,
                    succeeded=succeeded,
                    error_code=error_code,
                    retry_limit=self.settings.notification_retry_limit,
                    max_backoff_seconds=self.settings.notification_max_backoff_seconds,
                )

    async def _notify_admins(self, notification: ClaimedNotification) -> bool:
        bot = self.bot
        if bot is None:
            return False
        delivered = False
        eligible = False
        for admin_id in self.settings.admin_ids:
            if admin_id == notification.buyer_telegram_id:
                continue
            eligible = True
            try:
                await bot.send_message(
                    admin_id,
                    self._admin_message(notification),
                )
                delivered = True
            except Exception:
                logger.exception(
                    "Admin notification failed",
                    extra={
                        "event": "admin_notification_failed",
                        "order_id": notification.order_id,
                    },
                )
        return delivered or not eligible

    @staticmethod
    def _admin_message(notification: ClaimedNotification) -> str:
        recipient = notification.recipient_username or "unknown"
        if notification.status == OrderStatus.WAITING_FOR_MERCHANT_BALANCE:
            return (
                "🚨 Недостаточно средств на рабочем балансе\n\n"
                f"Заказ: {notification.order_number}\n"
                f"Звёзды: {notification.stars}\n"
                f"Получатель: {recipient}\n"
                "Пополните баланс, затем повторите обработку из админ-панели."
            )
        if notification.status == OrderStatus.PAID:
            return (
                "✅ Оплата подтверждена\n\n"
                f"Заказ: {notification.order_number}\n"
                f"Звёзды: {notification.stars}\n"
                f"Получатель: {recipient}\n"
                "Заказ принят в обработку."
            )
        if notification.status == OrderStatus.COMPLETED:
            return (
                "✅ Крупный заказ выполнен\n\n"
                f"Заказ: {notification.order_number}\n"
                f"Звёзды: {notification.stars}\n"
                f"Получатель: {recipient}"
            )
        return (
            f"Заказ #{notification.order_number}: {notification.status.value}. "
            f"Ошибка: {notification.error_code or '-'}. "
            f"Проверьте заказ в админ-панели."
        )

    @staticmethod
    def _user_status_keyboard(status: OrderStatus) -> InlineKeyboardMarkup | None:
        if status != OrderStatus.COMPLETED:
            return None
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=button["text"],
                        callback_data=button["callback_data"],
                    )
                    for button in row
                ]
                for row in completed_order_keyboard()
            ]
        )

    async def _sleep(self, seconds: int) -> None:
        if not self._stop_event:
            await asyncio.sleep(seconds)
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except TimeoutError:
            pass
