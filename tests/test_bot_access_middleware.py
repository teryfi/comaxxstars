from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.types import User

from app.bot.access_middleware import BlockedUserMiddleware
from app.database.models import BlockedTelegramUser
from app.database.session import create_local_schema, create_session_factory


async def test_blocked_user_cannot_reach_any_bot_handler(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'blocked.db').as_posix()}"
    await create_local_schema(database_url)
    session_factory = create_session_factory(database_url)
    async with session_factory() as session:
        async with session.begin():
            session.add(
                BlockedTelegramUser(
                    telegram_user_id=42,
                    reason="abuse",
                    created_by_admin_id=1,
                )
            )

    handler = AsyncMock(return_value="handled")
    message = MagicMock()
    message.answer = AsyncMock()
    event = MagicMock(message=message, callback_query=None)
    state = MagicMock(spec=FSMContext)
    state.clear = AsyncMock()
    data = {
        "event_from_user": User(id=42, is_bot=False, first_name="Blocked"),
        "state": state,
    }

    result = await BlockedUserMiddleware(session_factory)(handler, event, data)

    assert result is None
    handler.assert_not_awaited()
    state.clear.assert_awaited_once()
    message.answer.assert_awaited_once_with("⛔ <b>Доступ к боту ограничен.</b>", parse_mode="HTML")


async def test_active_user_reaches_bot_handler(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'active.db').as_posix()}"
    await create_local_schema(database_url)
    session_factory = create_session_factory(database_url)
    handler = AsyncMock(return_value="handled")
    data = {"event_from_user": User(id=43, is_bot=False, first_name="Active")}

    result = await BlockedUserMiddleware(session_factory)(handler, MagicMock(), data)

    assert result == "handled"
    handler.assert_awaited_once()
