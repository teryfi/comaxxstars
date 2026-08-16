import logging
import re
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards.orders import (
    confirmation_keyboard,
    main_menu,
    options_keyboard,
    payment_keyboard,
)
from app.bot.order_messages import display_recipient, format_status_message, status_label
from app.bot.states.orders import OrderFlow
from app.domain import (
    OrderKind,
    OrderStatus,
    PricedStarsOption,
    StarsOption,
    parse_stars_amount,
    validate_stars_amount,
)
from app.services.container import Container
from app.services.orders import TonPaymentInstructions

router = Router()
logger = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, container: Container) -> None:
    await state.clear()
    maintenance, _ = await container.order_service.get_runtime_controls()
    if maintenance:
        await message.answer(
            "terstars временно на обслуживании. Уже оплаченные заказы продолжают обрабатываться."
        )
        return
    await message.answer("⭐ terstars\n\nВыберите действие:", reply_markup=_markup(main_menu()))


@router.message(Command("cancel"))
async def cancel_input(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Текущий ввод отменён. Выберите действие:", reply_markup=_markup(main_menu())
    )


@router.message(Command("help"))
async def help_message(message: Message) -> None:
    await message.answer(
        "TerStars помогает купить звёзды Telegram для себя или в подарок.\n\n"
        "1. Выберите получателя.\n"
        "2. Выберите количество звёзд.\n"
        "3. Проверьте цену и оплатите заказ.\n"
        "4. Статус обновится в сообщении заказа.\n\n"
        "Повторно не оплачивайте заказ, если платёж уже подтверждён."
    )


@router.message(Command("orders"))
async def my_orders(message: Message, container: Container) -> None:
    if message.from_user is None:
        return
    orders = await container.order_service.list_user_orders(message.from_user.id, limit=5)
    if not orders:
        await message.answer("У вас пока нет заказов.", reply_markup=_markup(main_menu()))
        return
    lines = [
        f"<code>{escape(order.order_number)}</code> · {order.stars} ⭐ · {status_label(order.status)}"
        for order in orders
    ]
    await message.answer("Ваши последние заказы:\n\n" + "\n".join(lines), parse_mode="HTML")


@router.message(Command("fragment_status"))
async def fragment_status(message: Message, container: Container) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, container):
        await message.answer("Команда недоступна.")
        return
    if not container.is_fragment_mode or not container.fragment_client:
        await message.answer("Провайдер покупки звёзд выключен.")
        return
    await container.order_service.audit_admin_action(
        "fragment_status",
        actor_telegram_id=message.from_user.id,
    )
    health = await container.fragment_client.check_health()
    if not health.ok:
        await message.answer("Сервис покупки звёзд сейчас недоступен.")
        return
    try:
        unit_price = await container.fragment_client.get_stars_unit_price()
        price_text = (
            f"Цена 1 Star: {unit_price.amount} {unit_price.currency}\n"
            f"Кэш API: {unit_price.cached_at or 'не указан'}"
        )
    except Exception:
        logger.exception(
            "Fragment price check failed",
            extra={
                "event": "fragment_price_check_failed",
                "user_id": message.from_user.id,
            },
        )
        price_text = "Цены: ошибка проверки"
    maintenance, purchases_enabled = await container.order_service.get_runtime_controls()
    await message.answer(
        "Проверка сервиса покупки звёзд\n\n"
        f"Режим: {container.settings.fragment_api_mode}\n"
        f"Метод: {container.settings.fragment_payment_method}\n"
        f"Реальные покупки: {container.settings.real_stars_purchase_enabled}\n"
        f"Режим обслуживания: {maintenance}\n"
        f"Покупки включены: {purchases_enabled}\n"
        f"{price_text}"
    )


@router.callback_query(F.data == "menu:main")
async def back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit_text(
        _callback_message(callback),
        "⭐ terstars\n\nВыберите действие:",
        reply_markup=_markup(main_menu()),
    )
    await callback.answer()


@router.callback_query(F.data == "self:start")
@router.callback_query(F.data == "self:refresh")
async def self_start(callback: CallbackQuery, state: FSMContext, container: Container) -> None:
    await _show_options(callback, state, container, kind=OrderKind.SELF)


@router.callback_query(F.data == "self:custom")
async def self_custom(callback: CallbackQuery, state: FSMContext, container: Container) -> None:
    await state.set_state(OrderFlow.waiting_for_self_amount)
    await callback.answer(
        f"Отправьте число от {container.settings.min_stars} до {container.settings.max_stars}"
    )


@router.message(OrderFlow.waiting_for_self_amount)
async def self_amount(message: Message, state: FSMContext, container: Container) -> None:
    stars = _parse_amount(message.text, container)
    if stars is None:
        await message.answer(_amount_error(container))
        return
    await _preview_message(message, state, container, kind=OrderKind.SELF, stars=stars)


@router.callback_query(F.data == "gift:start")
async def gift_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderFlow.waiting_for_gift_username)
    await _safe_edit_text(
        _callback_message(callback), "🎁 Отправьте username получателя без ссылки."
    )
    await callback.answer()


@router.message(OrderFlow.waiting_for_gift_username)
async def gift_username(message: Message, state: FSMContext, container: Container) -> None:
    if message.from_user is None:
        await message.answer("Не удалось определить отправителя сообщения.")
        return
    username = (message.text or "").strip().lstrip("@")
    if not USERNAME_RE.fullmatch(username):
        await message.answer("Имя пользователя должно содержать 5–32 латинских символа, цифры или знак _. ")
        return
    if not await container.abuse_guard.allow(message.from_user.id, "resolve_username"):
        await message.answer("Слишком много запросов. Попробуйте через минуту.")
        return
    try:
        resolved = await container.telegram_client.resolve_username(username)
    except Exception:
        logger.exception(
            "Gift recipient resolution failed",
            extra={
                "event": "recipient_resolution_failed",
                "user_id": message.from_user.id,
            },
        )
        await message.answer("Не удалось найти получателя. Проверьте username.")
        return
    await state.update_data(
        recipient_username=resolved.username or username,
        recipient_id=resolved.telegram_id,
    )
    await _show_options_message(message, state, container, kind=OrderKind.GIFT)


@router.callback_query(F.data == "gift:refresh")
async def gift_refresh(callback: CallbackQuery, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    if not data.get("recipient_id"):
        await state.set_state(OrderFlow.waiting_for_gift_username)
        await _safe_edit_text(_callback_message(callback), "Сначала отправьте username получателя.")
        await callback.answer()
        return
    await _show_options(callback, state, container, kind=OrderKind.GIFT)


@router.callback_query(F.data == "gift:custom")
async def gift_custom(callback: CallbackQuery, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    if not data.get("recipient_id"):
        await state.set_state(OrderFlow.waiting_for_gift_username)
        await _safe_edit_text(_callback_message(callback), "Сначала отправьте username получателя.")
    else:
        await state.set_state(OrderFlow.waiting_for_gift_amount)
        await callback.answer(
            f"Отправьте число от {container.settings.min_stars} до {container.settings.max_stars}"
        )
        return
    await callback.answer()


@router.message(OrderFlow.waiting_for_gift_amount)
async def gift_amount(message: Message, state: FSMContext, container: Container) -> None:
    stars = _parse_amount(message.text, container)
    if stars is None:
        await message.answer(_amount_error(container))
        return
    data = await state.get_data()
    if not data.get("recipient_id"):
        await state.set_state(OrderFlow.waiting_for_gift_username)
        await message.answer("Сначала отправьте username получателя.")
        return
    await _preview_message(message, state, container, kind=OrderKind.GIFT, stars=stars)


@router.callback_query(F.data.startswith("self:option:"))
async def self_option(callback: CallbackQuery, state: FSMContext, container: Container) -> None:
    stars = _callback_int(callback.data)
    if stars is None:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    await _preview_callback(callback, state, container, kind=OrderKind.SELF, stars=stars)


@router.callback_query(F.data.startswith("gift:option:"))
async def gift_option(callback: CallbackQuery, state: FSMContext, container: Container) -> None:
    stars = _callback_int(callback.data)
    data = await state.get_data()
    if stars is None or not data.get("recipient_id"):
        await callback.answer("Сначала выберите получателя", show_alert=True)
        return
    await _preview_callback(callback, state, container, kind=OrderKind.GIFT, stars=stars)


@router.callback_query(F.data.startswith("order:confirm:"))
async def confirm_order(callback: CallbackQuery, state: FSMContext, container: Container) -> None:
    token = (callback.data or "").rsplit(":", 1)[-1]
    data = await state.get_data()
    if data.get("quote_token") != token or data.get("buyer_id") != callback.from_user.id:
        await callback.answer("Подтверждение устарело", show_alert=True)
        return
    if not await container.abuse_guard.allow(
        callback.from_user.id,
        "create_order",
        limit=container.settings.order_create_limit,
    ):
        await callback.answer("Слишком много заказов. Попробуйте позже.", show_alert=True)
        return
    expires_at = datetime.fromisoformat(str(data["quote_expires_at"]))
    if expires_at <= datetime.now(UTC):
        await callback.answer("Цена обновилась. Подтвердите новую котировку.", show_alert=True)
        await _preview_callback(
            callback,
            state,
            container,
            kind=OrderKind(str(data["kind"])),
            stars=int(data["stars"]),
        )
        return

    priced = _priced_from_state(data)
    kind = OrderKind(str(data["kind"]))
    try:
        order_id = await container.order_service.create_order(
            buyer_telegram_id=callback.from_user.id,
            buyer_username=callback.from_user.username,
            kind=kind,
            recipient_telegram_id=(
                int(data["recipient_id"])
                if data.get("recipient_id") is not None
                else callback.from_user.id
            ),
            recipient_username=(
                str(data["recipient_username"])
                if data.get("recipient_username")
                else callback.from_user.username
            ),
            priced_option=priced,
            request_id=token,
        )
        yookassa_url: str | None = None
        if container.is_ton_payment and not container.is_test_payment:
            instructions = await container.order_service.initiate_ton_payment(order_id)
            text = _format_ton_payment(
                instructions,
                priced.option.stars,
                recipient=str(
                    data.get("recipient_username") or callback.from_user.username or "вы"
                ),
            )
        elif container.is_yookassa_payment and not container.is_test_payment:
            yookassa_instructions = await container.order_service.initiate_yookassa_payment(
                order_id
            )
            yookassa_url = yookassa_instructions.confirmation_url
            recipient = str(data.get("recipient_username") or callback.from_user.username or "вы")
            text = (
                "<b>✅ Заказ создан</b>\n\n"
                f"Номер: <code>{escape(yookassa_instructions.order_number)}</code>\n"
                "<i>Нажмите на номер, чтобы скопировать</i>\n\n"
                f"К оплате: <b>{priced.rub_amount:.0f} ₽</b>\n"
                f"Звёзды: <b>{priced.option.stars} ⭐</b>\n"
                f"Получатель: {escape(display_recipient(recipient))}\n\n"
                "✨ Оплатите заказ по защищённой ссылке. Возврат в бот не подтверждает оплату — "
                "статус проверяется напрямую в YooKassa."
            )
        else:
            order_number = await container.order_service.get_order_number(order_id)
            recipient = str(data.get("recipient_username") or callback.from_user.username or "вы")
            text = (
                "<b>✅ Заказ создан</b>\n\n"
                f"Номер: <code>{escape(order_number)}</code>\n"
                "<i>Нажмите на номер, чтобы скопировать</i>\n\n"
                f"К оплате: <b>{priced.rub_amount:.0f} ₽</b>\n"
                f"Звёзды: <b>{priced.option.stars} ⭐</b>\n"
                f"Получатель: {escape(display_recipient(recipient))}\n\n"
                "✨ После подтверждения оплаты звёзды автоматически отправятся получателю."
            )
    except Exception:
        logger.exception(
            "Order creation failed",
            extra={"event": "order_creation_failed", "user_id": callback.from_user.id},
        )
        await _safe_edit_text(
            _callback_message(callback),
            "Не удалось создать заказ. Обновите цену и попробуйте снова.",
        )
        await callback.answer()
        return
    await state.clear()
    order_message = _callback_message(callback)
    await _safe_edit_text(
        order_message,
        text,
        parse_mode="HTML",
        reply_markup=_markup(
            payment_keyboard(
                order_id,
                test_mode=container.is_test_payment,
                fragment_mode=container.is_ton_payment,
                yookassa_url=yookassa_url,
            )
        ),
    )
    await container.order_service.set_customer_message(
        order_id,
        chat_id=order_message.chat.id,
        message_id=order_message.message_id,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("payment:test:"))
async def test_payment(callback: CallbackQuery, container: Container) -> None:
    order_id = _callback_int(callback.data)
    if order_id is None or not await container.order_service.is_order_owner(
        order_id, callback.from_user.id
    ):
        await callback.answer("Заказ недоступен", show_alert=True)
        return
    if not await container.abuse_guard.allow(
        callback.from_user.id,
        "payment_check",
        limit=container.settings.payment_check_limit,
    ):
        await callback.answer("Слишком много проверок", show_alert=True)
        return
    await container.order_service.confirm_test_payment(
        order_id,
        buyer_telegram_id=callback.from_user.id,
    )
    summary = await container.order_service.get_order_summary(order_id)
    await _safe_edit_text(
        _callback_message(callback), _format_order_result(summary), parse_mode="HTML"
    )
    await callback.answer("Оплата подтверждена")


@router.callback_query(F.data.startswith("payment:check:"))
async def check_payment(callback: CallbackQuery, container: Container) -> None:
    order_id = _callback_int(callback.data)
    if order_id is None or not await container.order_service.is_order_owner(
        order_id, callback.from_user.id
    ):
        await callback.answer("Заказ недоступен", show_alert=True)
        return
    if not container.payment_monitor and not container.is_yookassa_payment:
        await callback.answer("Мониторинг оплаты не настроен", show_alert=True)
        return
    if not await container.abuse_guard.allow(
        callback.from_user.id,
        "payment_check",
        limit=container.settings.payment_check_limit,
    ):
        await callback.answer("Слишком много проверок", show_alert=True)
        return
    await callback.answer("Проверяю оплату...")
    yookassa_url: str | None = None
    try:
        if container.is_yookassa_payment:
            status = await container.order_service.sync_yookassa_payment(order_id)
            yookassa_url = await container.order_service.get_payment_confirmation_url(order_id)
        else:
            if container.payment_monitor is None:
                raise RuntimeError("Payment monitor is not configured")
            status, _, _ = await container.payment_monitor.check_order_now(order_id)
    except Exception:
        logger.exception(
            "Manual payment check failed",
            extra={
                "event": "payment_provider_error",
                "order_id": order_id,
                "user_id": callback.from_user.id,
            },
        )
        await _safe_edit_text(
            _callback_message(callback),
            "Сервис проверки временно недоступен. Заказ сохранён; повторно платить не нужно.",
            reply_markup=_markup(
                payment_keyboard(
                    order_id,
                    test_mode=False,
                    fragment_mode=container.is_ton_payment,
                    yookassa_url=yookassa_url,
                )
            ),
        )
        return
    summary = await container.order_service.get_order_summary(order_id)
    await _safe_edit_text(
        _callback_message(callback),
        _format_order_result(summary),
        parse_mode="HTML",
        reply_markup=(
            _markup(
                payment_keyboard(
                    order_id,
                    test_mode=False,
                    fragment_mode=container.is_ton_payment,
                    yookassa_url=yookassa_url,
                )
            )
            if status
            in {
                OrderStatus.WAITING_FOR_PAYMENT,
                OrderStatus.PAYMENT_DETECTED,
                OrderStatus.PAYMENT_CONFIRMING,
            }
            else None
        ),
    )


@router.callback_query(F.data.startswith("order:cancel:"))
async def cancel_order(callback: CallbackQuery, container: Container) -> None:
    order_id = _callback_int(callback.data)
    if order_id is None:
        await callback.answer("Некорректный заказ", show_alert=True)
        return
    try:
        await container.order_service.cancel_order(
            order_id,
            buyer_telegram_id=callback.from_user.id,
        )
    except (PermissionError, ValueError):
        await callback.answer("Заказ недоступен", show_alert=True)
        return
    summary = await container.order_service.get_order_summary(order_id)
    await _safe_edit_text(
        _callback_message(callback), _format_order_result(summary), parse_mode="HTML"
    )
    await callback.answer()


async def _show_options(
    callback: CallbackQuery,
    state: FSMContext,
    container: Container,
    *,
    kind: OrderKind,
) -> None:
    if not await container.abuse_guard.allow(
        callback.from_user.id,
        "price_quote",
        limit=container.settings.price_quote_limit,
    ):
        await callback.answer("Слишком много запросов цены", show_alert=True)
        return
    try:
        options = await _get_priced_options(container, kind, await state.get_data())
    except Exception:
        logger.exception(
            "Stars options failed",
            extra={"event": "stars_options_failed", "user_id": callback.from_user.id},
        )
        await _safe_edit_text(
            _callback_message(callback),
            "Не удалось получить актуальную цену. Попробуйте позже.",
            reply_markup=_markup([[{"text": "◀️ Назад", "callback_data": "menu:main"}]]),
        )
        await callback.answer()
        return
    title = "⭐ Купить себе" if kind == OrderKind.SELF else "🎁 Выберите количество звёзд для подарка"
    amount_state = (
        OrderFlow.waiting_for_self_amount
        if kind == OrderKind.SELF
        else OrderFlow.waiting_for_gift_amount
    )
    await state.set_state(amount_state)
    await _safe_edit_text(
        _callback_message(callback),
        f"{title}\n\nВыберите готовый вариант или просто отправьте нужное количество числом.",
        reply_markup=_markup(options_keyboard(kind.value, options)),
    )
    await callback.answer()


async def _show_options_message(
    message: Message,
    state: FSMContext,
    container: Container,
    *,
    kind: OrderKind,
) -> None:
    if message.from_user is None:
        await message.answer("Не удалось определить отправителя сообщения.")
        return
    if not await container.abuse_guard.allow(
        message.from_user.id,
        "price_quote",
        limit=container.settings.price_quote_limit,
    ):
        await message.answer("Слишком много запросов цены. Попробуйте позже.")
        return
    try:
        options = await _get_priced_options(container, kind, await state.get_data())
    except Exception:
        logger.exception(
            "Stars options failed",
            extra={"event": "stars_options_failed", "user_id": message.from_user.id},
        )
        await message.answer("Не удалось получить актуальную цену. Попробуйте позже.")
        return
    amount_state = (
        OrderFlow.waiting_for_self_amount
        if kind == OrderKind.SELF
        else OrderFlow.waiting_for_gift_amount
    )
    await state.set_state(amount_state)
    await message.answer(
        "Выберите количество или введите своё:",
        reply_markup=_markup(options_keyboard(kind.value, options)),
    )


async def _get_priced_options(
    container: Container,
    kind: OrderKind,
    state_data: dict,
) -> list[PricedStarsOption]:
    if container.is_fragment_mode:
        raw = [
            StarsOption(stars=stars, currency="FRAGMENT", amount_minor=0)
            for stars in container.settings.popular_stars_amounts
        ]
    elif kind == OrderKind.GIFT:
        username = str(state_data.get("recipient_username") or "")
        resolved = await container.telegram_client.resolve_username(username)
        raw = await container.telegram_client.get_stars_gift_options(resolved)
    else:
        raw = await container.telegram_client.get_stars_topup_options()
    return await container.pricing_service.price_options(raw)


async def _preview_callback(
    callback: CallbackQuery,
    state: FSMContext,
    container: Container,
    *,
    kind: OrderKind,
    stars: int,
) -> None:
    if not await container.abuse_guard.allow(
        callback.from_user.id,
        "price_quote",
        limit=container.settings.price_quote_limit,
    ):
        await callback.answer("Слишком много запросов цены", show_alert=True)
        return
    try:
        text, token = await _build_preview(
            state,
            container,
            user_id=callback.from_user.id,
            buyer_username=callback.from_user.username,
            kind=kind,
            stars=stars,
        )
    except Exception:
        logger.exception(
            "Quote creation failed",
            extra={"event": "quote_creation_failed", "user_id": callback.from_user.id},
        )
        await _safe_edit_text(
            _callback_message(callback), "Не удалось обновить котировку. Попробуйте позже."
        )
        await callback.answer()
        return
    await _safe_edit_text(
        _callback_message(callback),
        text,
        parse_mode="HTML",
        reply_markup=_markup(confirmation_keyboard(token, kind.value)),
    )
    await callback.answer()


async def _preview_message(
    message: Message,
    state: FSMContext,
    container: Container,
    *,
    kind: OrderKind,
    stars: int,
) -> None:
    if message.from_user is None:
        await message.answer("Не удалось определить отправителя сообщения.")
        return
    if not await container.abuse_guard.allow(
        message.from_user.id,
        "price_quote",
        limit=container.settings.price_quote_limit,
    ):
        await message.answer("Слишком много запросов цены. Попробуйте позже.")
        return
    try:
        text, token = await _build_preview(
            state,
            container,
            user_id=message.from_user.id,
            buyer_username=message.from_user.username,
            kind=kind,
            stars=stars,
        )
    except Exception:
        logger.exception(
            "Quote creation failed",
            extra={"event": "quote_creation_failed", "user_id": message.from_user.id},
        )
        await message.answer("Не удалось получить актуальную цену. Попробуйте позже.")
        return
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=_markup(confirmation_keyboard(token, kind.value)),
    )


async def _build_preview(
    state: FSMContext,
    container: Container,
    *,
    user_id: int,
    buyer_username: str | None,
    kind: OrderKind,
    stars: int,
) -> tuple[str, str]:
    validate_stars_amount(
        stars, minimum=container.settings.min_stars, maximum=container.settings.max_stars
    )
    data = await state.get_data()
    if kind == OrderKind.SELF:
        recipient_id = user_id
        recipient_username = buyer_username
        if container.is_fragment_mode and not recipient_username:
            raise ValueError("Telegram username is required for Fragment")
    else:
        recipient_id = int(data["recipient_id"])
        recipient_username = str(data["recipient_username"])

    quote_method = getattr(container.pricing_service, "quote_option", None)
    if quote_method is not None:
        priced = await quote_method(stars)
    else:
        options = await _get_priced_options(container, kind, data)
        priced = next(item for item in options if item.option.stars == stars)
    if priced.quote_expires_at is None:
        raise RuntimeError("Quote expiration is missing")
    token = secrets.token_urlsafe(6)
    await state.set_state(OrderFlow.waiting_for_confirmation)
    await state.update_data(
        quote_token=token,
        buyer_id=user_id,
        kind=kind.value,
        stars=stars,
        recipient_id=recipient_id,
        recipient_username=recipient_username,
        option_currency=priced.option.currency,
        option_amount_minor=priced.option.amount_minor,
        usd_rub_rate=str(priced.usd_rub_rate),
        markup_percent=str(priced.markup_percent),
        rub_amount=str(priced.rub_amount),
        unit_price=str(priced.unit_price or "0"),
        unit_currency=priced.unit_currency or priced.option.currency,
        commission_percent=str(priced.provider_commission_percent),
        quote_expires_at=priced.quote_expires_at.isoformat(),
    )
    username_text = (
        display_recipient(recipient_username) if recipient_username else f"ID {recipient_id}"
    )
    return (
        "<b>✨ Ваш заказ почти готов</b>\n\n"
        f"Получатель: {escape(username_text)}\n"
        f"Звёзды: <b>{stars} ⭐</b>\n"
        f"Итого: <b>{priced.rub_amount:.0f} ₽</b>\n\n"
        "Проверьте данные — дальше останется только оплатить.",
        token,
    )


def _priced_from_state(data: dict) -> PricedStarsOption:
    return PricedStarsOption(
        option=StarsOption(
            stars=int(data["stars"]),
            currency=str(data["option_currency"]),
            amount_minor=int(data["option_amount_minor"]),
        ),
        usd_rub_rate=Decimal(str(data["usd_rub_rate"])),
        markup_percent=Decimal(str(data["markup_percent"])),
        rub_amount=Decimal(str(data["rub_amount"])),
        unit_price=Decimal(str(data["unit_price"])),
        unit_currency=str(data["unit_currency"]),
        provider_commission_percent=Decimal(str(data["commission_percent"])),
        quote_expires_at=datetime.fromisoformat(str(data["quote_expires_at"])),
    )


def _parse_amount(text: str | None, container: Container) -> int | None:
    try:
        return parse_stars_amount(
            text,
            minimum=container.settings.min_stars,
            maximum=container.settings.max_stars,
        )
    except ValueError:
        return None


def _amount_error(container: Container) -> str:
    return (
        f"Введите целое число от {container.settings.min_stars} до {container.settings.max_stars}. "
        "Дробные, отрицательные и текстовые значения не принимаются."
    )


def _callback_int(data: str | None) -> int | None:
    try:
        return int((data or "").rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _callback_message(callback: CallbackQuery) -> Message:
    if not isinstance(callback.message, Message):
        raise ValueError("Callback does not contain an editable bot message")
    return callback.message


async def _safe_edit_text(
    message: Message,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def _format_ton_payment(
    instructions: TonPaymentInstructions,
    stars: int,
    *,
    recipient: str,
) -> str:
    return (
        "<b>✅ Заказ создан</b>\n\n"
        f"Номер: <code>{escape(instructions.order_number)}</code>\n"
        "<i>Нажмите на номер, чтобы скопировать</i>\n\n"
        f"Цена: <b>{Decimal(instructions.rub_amount):.0f} ₽</b>\n"
        f"К оплате: <b>{escape(instructions.ton_amount)} TON</b>\n"
        f"Звёзды: <b>{stars} ⭐</b>\n"
        f"Получатель: {escape(display_recipient(recipient))}\n\n"
        f"Кошелёк:\n<code>{escape(instructions.wallet_address)}</code>\n\n"
        f"Комментарий:\n<code>{escape(instructions.payment_comment)}</code>\n\n"
        "Отправьте точную сумму одним переводом в сети TON и обязательно добавьте комментарий.\n"
        f"Оплатить до: <b>{instructions.expires_at:%d.%m.%Y %H:%M UTC}</b>\n\n"
        "✨ Как только платёж подтвердится, звёзды автоматически отправятся получателю."
    )


def _format_order_result(summary) -> str:
    return format_status_message(
        order_number=summary.order_number,
        stars=summary.stars,
        recipient_username=summary.recipient_username,
        status=summary.status,
    )


def _is_admin(user_id: int, container: Container) -> bool:
    return user_id in container.settings.admin_ids


def _markup(rows: list[list[dict[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=button["text"], callback_data=button["callback_data"])
                for button in row
            ]
            for row in rows
        ]
    )
