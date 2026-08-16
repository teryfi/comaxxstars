import asyncio
import logging

from app.config import get_settings
from app.database.session import (
    create_local_schema,
    create_session_factory,
    dispose_session_factory,
)
from app.logging_config import configure_logging
from app.services.container import build_container
from app.services.fragment_health import check_fragment_api


async def main() -> None:
    settings = get_settings()
    settings.validate_runtime()
    if settings.process_role not in {"all", "worker"}:
        raise RuntimeError("Purchase worker requires PROCESS_ROLE=worker or all")

    configure_logging(
        settings.log_level,
        secrets=(
            settings.database_url,
            settings.secret_value(settings.fragment_wallet_seed) or "",
            settings.secret_value(settings.fragment_cookies) or "",
            settings.secret_value(settings.fragment_local_storage) or "",
        ),
    )
    await check_fragment_api(settings)
    await create_local_schema(settings.database_url)
    session_factory = create_session_factory(settings.database_url)
    container = build_container(settings, session_factory)

    logging.getLogger(__name__).info(
        "Starting purchase worker",
        extra={"event": "purchase_worker_started"},
    )
    try:
        await container.order_worker.start()
    finally:
        await container.order_worker.stop()
        await container.telegram_client.disconnect()
        await dispose_session_factory(session_factory)


if __name__ == "__main__":
    asyncio.run(main())
