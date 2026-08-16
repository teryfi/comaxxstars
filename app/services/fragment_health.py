import logging

import aiohttp

from app.config import Settings

logger = logging.getLogger(__name__)


async def check_fragment_api(settings: Settings) -> bool:
    if settings.stars_purchase_provider != "fragment":
        return True

    url = f"{settings.fragment_api_base_url}/health"
    timeout = aiohttp.ClientTimeout(total=15, connect=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status >= 400:
                    raise aiohttp.ClientResponseError(
                        response.request_info,
                        response.history,
                        status=response.status,
                    )
    except (aiohttp.ClientError, TimeoutError):
        logger.warning(
            "Fragment API startup health check failed; recovery workers will keep retrying",
            extra={"event": "fragment_startup_health_failed"},
        )
        return False

    logger.info("Fragment API is reachable", extra={"event": "fragment_startup_health_ok"})
    return True
