from app.domain import PricedStarsOption


def main_menu() -> list[list[dict[str, str]]]:
    return [
        [{"text": "⭐ Купить себе", "callback_data": "self:start"}],
        [{"text": "🎁 Подарить", "callback_data": "gift:start"}],
    ]


def completed_order_keyboard() -> list[list[dict[str, str]]]:
    return [[{"text": "В меню", "callback_data": "menu:main"}]]


def options_keyboard(kind: str, options: list[PricedStarsOption]) -> list[list[dict[str, str]]]:
    rows = [
        [
            {
                "text": f"{item.option.stars} ⭐ - {item.rub_amount} ₽",
                "callback_data": f"{kind}:option:{item.option.stars}",
            }
        ]
        for item in options
    ]
    rows.append([{"text": "✏️ Другое количество", "callback_data": f"{kind}:custom"}])
    rows.append([{"text": "◀️ Назад", "callback_data": "menu:main"}])
    return rows


def confirmation_keyboard(token: str, kind: str) -> list[list[dict[str, str]]]:
    return [
        [{"text": "✅ Создать заказ", "callback_data": f"order:confirm:{token}"}],
        [{"text": "↩️ Изменить количество", "callback_data": f"{kind}:refresh"}],
    ]


def payment_keyboard(
    order_id: int,
    *,
    test_mode: bool,
    fragment_mode: bool,
    yookassa_url: str | None = None,
) -> list[list[dict[str, str]]]:
    if test_mode:
        return [
            [{"text": "💳 Тестовая оплата", "callback_data": f"payment:test:{order_id}"}],
            [{"text": "✖️ Отменить заказ", "callback_data": f"order:cancel:{order_id}"}],
        ]
    if yookassa_url:
        return [
            [{"text": "💳 Оплатить в YooKassa", "url": yookassa_url}],
            [{"text": "🔎 Проверить оплату", "callback_data": f"payment:check:{order_id}"}],
            [{"text": "✖️ Отменить заказ", "callback_data": f"order:cancel:{order_id}"}],
        ]
    if fragment_mode:
        return [
            [{"text": "🔎 Проверить оплату", "callback_data": f"payment:check:{order_id}"}],
            [{"text": "✖️ Отменить заказ", "callback_data": f"order:cancel:{order_id}"}],
        ]
    return [[{"text": "✖️ Отменить заказ", "callback_data": f"order:cancel:{order_id}"}]]
