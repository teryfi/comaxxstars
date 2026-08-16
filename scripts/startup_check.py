"""Validate configuration and dependencies without starting polling or a financial operation."""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.config import get_settings
from app.database.session import (
    create_local_schema,
    create_session_factory,
    dispose_session_factory,
)
from app.logging_config import configure_logging
from app.services.container import build_container


async def main() -> None:
    settings = get_settings()
    settings.validate_runtime()
    configure_logging(settings.log_level)
    await create_local_schema(settings.database_url)
    session_factory = create_session_factory(settings.database_url)
    container = build_container(settings, session_factory)

    async with session_factory() as session:
        await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=5)
    logging.getLogger(__name__).info(
        "Database startup check passed",
        extra={"event": "startup_database_ok"},
    )

    if container.fragment_client is not None:
        health = await container.fragment_client.check_health()
        if not health.ok:
            raise RuntimeError("Fragment API startup check failed")
        price = await container.fragment_client.get_stars_unit_price()
        logging.getLogger(__name__).info(
            "Fragment startup check passed",
            extra={
                "event": "startup_fragment_ok",
                "provider": "fragment",
                "status": price.currency,
            },
        )

    await container.telegram_client.disconnect()
    await dispose_session_factory(session_factory)


if __name__ == "__main__":
    asyncio.run(main())
