import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from app.bot.access_middleware import BlockedUserMiddleware
from app.bot.handlers.admin import router as admin_router
from app.bot.handlers.orders import router as orders_router
from app.config import get_settings
from app.database.process_lock import ProcessLock
from app.database.session import (
    create_local_schema,
    create_session_factory,
    dispose_session_factory,
)
from app.logging_config import configure_logging
from app.services.container import build_container
from app.services.fragment_health import check_fragment_api
from app.services.provider_health_monitor import ProviderHealthMonitor


async def main() -> None:
    settings = get_settings()
    settings.validate_runtime()
    if settings.process_role == "worker":
        raise RuntimeError("PROCESS_ROLE=worker must be started with python -m app.purchase_worker")
    configure_logging(
        settings.log_level,
        secrets=(
            settings.bot_token.get_secret_value(),
            settings.database_url,
            settings.secret_value(settings.telegram_api_hash) or "",
            settings.secret_value(settings.fragment_wallet_seed) or "",
            settings.secret_value(settings.fragment_cookies) or "",
            settings.secret_value(settings.fragment_local_storage) or "",
            settings.secret_value(settings.toncenter_api_key) or "",
            settings.secret_value(settings.yookassa_secret_key) or "",
        ),
    )
    await check_fragment_api(settings)

    await create_local_schema(settings.database_url)
    session_factory = create_session_factory(settings.database_url)
    polling_lock = await ProcessLock.acquire(session_factory, "telegram-bot-polling")
    container = build_container(settings, session_factory)

    bot = Bot(settings.bot_token.get_secret_value())
    await _setup_bot_profile(bot)
    dispatcher = Dispatcher()
    dispatcher["container"] = container
    dispatcher.update.outer_middleware(BlockedUserMiddleware(session_factory))
    dispatcher.include_router(admin_router)
    dispatcher.include_router(orders_router)

    logging.getLogger(__name__).info("Starting Telegram bot polling")
    background_tasks: list[asyncio.Task[None]] = []
    provider_health_monitor: ProviderHealthMonitor | None = None
    container.order_worker.set_bot(bot)
    background_tasks.append(asyncio.create_task(container.order_worker.start()))
    if container.payment_monitor:
        background_tasks.append(asyncio.create_task(container.payment_monitor.start()))
    if container.fragment_client is not None:
        provider_health_monitor = ProviderHealthMonitor(settings, bot, container.fragment_client)
        background_tasks.append(asyncio.create_task(provider_health_monitor.start()))
    try:
        await dispatcher.start_polling(bot)
    finally:
        await container.order_worker.stop()
        if container.payment_monitor:
            await container.payment_monitor.stop()
        if provider_health_monitor is not None:
            await provider_health_monitor.stop()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        await container.telegram_client.disconnect()
        await bot.session.close()
        await polling_lock.release()
        await dispose_session_factory(session_factory)


async def _setup_bot_profile(bot: Bot) -> None:
    try:
        await bot.set_my_short_description("Быстрая покупка Telegram Stars для себя и в подарок.")
        await bot.set_my_description(
            "Сервис покупки Telegram Stars. Выберите готовое количество или укажите своё, "
            "выберите получателя и оплатите заказ. Статус заказа обновляется в одном сообщении."
        )
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="orders", description="Мои последние заказы"),
                BotCommand(command="cancel", description="Отменить текущий ввод"),
                BotCommand(command="help", description="Как работает сервис"),
            ]
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Telegram bot profile setup failed",
            extra={"event": "bot_profile_setup_failed"},
        )


if __name__ == "__main__":
    asyncio.run(main())
