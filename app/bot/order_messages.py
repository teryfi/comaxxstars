from html import escape

from app.domain import OrderStatus


STATUS_LABELS = {
    OrderStatus.WAITING_FOR_PAYMENT: "Ожидает оплаты",
    OrderStatus.PAYMENT_DETECTED: "Оплата обнаружена",
    OrderStatus.PAYMENT_CONFIRMING: "Подтверждаем оплату",
    OrderStatus.PAYMENT_EXPIRED: "Срок оплаты истёк",
    OrderStatus.PAYMENT_FAILED: "Оплата не подтверждена",
    OrderStatus.PURCHASE_FAILED: "Не удалось отправить Stars",
    OrderStatus.PAID: "Оплачено",
    OrderStatus.PURCHASE_PROCESSING: "Покупаем Stars",
    OrderStatus.STARS_SENDING: "Отправляем Stars",
    OrderStatus.COMPLETED: "Выполнено",
    OrderStatus.CANCELLED: "Отменено",
    OrderStatus.REFUNDED: "Возвращено",
    OrderStatus.MANUAL_REVIEW: "На проверке",
    OrderStatus.REFUND_REQUIRED: "Нужен возврат",
    OrderStatus.WAITING_FOR_MERCHANT_BALANCE: "Ожидает пополнения",
}


def status_label(status: OrderStatus) -> str:
    return STATUS_LABELS.get(status, "Статус обновлён")


def display_recipient(value: str | None) -> str:
    clean = (value or "вы").strip()
    if clean.lower() in {"вы", "you"}:
        return "вы"
    return clean if clean.startswith("@") else f"@{clean}"


def format_status_message(
    *,
    order_number: str,
    stars: int,
    recipient_username: str | None,
    status: OrderStatus,
) -> str:
    recipient = escape(display_recipient(recipient_username))
    copyable_number = f"<code>{escape(order_number)}</code>"
    if status == OrderStatus.COMPLETED:
        return (
            "<b>✅ Заказ выполнен</b>\n\n"
            f"Номер: {copyable_number}\n"
            f"Отправлено: <b>{stars} ⭐</b>\n"
            f"Получатель: {recipient}\n"
            "Статус: <b>успешно</b>\n\n✨ Спасибо за покупку!"
        )
    if status in {
        OrderStatus.PAID,
        OrderStatus.PURCHASE_PROCESSING,
        OrderStatus.STARS_SENDING,
    }:
        return (
            "<b>✅ Оплата получена</b>\n\n"
            "Платёж подтверждён. Выполняем покупку Telegram Stars…\n"
            f"Номер: {copyable_number}\n\n"
            "Повторная оплата не требуется."
        )
    if status in {
        OrderStatus.MANUAL_REVIEW,
        OrderStatus.REFUND_REQUIRED,
        OrderStatus.WAITING_FOR_MERCHANT_BALANCE,
    }:
        return (
            "<b>⚠️ Оплата успешно получена</b>\n\n"
            "Возникла временная техническая задержка при выполнении заказа. "
            "Повторная оплата не требуется.\n\n"
            f"Номер: {copyable_number}"
        )
    return f"<b>Заказ</b> {copyable_number}\n\n{status_label(status)}."
