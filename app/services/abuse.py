import asyncio
import logging
import time
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class AbuseGuard:
    def __init__(self, *, default_limit: int, window_seconds: int) -> None:
        self.default_limit = default_limit
        self.window_seconds = window_seconds
        self._events: dict[tuple[int, str], deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, user_id: int, action: str, *, limit: int | None = None) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        key = (user_id, action)
        async with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            effective_limit = limit or self.default_limit
            if len(events) >= effective_limit:
                logger.warning(
                    "Application rate limit exceeded",
                    extra={
                        "event": "rate_limit_exceeded",
                        "user_id": user_id,
                        "request_id": action,
                    },
                )
                return False
            events.append(now)
            return True
