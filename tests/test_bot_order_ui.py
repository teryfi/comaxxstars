from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest

from app.bot.handlers.orders import (
    _build_preview,
    _format_ton_payment,
    _safe_edit_text,
    _show_options,
)
from app.bot.states.orders import OrderFlow
from app.domain import OrderKind, PricedStarsOption, StarsOption
from app.services.orders import TonPaymentInstructions


async def test_amount_can_be_typed_immediately_on_options_screen() -> None:
    callback = MagicMock()
    callback.from_user.id = 123
    callback.answer = AsyncMock()
    state = MagicMock()
    state.get_data = AsyncMock(return_value={})
    state.set_state = AsyncMock()
    container = MagicMock()
    container.settings.price_quote_limit = 20
    container.abuse_guard.allow = AsyncMock(return_value=True)
    bot_message = MagicMock()
    bot_message.edit_text = AsyncMock()

    with (
        patch(
            "app.bot.handlers.orders._get_priced_options",
            new=AsyncMock(return_value=[]),
        ),
        patch("app.bot.handlers.orders._callback_message", return_value=bot_message),
    ):
        await _show_options(callback, state, container, kind=OrderKind.SELF)

    state.set_state.assert_awaited_once_with(OrderFlow.waiting_for_self_amount)
    shown_text = bot_message.edit_text.await_args.args[0]
    assert "просто отправьте нужное количество числом" in shown_text


async def test_repeated_button_edit_is_treated_as_success() -> None:
    bot_message = MagicMock()
    bot_message.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(
            method=MagicMock(),
            message="Bad Request: message is not modified",
        )
    )

    await _safe_edit_text(bot_message, "Та же цена", parse_mode="HTML")

    bot_message.edit_text.assert_awaited_once()


async def test_preview_hides_internal_commissions_and_uses_html_emphasis() -> None:
    state = MagicMock()
    state.get_data = AsyncMock(return_value={})
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    container = MagicMock()
    container.settings.min_stars = 50
    container.settings.max_stars = 100_000
    container.is_fragment_mode = True
    container.pricing_service.quote_option = AsyncMock(
        return_value=PricedStarsOption(
            option=StarsOption(stars=137, currency="USDT", amount_minor=0),
            usd_rub_rate=Decimal("80"),
            markup_percent=Decimal("25.35"),
            rub_amount=Decimal("209"),
            unit_price=Decimal("0.015"),
            unit_currency="USDT",
            provider_commission_percent=Decimal("0.25"),
            quote_expires_at=datetime.now(UTC) + timedelta(minutes=2),
        )
    )

    text, _ = await _build_preview(
        state,
        container,
        user_id=123,
        buyer_username="buyer_name",
        kind=OrderKind.SELF,
        stars=137,
    )

    assert "<b>137 ⭐</b>" in text
    assert "<b>209 ₽</b>" in text
    assert "Fragment" not in text
    assert "Комиссия" not in text
    assert "Наценка" not in text


def test_created_order_number_and_payment_details_are_tap_to_copy() -> None:
    text = _format_ton_payment(
        TonPaymentInstructions(
            order_id=1,
            order_number="TS-20260811-ABC123",
            ton_amount="1.250000000",
            wallet_address="wallet-address",
            payment_comment="TS-ABC123",
            rub_amount="153",
            expires_at=datetime(2026, 8, 11, 12, 30, tzinfo=UTC),
        ),
        100,
        recipient="buyer_name",
    )

    assert "<code>TS-20260811-ABC123</code>" in text
    assert "<code>wallet-address</code>" in text
    assert "<code>TS-ABC123</code>" in text
    assert "<b>153 ₽</b>" in text
