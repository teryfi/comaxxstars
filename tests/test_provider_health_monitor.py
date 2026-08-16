from unittest.mock import AsyncMock, MagicMock

from app.services.fragment_client import FragmentHealthResult
from app.services.provider_health_monitor import ProviderHealthMonitor
from tests.factories import make_settings


async def test_provider_health_alerts_only_on_state_changes() -> None:
    settings = make_settings(ADMIN_IDS=[1, 2])
    bot = MagicMock()
    bot.send_message = AsyncMock()
    client = MagicMock()
    client.check_health = AsyncMock(
        side_effect=[
            FragmentHealthResult(ok=False, message="down"),
            FragmentHealthResult(ok=False, message="still down"),
            FragmentHealthResult(ok=True, message="up"),
        ]
    )
    monitor = ProviderHealthMonitor(settings, bot, client)

    await monitor.tick()
    await monitor.tick()
    await monitor.tick()

    assert bot.send_message.await_count == 4
