from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings
from app.domain import StarsOption


@dataclass(frozen=True)
class ResolvedTelegramUser:
    telegram_id: int
    username: str | None
    input_user: Any


class TelegramUserClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any | None = None

    async def connect(self) -> None:
        if self._client is not None and self._client.is_connected():
            return
        self._ensure_credentials()
        from telethon import TelegramClient

        session_path = self._prepare_session_path(self.settings.telegram_session_path)
        self._client = TelegramClient(
            str(session_path),
            self.settings.telegram_api_id,
            self.settings.secret_value(self.settings.telegram_api_hash),
        )
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError(
                "Telegram user session is not authorized. Run scripts/create_telegram_session.py first."
            )

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()

    async def get_me(self) -> Any:
        client = await self._get_client()
        return await client.get_me()

    async def resolve_username(self, username: str) -> ResolvedTelegramUser:
        client = await self._get_client()
        clean_username = username.strip().lstrip("@")
        if not clean_username:
            raise ValueError("Username is empty")
        entity = await client.get_entity(clean_username)
        input_user = await client.get_input_entity(entity)
        return ResolvedTelegramUser(
            telegram_id=entity.id,
            username=getattr(entity, "username", clean_username),
            input_user=input_user,
        )

    async def get_stars_balance(self) -> Any:
        client = await self._get_client()
        from telethon.tl.functions.payments import GetStarsStatusRequest
        from telethon.tl.types import InputPeerSelf

        return await client(GetStarsStatusRequest(peer=InputPeerSelf()))

    async def get_star_transactions(self, *, limit: int = 20, offset: str = "") -> Any:
        client = await self._get_client()
        from telethon.tl.functions.payments import GetStarsTransactionsRequest
        from telethon.tl.types import InputPeerSelf

        return await client(
            GetStarsTransactionsRequest(peer=InputPeerSelf(), offset=offset, limit=limit)
        )

    async def get_stars_topup_options(self) -> list[StarsOption]:
        client = await self._get_client()
        from telethon.tl.functions.payments import GetStarsTopupOptionsRequest

        raw_options = await client(GetStarsTopupOptionsRequest())
        return self._map_options(raw_options)

    async def get_stars_gift_options(self, user: ResolvedTelegramUser) -> list[StarsOption]:
        client = await self._get_client()
        from telethon.tl.functions.payments import GetStarsGiftOptionsRequest

        raw_options = await client(GetStarsGiftOptionsRequest(user_id=user.input_user))
        return self._map_options(raw_options)

    async def create_stars_payment_form(
        self, option: StarsOption, user: ResolvedTelegramUser | None
    ) -> Any:
        client = await self._get_client()
        from telethon.tl.functions.payments import GetPaymentFormRequest
        from telethon.tl.types import (
            InputInvoiceStars,
            InputStorePaymentStarsGift,
            InputStorePaymentStarsTopup,
        )

        if user is None:
            purpose = InputStorePaymentStarsTopup(
                stars=option.stars,
                currency=option.currency,
                amount=option.amount_minor,
            )
        else:
            purpose = InputStorePaymentStarsGift(
                user_id=user.input_user,
                stars=option.stars,
                currency=option.currency,
                amount=option.amount_minor,
            )
        invoice = InputInvoiceStars(purpose=purpose)
        return await client(GetPaymentFormRequest(invoice=invoice))

    async def submit_stars_payment(
        self, form: Any, option: StarsOption, user: ResolvedTelegramUser | None
    ) -> Any:
        client = await self._get_client()
        from telethon.tl.functions.payments import SendStarsFormRequest

        invoice = await self._build_invoice(option, user)
        return await client(SendStarsFormRequest(form_id=form.form_id, invoice=invoice))

    async def purchase_stars_for_self(self, option: StarsOption) -> Any:
        form = await self.create_stars_payment_form(option, user=None)
        return await self.submit_stars_payment(form, option, user=None)

    async def purchase_stars_as_gift(self, option: StarsOption, user: ResolvedTelegramUser) -> Any:
        form = await self.create_stars_payment_form(option, user=user)
        return await self.submit_stars_payment(form, option, user=user)

    async def _build_invoice(self, option: StarsOption, user: ResolvedTelegramUser | None) -> Any:
        from telethon.tl.types import (
            InputInvoiceStars,
            InputStorePaymentStarsGift,
            InputStorePaymentStarsTopup,
        )

        if user is None:
            purpose = InputStorePaymentStarsTopup(
                stars=option.stars,
                currency=option.currency,
                amount=option.amount_minor,
            )
        else:
            purpose = InputStorePaymentStarsGift(
                user_id=user.input_user,
                stars=option.stars,
                currency=option.currency,
                amount=option.amount_minor,
            )
        return InputInvoiceStars(purpose=purpose)

    @staticmethod
    def _map_options(raw_options: Any) -> list[StarsOption]:
        options = getattr(raw_options, "options", raw_options)
        return [
            StarsOption(
                stars=int(option.stars),
                currency=str(option.currency),
                amount_minor=int(option.amount),
                store_product=getattr(option, "store_product", None),
            )
            for option in options
        ]

    def _ensure_credentials(self) -> None:
        if not self.settings.telegram_api_id or not self.settings.telegram_api_hash:
            raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")

    async def _get_client(self) -> Any:
        await self.connect()
        if self._client is None:
            raise RuntimeError("Telegram client did not initialize")
        return self._client

    @staticmethod
    def _prepare_session_path(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
