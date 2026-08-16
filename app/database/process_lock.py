from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker


@dataclass
class ProcessLock:
    connection: AsyncConnection | None
    lock_id: int | None

    @classmethod
    async def acquire(
        cls,
        session_factory: async_sessionmaker[AsyncSession],
        name: str,
    ) -> "ProcessLock":
        engine = session_factory.kw.get("bind")
        if engine is None or engine.url.get_backend_name() == "sqlite":
            return cls(connection=None, lock_id=None)
        lock_id = int.from_bytes(sha256(name.encode("utf-8")).digest()[:8], "big", signed=True)
        connection = await engine.connect()
        acquired = bool(
            await connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id}
            )
        )
        if not acquired:
            await connection.close()
            raise RuntimeError(f"Another {name} process is already running")
        return cls(connection=connection, lock_id=lock_id)

    async def release(self) -> None:
        if self.connection is None or self.lock_id is None:
            return
        try:
            await self.connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": self.lock_id}
            )
        finally:
            await self.connection.close()
            self.connection = None
