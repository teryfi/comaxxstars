import asyncio
import logging
import secrets
import time
from decimal import Decimal
from xml.etree import ElementTree

import aiohttp

logger = logging.getLogger(__name__)


class ExchangeRateService:
    CBR_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"

    def __init__(self) -> None:
        self._cached_rate: Decimal | None = None
        self._cached_at = 0.0

    async def get_usd_rub_rate(self) -> Decimal:
        now = time.monotonic()
        if self._cached_rate is not None and now - self._cached_at < 3600:
            return self._cached_rate

        timeout = aiohttp.ClientTimeout(total=10, connect=4)
        for attempt in range(2):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(self.CBR_DAILY_URL) as response:
                        response.raise_for_status()
                        payload = await response.read()
                rate = self._parse_rate(payload)
                self._cached_rate = rate
                self._cached_at = now
                return rate
            except (aiohttp.ClientError, TimeoutError):
                logger.warning(
                    "USD/RUB source unavailable",
                    extra={"event": "usd_rub_rate_source_failed", "request_id": attempt + 1},
                )
                if attempt == 0:
                    delay = Decimal("0.25") + Decimal(secrets.randbelow(250)) / 1000
                    await asyncio.sleep(float(delay))
        raise RuntimeError("Could not fetch USD/RUB rate")

    @staticmethod
    def _parse_rate(payload: bytes) -> Decimal:
        root = ElementTree.fromstring(payload)
        for valute in root.findall("Valute"):
            if valute.findtext("CharCode") == "USD":
                value = valute.findtext("Value")
                nominal = valute.findtext("Nominal") or "1"
                if value:
                    rate = Decimal(value.replace(",", ".")) / Decimal(nominal)
                    if Decimal("10") < rate < Decimal("1000"):
                        return rate
                    raise RuntimeError("USD/RUB rate is outside the accepted safety range")
        raise RuntimeError("USD/RUB rate not found in CBR response")
