import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.domain import OrderStatus
from app.services.container import Container

router = Router()
logger = logging.getLogger(__name__)


def _admin(message: Message, container: Container) -> bool:
    return message.from_user is not None and message.from_user.id in container.settings.admin_ids


def _arguments(message: Message) -> list[str]:
    return (message.text or "").split()[1:]


@router.message(Command("order"))
async def show_order(message: Message, container: Container) -> None:
    if not _admin(message, container):
        await message.answer("Команда недоступна.")
        return
    assert message.from_user is not None
    args = _arguments(message)
    if len(args) != 1 or not args[0].isdigit():
        await message.answer("Использование: /order 123")
        return
    try:
        order = await container.order_service.get_order_summary(int(args[0]))
    except ValueError:
        await message.answer("Заказ не найден.")
        return
    await container.order_service.audit_admin_action(
        "view_order",
        actor_telegram_id=message.from_user.id,
        order_id=order.order_id,
    )
    await message.answer(
        f"Order #{order.order_id}\n"
        f"Buyer ID: {order.buyer_telegram_id}\n"
        f"Recipient: @{order.recipient_username or 'unknown'}\n"
        f"Stars: {order.stars}\n"
        f"Status: {order.status.value}\n"
        f"Error code: {order.error_code or '-'}\n"
        f"Payment tx present: {bool(order.payment_tx)}\n"
        f"Fragment request present: {bool(order.purchase_request_id)}\n"
        f"Purchase tx present: {bool(order.purchase_tx)}"
    )


@router.message(Command("manual_review"))
async def manual_review(message: Message, container: Container) -> None:
    if not _admin(message, container):
        await message.answer("Команда недоступна.")
        return
    assert message.from_user is not None
    orders = await container.order_service.list_status(OrderStatus.MANUAL_REVIEW, limit=20)
    await container.order_service.audit_admin_action(
        "list_manual_review",
        actor_telegram_id=message.from_user.id,
    )
    if not orders:
        await message.answer("MANUAL_REVIEW пуст.")
        return
    await message.answer(
        "MANUAL_REVIEW:\n"
        + "\n".join(
            f"#{order.order_id}: {order.stars} Stars, error={order.error_code or '-'}"
            for order in orders
        )
    )


@router.message(Command("user_orders"))
async def user_orders(message: Message, container: Container) -> None:
    if not _admin(message, container):
        await message.answer("Команда недоступна.")
        return
    assert message.from_user is not None
    args = _arguments(message)
    if len(args) != 1 or not args[0].isdigit():
        await message.answer("Использование: /user_orders 123456789")
        return
    orders = await container.order_service.list_user_orders(int(args[0]), limit=20)
    await container.order_service.audit_admin_action(
        "list_user_orders",
        actor_telegram_id=message.from_user.id,
    )
    await _send_order_list(message, "Заказы пользователя", orders)


@router.message(Command("stuck"))
async def stuck(message: Message, container: Container) -> None:
    if not _admin(message, container):
        await message.answer("Команда недоступна.")
        return
    assert message.from_user is not None
    orders = await container.order_service.list_stuck_orders(limit=20)
    await container.order_service.audit_admin_action(
        "list_stuck_orders",
        actor_telegram_id=message.from_user.id,
    )
    await _send_order_list(message, "Незавершенные заказы", orders)


@router.message(Command("reconcile"))
async def reconcile(message: Message, container: Container) -> None:
    if not _admin(message, container):
        await message.answer("Команда недоступна.")
        return
    assert message.from_user is not None
    args = _arguments(message)
    if len(args) != 1 or not args[0].isdigit():
        await message.answer("Использование: /reconcile 123")
        return
    try:
        status = await container.order_service.reconcile_order(
            int(args[0]),
            actor_telegram_id=message.from_user.id,
        )
    except ValueError:
        await message.answer("Заказ не найден.")
        return
    await message.answer(f"Reconciliation завершен. Статус: {status.value}")


@router.message(Command("retry"))
async def retry(message: Message, container: Container) -> None:
    if not _admin(message, container):
        await message.answer("Команда недоступна.")
        return
    assert message.from_user is not None
    args = _arguments(message)
    if len(args) != 2 or not args[0].isdigit() or args[1] != "CONFIRM":
        await message.answer("Использование: /retry 123 CONFIRM")
        return
    try:
        status = await container.order_service.safe_retry_order(
            int(args[0]),
            actor_telegram_id=message.from_user.id,
        )
    except ValueError:
        await message.answer("Для заказа нет безопасного автоматического retry.")
        return
    await message.answer(f"Безопасная повторная проверка выполнена. Статус: {status.value}")


@router.message(Command("cancel_order"))
async def cancel_order(message: Message, container: Container) -> None:
    if not _admin(message, container):
        await message.answer("Команда недоступна.")
        return
    assert message.from_user is not None
    args = _arguments(message)
    if len(args) != 2 or not args[0].isdigit() or args[1] != "CONFIRM":
        await message.answer("Использование: /cancel_order 123 CONFIRM")
        return
    try:
        status = await container.order_service.admin_cancel_order(
            int(args[0]),
            actor_telegram_id=message.from_user.id,
        )
    except ValueError:
        await message.answer("Заказ нельзя безопасно отменить.")
        return
    await message.answer(f"Статус заказа: {status.value}")


@router.message(Command("refund"))
async def refund(message: Message, container: Container) -> None:
    if not _admin(message, container):
        await message.answer("Команда недоступна.")
        return
    assert message.from_user is not None
    args = _arguments(message)
    if len(args) != 2 or not args[0].isdigit() or args[1] != "CONFIRM":
        await message.answer("После фактического ручного возврата: /refund 123 CONFIRM")
        return
    try:
        status = await container.order_service.mark_refunded(
            int(args[0]),
            actor_telegram_id=message.from_user.id,
        )
    except ValueError:
        await message.answer("Заказ не находится в REFUND_REQUIRED.")
        return
    await message.answer(f"Возврат зафиксирован. Статус: {status.value}")


@router.message(Command("stats"))
async def stats(message: Message, container: Container) -> None:
    if not _admin(message, container):
        await message.answer("Команда недоступна.")
        return
    assert message.from_user is not None
    values = await container.order_service.stats()
    await container.order_service.audit_admin_action(
        "view_stats",
        actor_telegram_id=message.from_user.id,
    )
    await message.answer(
        "Orders:\n"
        + ("\n".join(f"{key}: {value}" for key, value in sorted(values.items())) or "empty")
    )


@router.message(Command("health"))
async def health(message: Message, container: Container) -> None:
    if not _admin(message, container):
        await message.answer("Команда недоступна.")
        return
    assert message.from_user is not None
    database_ok = True
    try:
        await container.order_service.stats()
    except Exception:
        logger.exception("Database health check failed", extra={"event": "database_health_failed"})
        database_ok = False
    if not database_ok:
        await message.answer("Database: error")
        return
    fragment_ok: bool | None = None
    if container.fragment_client:
        fragment_ok = (await container.fragment_client.check_health()).ok
    maintenance, purchases_enabled = await container.order_service.get_runtime_controls()
    await container.order_service.audit_admin_action(
        "view_health",
        actor_telegram_id=message.from_user.id,
    )
    await message.answer(
        f"Database: {'ok' if database_ok else 'error'}\n"
        f"Fragment: {('ok' if fragment_ok else 'error') if fragment_ok is not None else 'disabled'}\n"
        f"Maintenance: {maintenance}\n"
        f"Purchases enabled: {purchases_enabled}"
    )


@router.message(Command("maintenance"))
async def maintenance(message: Message, container: Container) -> None:
    await _runtime_control(message, container, key="maintenance_mode", label="Maintenance")


@router.message(Command("purchases"))
async def purchases(message: Message, container: Container) -> None:
    await _runtime_control(message, container, key="purchases_enabled", label="Purchases")


async def _runtime_control(
    message: Message,
    container: Container,
    *,
    key: str,
    label: str,
) -> None:
    if not _admin(message, container):
        await message.answer("Команда недоступна.")
        return
    assert message.from_user is not None
    args = _arguments(message)
    if not args or args[0].lower() == "status":
        maintenance_value, purchases_value = await container.order_service.get_runtime_controls()
        value = maintenance_value if key == "maintenance_mode" else purchases_value
        await container.order_service.audit_admin_action(
            f"view_{key}",
            actor_telegram_id=message.from_user.id,
        )
        await message.answer(f"{label}: {value}")
        return
    action = args[0].lower()
    if action not in {"on", "off"}:
        await message.answer(f"Использование: /{key.split('_')[0]} on|off|status")
        return
    value = action == "on"
    if key == "purchases_enabled" and value and (len(args) < 2 or args[1] != "CONFIRM"):
        await message.answer("Для включения: /purchases on CONFIRM")
        return
    await container.order_service.set_runtime_control(
        key,
        value,
        actor_telegram_id=message.from_user.id,
    )
    await message.answer(f"{label}: {value}")


async def _send_order_list(message: Message, title: str, orders) -> None:
    if not orders:
        await message.answer(f"{title}: пусто.")
        return
    await message.answer(
        f"{title}:\n"
        + "\n".join(
            f"#{order.order_id}: user={order.buyer_telegram_id}, "
            f"stars={order.stars}, status={order.status.value}"
            for order in orders
        )
    )
