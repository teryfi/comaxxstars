from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import TelegramObject, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories.orders import OrderRepository


class BlockedUserMiddleware(BaseMiddleware):
    """Stop every bot update from an actively blocked Telegram user."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not isinstance(user, User) or not await self._is_blocked(user.id):
            return await handler(event, data)

        state = data.get("state")
        if isinstance(state, FSMContext):
            await state.clear()

        callback = getattr(event, "callback_query", None)
        if callback is not None:
            await callback.answer("Доступ к боту ограничен", show_alert=True)
            return None

        message = getattr(event, "message", None)
        if message is not None:
            await message.answer("⛔ <b>Доступ к боту ограничен.</b>", parse_mode="HTML")
        return None

    async def _is_blocked(self, telegram_id: int) -> bool:
        async with self.session_factory() as session:
            return await OrderRepository(session).is_user_blocked(telegram_id)
