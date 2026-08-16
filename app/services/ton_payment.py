import logging
import secrets
import string
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TonPaymentObservation:
    transaction_hash: str
    amount: Decimal
    timestamp: datetime
    destination: str
    currency: str
    network: str


class TonPaymentService:
    def __init__(
        self,
        *,
        wallet_address: str,
        toncenter_api_key: str | None = None,
        scan_limit: int = 100,
    ) -> None:
        self.wallet_address = wallet_address.strip()
        self.toncenter_api_key = toncenter_api_key
        self.scan_limit = scan_limit
        self._rate_cache: dict[str, Any] = {"value": None, "timestamp": 0.0}

    def generate_payment_comment(self, order_id: int) -> str:
        suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))
        return f"TERSTARS-{order_id}-{suffix}"

    async def check_health(self) -> bool:
        params = {"api_key": self.toncenter_api_key} if self.toncenter_api_key else None
        timeout = aiohttp.ClientTimeout(total=8, connect=4)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    "https://toncenter.com/api/v2/getMasterchainInfo",
                    params=params,
                    headers={"User-Agent": "terstars-bot/1.0"},
                ) as response:
                    if response.status != 200:
                        return False
                    data = await response.json()
                    return bool(data.get("ok"))
        except (aiohttp.ClientError, TimeoutError, ValueError, TypeError):
            return False

    async def get_ton_usd_rate(self) -> Decimal:
        now = time.time()
        cached = self._rate_cache["value"]
        if cached is not None and now - float(self._rate_cache["timestamp"]) < 300:
            return Decimal(str(cached))

        sources = [
            ("Binance", "https://api.binance.com/api/v3/ticker/price", {"symbol": "TONUSDT"}),
            (
                "CoinGecko",
                "https://api.coingecko.com/api/v3/simple/price",
                {"ids": "the-open-network", "vs_currencies": "usd"},
            ),
        ]
        timeout = aiohttp.ClientTimeout(total=8, connect=4)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for name, url, params in sources:
                try:
                    async with session.get(url, params=params) as response:
                        if response.status != 200:
                            continue
                        data = await response.json()
                        rate = Decimal(
                            str(
                                data["price"]
                                if name == "Binance"
                                else data["the-open-network"]["usd"]
                            )
                        )
                        if Decimal("0.1") < rate < Decimal("100"):
                            self._rate_cache = {"value": rate, "timestamp": now}
                            return rate
                except (aiohttp.ClientError, TimeoutError, KeyError, ValueError, TypeError):
                    logger.warning(
                        "TON/USD source unavailable", extra={"event": "ton_rate_source_failed"}
                    )
        raise RuntimeError("Could not fetch TON/USD rate")

    async def usd_to_ton(self, usd_amount: Decimal) -> Decimal:
        rate = await self.get_ton_usd_rate()
        return (usd_amount / rate).quantize(Decimal("0.000000001"), rounding=ROUND_CEILING)

    async def find_incoming_payment(
        self,
        *,
        comment: str,
        since_timestamp: int,
    ) -> TonPaymentObservation | None:
        if not self.wallet_address:
            raise RuntimeError("TON wallet address is not configured")
        params: dict[str, Any] = {"address": self.wallet_address, "limit": self.scan_limit}
        if self.toncenter_api_key:
            params["api_key"] = self.toncenter_api_key

        timeout = aiohttp.ClientTimeout(total=12, connect=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://toncenter.com/api/v2/getTransactions",
                params=params,
                headers={"User-Agent": "terstars-bot/1.0"},
            ) as response:
                response.raise_for_status()
                data = await response.json()
        if not data.get("ok") or not isinstance(data.get("result"), list):
            raise RuntimeError("Toncenter returned an invalid transaction response")

        for tx in data["result"]:
            timestamp = int(tx.get("utime", 0))
            if timestamp < since_timestamp:
                continue
            incoming = tx.get("in_msg") or {}
            message = str(incoming.get("message") or "").strip()
            if message != comment:
                continue
            value = incoming.get("value")
            tx_hash = (tx.get("transaction_id") or {}).get("hash") or tx.get("hash")
            if value is None or not tx_hash:
                raise RuntimeError("Toncenter transaction is missing amount or hash")
            amount = Decimal(str(int(value))) / Decimal("1000000000")
            return TonPaymentObservation(
                transaction_hash=str(tx_hash),
                amount=amount,
                timestamp=datetime.fromtimestamp(timestamp, tz=UTC),
                destination=self.wallet_address,
                currency="TON",
                network="TON",
            )
        return None
