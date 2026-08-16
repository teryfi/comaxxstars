import asyncio
import logging

from aiogram import Bot

from app.config import Settings
from app.services.fragment_client import FragmentClient

logger = logging.getLogger(__name__)


class ProviderHealthMonitor:
    def __init__(self, settings: Settings, bot: Bot, fragment_client: FragmentClient) -> None:
        self.settings = settings
        self.bot = bot
        self.fragment_client = fragment_client
        self._last_ok: bool | None = None
        self._running = False
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event = asyncio.Event()
        try:
            while self._running:
                try:
                    await self.tick()
                except Exception:
                    logger.exception(
                        "Provider health monitor tick failed",
                        extra={"event": "provider_health_monitor_failed", "provider": "fragment"},
                    )
                await self._sleep(self.settings.provider_health_interval_seconds)
        finally:
            self._running = False
            self._stop_event = None

    async def stop(self) -> None:
        self._running = False
        if self._stop_event is not None:
            self._stop_event.set()

    async def tick(self) -> None:
        result = await self.fragment_client.check_health()
        if result.ok:
            if self._last_ok is False:
                await self._notify_admins("Fragment API снова доступен.")
                logger.info(
                    "Fragment API recovered",
                    extra={"event": "provider_recovered", "provider": "fragment"},
                )
        elif self._last_ok is not False:
            await self._notify_admins(
                "Fragment API недоступен. Новые котировки могут не работать; "
                "проверьте /health и зависшие заказы."
            )
            logger.error(
                "Fragment API is unavailable",
                extra={"event": "provider_unavailable", "provider": "fragment"},
            )
        self._last_ok = result.ok

    async def _notify_admins(self, message: str) -> None:
        for admin_id in self.settings.admin_ids:
            try:
                await self.bot.send_message(admin_id, message)
            except Exception:
                logger.exception(
                    "Provider alert delivery failed",
                    extra={
                        "event": "provider_alert_delivery_failed",
                        "provider": "fragment",
                        "user_id": admin_id,
                    },
                )

    async def _sleep(self, seconds: int) -> None:
        if self._stop_event is None:
            await asyncio.sleep(seconds)
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except TimeoutError:
            pass
