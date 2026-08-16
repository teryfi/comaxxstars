from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import func, select, update

from app.database.models import OrderNotification, RuntimeSetting
from app.services.order_worker import OrderWorker
from tests.test_order_lifecycle import CountingProvider, _build, _create_paid


async def test_notification_outbox_retries_after_send_failure(tmp_path) -> None:
    service, session_factory = await _build(tmp_path, CountingProvider())
    await _create_paid(service, token="notification-retry")
    worker = OrderWorker(service.settings, session_factory, service, process_orders=False)

    failing_bot = MagicMock()
    failing_bot.send_message = AsyncMock(side_effect=TimeoutError("Telegram unavailable"))
    worker.set_bot(failing_bot)
    await worker.tick()

    async with session_factory() as session:
        pending = int(
            await session.scalar(
                select(func.count(OrderNotification.id)).where(
                    OrderNotification.delivered_at.is_(None),
                    OrderNotification.attempts == 1,
                )
            )
            or 0
        )
    assert pending == 2

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                update(OrderNotification)
                .where(OrderNotification.delivered_at.is_(None))
                .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
            )

    successful_bot = MagicMock()
    successful_bot.send_message = AsyncMock()
    worker.set_bot(successful_bot)
    await worker.tick()

    async with session_factory() as session:
        remaining = int(
            await session.scalar(
                select(func.count(OrderNotification.id)).where(
                    OrderNotification.delivered_at.is_(None)
                )
            )
            or 0
        )
    assert remaining == 0


async def test_all_in_one_worker_writes_bot_and_purchase_heartbeats(tmp_path) -> None:
    service, session_factory = await _build(tmp_path, CountingProvider())
    worker = OrderWorker(service.settings, session_factory, service, process_orders=True)
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.edit_message_text = AsyncMock()
    worker.set_bot(bot)

    await worker.tick()

    async with session_factory() as session:
        notification = await session.get(RuntimeSetting, "notification_worker_heartbeat")
        purchase = await session.get(RuntimeSetting, "purchase_worker_heartbeat")
    assert notification is not None
    assert purchase is not None


async def test_completed_user_notification_has_menu_button(tmp_path) -> None:
    provider = CountingProvider()
    service, session_factory = await _build(tmp_path, provider)
    order_id = await _create_paid(service, token="completed-menu-button")
    await service.set_customer_message(order_id, chat_id=10, message_id=20)
    await service.deliver_paid_order(order_id)

    worker = OrderWorker(service.settings, session_factory, service, process_orders=False)
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.edit_message_text = AsyncMock()
    worker.set_bot(bot)

    await worker.tick()

    assert bot.edit_message_text.await_count >= 1
    last_call = bot.edit_message_text.await_args
    reply_markup = last_call.kwargs["reply_markup"]
    button = reply_markup.inline_keyboard[0][0]
    assert button.text == "В меню"
    assert button.callback_data == "menu:main"
