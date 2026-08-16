from decimal import Decimal
from datetime import datetime
from html import escape
from typing import Any
from urllib.parse import urlencode

from app.admin.auth import AdminPrincipal
from app.domain import OrderStatus


def e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


STATUS_LABELS = {
    "on": "Включено",
    "off": "Выключено",
    "ok": "Работает",
    "error": "Ошибка",
    "offline": "Нет связи",
    "unknown": "Не проверено",
    "not checked": "Не проверено",
    "test": "Тестовый режим",
    "active": "Активен",
    "blocked": "Заблокирован",
    "created": "Создан",
    "waiting_for_payment": "Ожидает оплаты",
    "payment_detected": "Оплата обнаружена",
    "payment_confirming": "Подтверждаем оплату",
    "paid": "Оплачено",
    "purchase_processing": "Покупаем Stars",
    "stars_sending": "Отправляем Stars",
    "completed": "Выполнено",
    "payment_expired": "Срок оплаты истёк",
    "payment_failed": "Оплата не подтверждена",
    "purchase_failed": "Ошибка отправки Stars",
    "refund_required": "Нужен возврат",
    "refunded": "Возвращено",
    "cancelled": "Отменено",
    "manual_review": "На проверке",
    "waiting_for_merchant_balance": "Ожидает пополнения",
}


def status_label(value: Any) -> str:
    raw = value.value if hasattr(value, "value") else str(value or "")
    return STATUS_LABELS.get(raw, raw.replace("_", " ").capitalize())


def health_badge(value: Any) -> str:
    state = "ok" if value is True else "offline" if value is False else "test" if value == "test" else "unknown"
    return badge(state)


def login_page(*, error: str = "") -> str:
    notice = f'<div class="alert error">{e(error)}</div>' if error else ""
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TerStars · Вход</title><link rel="stylesheet" href="/admin/static/admin.css"></head>
<body class="login-body"><main class="login-card"><div class="brand-mark">T</div>
<h1>TerStars · Панель управления</h1><p class="muted">Закрытая панель управления магазином</p>{notice}
<form method="post" action="/admin/login" class="stack">
<label>Логин<input name="username" autocomplete="username" required maxlength="64"></label>
<label>Пароль<input type="password" name="password" autocomplete="current-password" required></label>
<label>Код 2FA<input name="totp" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="Если включён"></label>
<button class="button primary" type="submit">Войти</button></form></main></body></html>"""


def layout(
    title: str,
    body: str,
    principal: AdminPrincipal,
    *,
    active: str,
    notice: str = "",
    error: str = "",
) -> str:
    asset_version = "20260815-star-price"
    links = [
        ("dashboard", "/admin", "Обзор"),
        ("orders", "/admin/orders", "Заказы"),
        ("users", "/admin/users", "Пользователи"),
        ("security", "/admin/security", "Блокировки"),
        ("audit", "/admin/audit", "Журнал действий"),
        ("system", "/admin/system", "Система"),
        ("settings", "/admin/settings", "Настройки"),
    ]
    nav = "".join(
        f'<a class="nav-link {"active" if key == active else ""}" href="{url}">{label}</a>'
        for key, url, label in links
    )
    message = f'<div class="alert success">{e(notice)}</div>' if notice else ""
    if error:
        message += f'<div class="alert error">{e(error)}</div>'
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark">
<title>{e(title)} · TerStars</title><link rel="stylesheet" href="/admin/static/admin.css?v={asset_version}"><script src="/admin/static/admin.js?v={asset_version}" defer></script></head>
<body><aside class="sidebar"><a class="brand" href="/admin"><span class="brand-mark">T</span><span>TerStars</span></a>
<nav>{nav}</nav><div class="account"><strong>{e(principal.username)}</strong><span>{e(principal.role.value)}</span>
<form method="post" action="/admin/logout"><input type="hidden" name="csrf" value="{e(principal.csrf_token)}">
<button class="link-button" type="submit">Выйти</button></form></div></aside>
<main class="content"><header class="page-head"><div><p class="eyebrow">ЦЕНТР УПРАВЛЕНИЯ</p><h1>{e(title)}</h1></div></header>{message}{body}</main></body></html>"""


def badge(status: Any) -> str:
    value = status.value if hasattr(status, "value") else str(status or "—")
    tone = (
        "ok"
        if value in {"completed", "succeeded", "ok", "on", "active", "test"}
        else "warn"
        if value
        in {
            "waiting_for_payment",
            "payment_confirming",
            "paid",
            "purchase_processing",
            "stars_sending",
        }
        else "danger"
        if value
        in {
            "manual_review",
            "refund_required",
            "purchase_failed",
            "payment_failed",
            "waiting_for_merchant_balance",
            "offline",
        }
        else "neutral"
    )
    return f'<span class="badge {tone}">{e(status_label(value))}</span>'


def _star_price_card(price: dict[str, Any] | None) -> str:
    if not price or not price.get("ok"):
        error = price.get("error") if isinstance(price, dict) else "Цена пока не загружена"
        return f"""<article class="star-price-card error" data-star-price-card>
<div class="star-price-top"><span class="star-price-label">Цена Fragment без комиссии</span><span class="star-price-mode">KYC</span></div>
<strong data-star-price-main>Нет данных</strong>
<div class="star-price-meta" data-star-price-meta>{e(error)}</div></article>"""
    amount = price.get("amount", "—")
    currency = price.get("currency", "—")
    rub = Decimal(str(price.get("rub_per_star", "0")))
    method = price.get("payment_method", "—")
    mode = str(price.get("api_mode", "—")).upper()
    checked_at = price.get("checked_at", "")
    commission = Decimal(str(price.get("commission_percent", "0")))
    commission_text = (
        "KYC: комиссия API 0%"
        if commission == 0
        else f"Комиссия API не включена: {commission}%"
    )
    return f"""<article class="star-price-card ok" data-star-price-card>
<div class="star-price-top"><span class="star-price-label">Цена Fragment без комиссии</span><span class="star-price-mode">{e(mode)}</span></div>
<div class="star-price-value"><strong data-star-price-main>{rub:.4f} ₽</strong><span>за 1 Star</span></div>
<div class="star-price-meta" data-star-price-meta>
<span>{e(amount)} {e(currency)}</span><span>{e(method)}</span>
</div>
<div class="star-price-foot"><span data-star-price-commission>{e(commission_text)}</span><span data-star-price-updated>{e(_short_time(checked_at))}</span></div></article>"""


def _short_time(value: Any) -> str:
    if not value:
        return "обновляется автоматически"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return f"обновлено {parsed:%H:%M:%S}"


def dashboard_page(data: dict[str, Any]) -> str:
    statuses = data["statuses"]
    health = data.get("health", {})
    cards = [
        ("Заказы сегодня", data["today_orders"], "За текущие сутки"),
        ("Выручка", f"{data['total_revenue']:.2f} ₽", "За всё время"),
        ("Продано звёзд", f"{data['total_stars']:,}", "Выполненные заказы"),
        ("Выручка сегодня", f"{data['today_revenue']:.2f} ₽", "Подтверждённые платежи"),
        ("Звёзд сегодня", f"{data['today_stars']:,}", "Оплаченные заказы"),
        ("Средний чек", f"{data['average_check']:.2f} ₽", "Сегодня"),
        ("Выполнено", statuses.get("completed", 0), "За всё время"),
        ("Оплачено", statuses.get("paid", 0), "Ожидают исполнения"),
        ("Ожидают оплату", statuses.get("waiting_for_payment", 0), "Открытые счета"),
        (
            "В обработке",
            statuses.get("purchase_processing", 0) + statuses.get("stars_sending", 0),
            "В обработке",
        ),
        (
            "Ошибки",
            statuses.get("payment_failed", 0) + statuses.get("purchase_failed", 0),
            "Требуют анализа",
        ),
        (
            "Требуют внимания",
            statuses.get("manual_review", 0) + statuses.get("waiting_for_merchant_balance", 0),
            "Ручная проверка и баланс",
        ),
    ]
    card_html = "".join(
        f'<article class="metric"><span>{e(label)}</span><strong>{e(value)}</strong><small>{e(hint)}</small></article>'
        for label, value, hint in cards
    )
    status_html = (
        "".join(
            f'<div class="status-row"><span>{e(status_label(key))}</span><strong>{value}</strong></div>'
            for key, value in sorted(statuses.items(), key=lambda item: item[1], reverse=True)[:8]
        )
        or '<div class="empty">Заказов пока нет</div>'
    )
    recent_rows = "".join(_order_row(order) for order in data["recent"])
    health_html = "".join(
        f"<div><span>{e(label)}</span>{health_badge(health.get(key))}</div>"
        for key, label in (
            ("telegram", "Telegram-бот"),
            ("fragment", "Провайдер Stars"),
            ("payment", "Платёжный провайдер"),
            ("database", "База данных"),
            ("worker", "Обработчик заказов"),
        )
    )
    balance = health.get("operational_balance", 0)
    balance_text = f"{balance} Stars (подтверждено)" if balance else "API не предоставляет"
    trend = data.get("trend", [])
    chart_html = _trend_chart(trend)
    star_price_html = _star_price_card(health.get("star_price"))
    return f"""<section class="overview-chart-reveal"><div class="overview-chart-hint">Наведи сюда, чтобы увидеть графики</div><div class="overview-chart-panel"><div class="panel-head"><div><p class="eyebrow">ОБЗОР</p><h2>Динамика магазина</h2></div><span class="muted">Последние 14 дней</span></div>{chart_html}</div></section>
<section class="metrics">{card_html}</section><section class="grid two">
<article class="panel"><div class="panel-head"><h2>Состояния заказов</h2><a href="/admin/orders">Все заказы</a></div>{status_html}</article>
<article class="panel"><div class="panel-head"><h2>Операционный контур</h2></div>
{star_price_html}<div class="health-list"><div><span>Ошибки провайдеров · 24 ч</span><strong>{data["provider_errors"]}</strong></div>
<div><span>Резерв Stars</span><strong>{e(balance_text)}</strong></div>{health_html}</div></article></section>
<section class="panel"><div class="panel-head"><h2>Последние заказы</h2><a href="/admin/orders">Открыть журнал</a></div>
<div class="table-wrap"><table><thead><tr><th>Заказ</th><th>Пользователь</th><th>Получатель</th><th>Звёзды</th><th>Сумма</th><th>Статус</th><th>Создан</th></tr></thead>
<tbody>{recent_rows or '<tr><td colspan="7" class="empty">Заказов пока нет</td></tr>'}</tbody></table></div></section>"""


def _trend_chart(trend: list[dict[str, Any]]) -> str:
    if not trend:
        return '<div class="empty">Данных для графика пока нет</div>'
    revenues = [Decimal(str(item["revenue"])) for item in trend]
    orders = [int(item["orders"]) for item in trend]
    stars = [int(item["stars"]) for item in trend]
    total_revenue = sum(revenues, Decimal("0"))
    total_orders = sum(orders)
    total_stars = sum(stars)
    average_check = total_revenue / total_orders if total_orders else Decimal("0")
    if not total_revenue and not total_orders:
        return (
            '<div class="chart-empty-state"><strong>Данных пока мало</strong>'
            '<span>Здесь появится динамика выручки, заказов и звёзд после первых оплат.</span></div>'
        )

    max_revenue = max(revenues, default=Decimal("1")) or Decimal("1")
    max_orders = max(orders, default=1) or 1
    width, height, left, right, top, bottom = 920, 300, 64, 28, 22, 46
    plot_width = width - left - right
    plot_height = height - top - bottom
    step = plot_width / max(len(trend) - 1, 1)
    revenue_points = []
    order_points = []
    marks = []
    grid = []
    y_labels = []
    for tick in range(5):
        ratio = tick / 4
        y = top + plot_height - ratio * plot_height
        value = max_revenue * Decimal(str(ratio))
        grid.append(f'<line class="chart-grid-line" x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" />')
        y_labels.append(f'<text class="chart-axis-label" x="{left - 10}" y="{y + 4:.1f}" text-anchor="end">{value:.0f} ₽</text>')
    for index, item in enumerate(trend):
        x = left + index * step
        revenue_y = top + plot_height - float(Decimal(str(item["revenue"])) / max_revenue * plot_height)
        order_y = top + plot_height - (int(item["orders"]) / max_orders * plot_height)
        revenue_points.append(f"{x:.1f},{revenue_y:.1f}")
        order_points.append(f"{x:.1f},{order_y:.1f}")
        label = e(item["label"])
        marks.append(
            f'<g class="chart-point"><title>{label}: {Decimal(str(item["revenue"])):.0f} ₽ · {item["orders"]} заказов · {item["stars"]} звёзд</title>'
            f'<circle class="revenue-point" cx="{x:.1f}" cy="{revenue_y:.1f}" r="4" />'
            f'<circle class="orders-point" cx="{x:.1f}" cy="{order_y:.1f}" r="3" />'
            f'<text class="chart-date-label" x="{x:.1f}" y="{height - 14}" text-anchor="middle">{label if index % 2 == 0 or index == len(trend) - 1 else ""}</text></g>'
        )
    area_points = f"{left},{top + plot_height} {' '.join(revenue_points)} {width - right},{top + plot_height}"
    stats = (
        f'<div class="chart-stat"><span>Выручка</span><strong>{total_revenue:.0f} ₽</strong></div>'
        f'<div class="chart-stat"><span>Заказы</span><strong>{total_orders}</strong></div>'
        f'<div class="chart-stat"><span>Звёзды</span><strong>{total_stars}</strong></div>'
        f'<div class="chart-stat"><span>Средний чек</span><strong>{average_check:.0f} ₽</strong></div>'
    )
    return f'''<div class="chart-summary">{stats}</div>
<div class="chart-legend"><span><i class="legend-dot revenue-dot"></i>Выручка</span><span><i class="legend-dot orders-dot"></i>Заказы</span></div>
<svg class="trend-chart" viewBox="0 0 {width} {height}" role="img" aria-label="График выручки и заказов за последние 14 дней">
<rect class="chart-plot-bg" x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" rx="10" />
{"".join(grid)}{"".join(y_labels)}
<polyline class="revenue-area" points="{area_points}" />
<polyline class="revenue-line" points="{' '.join(revenue_points)}" />
<polyline class="orders-line" points="{' '.join(order_points)}" />
<line class="chart-axis" x1="{left}" y1="{top + plot_height}" x2="{width - right}" y2="{top + plot_height}" />
{"".join(marks)}</svg>'''


def orders_page(
    rows: list[dict[str, Any]],
    *,
    query: str,
    status: str,
    paid: str,
    filters: dict[str, str],
    page: int = 1,
    has_next: bool = False,
) -> str:
    options = '<option value="">Все статусы</option>' + "".join(
        f'<option value="{item.value}" {"selected" if item.value == status else ""}>{e(item.value)}</option>'
        for item in OrderStatus
    )
    table_rows = "".join(_order_row(row["order"], row["payment"], row["attempt"]) for row in rows)
    query_values = {"q": query, "status": status, "paid": paid, **filters}
    links = []
    if page > 1:
        links.append(
            f'<a class="button" href="?{e(urlencode({**query_values, "page": page - 1}))}">Назад</a>'
        )
    links.append(f"<span>Страница {page}</span>")
    if has_next:
        links.append(
            f'<a class="button" href="?{e(urlencode({**query_values, "page": page + 1}))}">Далее</a>'
        )
    table_rows += f'<tr><td colspan="9"><nav class="actions">{"".join(links)}</nav></td></tr>'
    return f"""<form class="filters panel" method="get"><label>Поиск<input name="q" value="{e(query)}" placeholder="Номер, ID, имя пользователя или транзакция"></label>
<label>Статус<select name="status">{options}</select></label><label>Оплата<select name="paid">
<option value="">Любая</option><option value="yes" {"selected" if paid == "yes" else ""}>Оплачено</option>
<option value="no" {"selected" if paid == "no" else ""}>Не оплачено</option></select></label>
<label>Звёзд от<input name="min_stars" inputmode="numeric" value="{e(filters["min_stars"])}"></label>
<label>Звёзд до<input name="max_stars" inputmode="numeric" value="{e(filters["max_stars"])}"></label>
<label>Сумма от<input name="min_amount" inputmode="decimal" value="{e(filters["min_amount"])}"></label>
<label>Сумма до<input name="max_amount" inputmode="decimal" value="{e(filters["max_amount"])}"></label>
<label>Дата от<input type="date" name="date_from" value="{e(filters["date_from"])}"></label>
<label>Дата до<input type="date" name="date_to" value="{e(filters["date_to"])}"></label>
<button class="button" type="submit">Применить</button></form><section class="panel table-panel"><div class="table-wrap"><table>
<thead><tr><th>Заказ</th><th>Пользователь</th><th>Получатель</th><th>Stars</th><th>Сумма</th><th>Оплата</th><th>Покупка</th><th>Статус</th><th>Обновлён</th></tr></thead>
<tbody>{table_rows or '<tr><td colspan="9" class="empty">Ничего не найдено</td></tr>'}</tbody></table></div></section>"""


def order_detail_page(data: dict[str, Any], principal: AdminPrincipal) -> str:
    order, payment, attempt = data["order"], data["payment"], data["attempt"]
    is_yookassa = bool(payment and payment.provider == "yookassa")
    fields = [
        ("Платёжный провайдер", payment.provider if payment else "—"),
        ("Статус провайдера", payment.provider_status if payment and is_yookassa else "—"),
        (
            "Ожидаемая оплата",
            f"{payment.expected_amount:.2f} {payment.currency}" if payment else "—",
        ),
        ("Paid", payment.paid if payment and is_yookassa else "—"),
        ("Refundable", payment.refundable if payment and is_yookassa else "—"),
        (
            "Последняя синхронизация",
            payment.last_provider_sync_at if payment and is_yookassa else "—",
        ),
        ("Last webhook", payment.last_webhook_event if payment and is_yookassa else "—"),
        ("Статус возврата", payment.refund_status if payment and is_yookassa else "—"),
        ("Внутренний ID", order.id),
        ("Создан", order.created_at),
        ("Обновлён", order.updated_at),
        ("Тип заказа", order.kind),
        ("Telegram ID", order.buyer_telegram_id),
        ("Имя пользователя", order.buyer_username or "—"),
        ("Получатель", order.recipient_username or "—"),
        ("Звёзды", order.stars),
        ("Сумма", f"{order.rub_amount:.2f} ₽"),
        ("Метод оплаты", order.customer_payment_type or "—"),
        ("Статус оплаты", payment.status if payment else order.payment_status or "—"),
        ("Статус покупки", attempt.status if attempt else "—"),
        ("Провайдер Stars", attempt.provider if attempt else "—"),
        ("Идентификатор оплаты", payment.provider_reference if payment else "—"),
        ("Транзакция оплаты", payment.transaction_hash if payment else "—"),
        ("Запрос провайдеру", attempt.provider_request_id if attempt else "—"),
        ("Транзакция провайдера", attempt.transaction_id if attempt else "—"),
        ("Код ошибки", order.error_code or "—"),
        ("Связанный идентификатор", "см. журнал событий"),
    ]
    detail_html = "".join(
        f"<div><span>{e(label)}</span><strong>{e(value)}</strong></div>" for label, value in fields
    )
    timeline = (
        "".join(_timeline_event(event) for event in data["events"])
        or '<li class="empty">История пуста</li>'
    )
    actions = ""
    if principal.can_manage():
        buttons = [
            '<button name="action" value="resend_notification">Повторить уведомление</button>'
        ]
        if payment and payment.provider == "yookassa":
            buttons.append(
                '<button name="action" value="sync_payment">Проверить статус YooKassa</button>'
            )
            if order.status == OrderStatus.REFUND_REQUIRED:
                buttons.append(
                    '<button class="danger-button" name="action" value="refund_yookassa">'
                    f"Возврат: введите REFUND {e(order.order_number)}</button>"
                )
        if attempt and attempt.provider_request_id and order.status != OrderStatus.COMPLETED:
            buttons.append('<button name="action" value="reconcile">Сверить с провайдером</button>')
        if order.status in {
            OrderStatus.PAID,
            OrderStatus.PURCHASE_PROCESSING,
            OrderStatus.STARS_SENDING,
            OrderStatus.WAITING_FOR_MERCHANT_BALANCE,
        } or (
            order.status == OrderStatus.MANUAL_REVIEW and attempt and attempt.provider_request_id
        ):
            buttons.append('<button name="action" value="retry">Безопасно повторить</button>')
        if order.status not in {
            OrderStatus.COMPLETED,
            OrderStatus.REFUNDED,
            OrderStatus.PAYMENT_FAILED,
        }:
            buttons.append('<button name="action" value="manual_review">Отправить на проверку</button>')
        if order.status in {OrderStatus.CREATED, OrderStatus.WAITING_FOR_PAYMENT}:
            buttons.append(
                '<button class="danger-button" name="action" value="cancel">Отменить неоплаченный</button>'
            )
        actions = f"""<section class="panel"><h2>Разрешённые действия</h2><p class="muted">Финансовые действия требуют ввода CONFIRM.</p>
<form class="actions" method="post" action="/admin/orders/{e(order.order_number)}/action">
<input type="hidden" name="csrf" value="{e(principal.csrf_token)}"><input name="confirmation" placeholder="CONFIRM">
{"".join(buttons)}</form></section>"""
    return f"""<div class="detail-title"><div><p class="eyebrow">ЗАКАЗ</p><h2>{e(order.order_number)}</h2><button class="copy-order" type="button" data-copy-order="{e(order.order_number)}">Скопировать номер</button></div>{badge(order.status)}</div>
<section class="panel detail-grid">{detail_html}</section>{actions}<section class="panel"><h2>Журнал событий</h2><ol class="timeline">{timeline}</ol></section>"""


def _timeline_event(event: Any) -> str:
    details = event.details if isinstance(event.details, dict) else {}
    summary = details.get("error_code") or details.get("reason") or ""
    state = ""
    if event.previous_state or event.new_state:
        state = f"{event.previous_state or '—'} → {event.new_state or '—'}"
    context = " · ".join(str(value) for value in (summary, state) if value)
    return (
        f"<li><span>{e(event.created_at)}</span><strong>{e(event.event)}"
        f"<small>{e(context)}</small></strong><code>{e(event.correlation_id or '')}</code></li>"
    )


def users_page(rows: list[dict[str, Any]], query: str) -> str:
    body = "".join(
        f'<tr><td><a href="/admin/users/{r["user"].telegram_id}">{r["user"].telegram_id}</a></td><td>{e(r["user"].username or "—")}</td>'
        f"<td>{r['orders']}</td><td>{r['spent']:.2f} ₽</td><td>{r['stars']}</td><td>{r['successful']}</td><td>{r['failed']}</td>"
        f"<td>{badge('blocked') if r['blocked'] else badge('active')}</td><td>{e(r['user'].updated_at)}</td></tr>"
        for r in rows
    )
    return f"""<form class="filters panel" method="get"><label>Поиск<input name="q" value="{e(query)}" placeholder="Telegram ID или имя пользователя"></label>
<button class="button" type="submit">Найти</button></form><section class="panel table-panel"><div class="table-wrap"><table><thead><tr>
<th>Telegram ID</th><th>Имя пользователя</th><th>Заказы</th><th>Сумма</th><th>Звёзды</th><th>Успешные</th><th>Ошибки</th><th>Доступ</th><th>Активность</th></tr></thead>
<tbody>{body or '<tr><td colspan="9" class="empty">Пользователей пока нет</td></tr>'}</tbody></table></div></section>"""


def user_detail_page(data: dict[str, Any], principal: AdminPrincipal) -> str:
    user, orders, block = data["user"], data["orders"], data["block"]
    order_rows = "".join(_order_row(order) for order in orders)
    controls = ""
    if principal.can_manage():
        controls = f"""<section class="panel"><h2>Управление</h2><form class="stack" method="post" action="/admin/users/{user.telegram_id}/action">
<input type="hidden" name="csrf" value="{e(principal.csrf_token)}"><label>Внутренняя заметка<textarea name="note">{e(user.admin_note or "")}</textarea></label>
<button name="action" value="note">Сохранить заметку</button><label>Причина блокировки<input name="reason" value="{e(block.reason if block else "")}"></label>
<div class="actions"><button name="action" value="block">Заблокировать</button><button name="action" value="unblock">Разблокировать</button></div></form></section>"""
    return f"""<section class="panel detail-grid"><div><span>Telegram ID</span><strong>{user.telegram_id}</strong></div>
<div><span>Имя пользователя</span><strong>{e(user.username or "—")}</strong></div><div><span>Первый визит</span><strong>{e(user.created_at)}</strong></div>
<div><span>Последняя активность</span><strong>{e(user.updated_at)}</strong></div><div><span>Доступ</span><strong>{"Заблокирован" if block else "Активен"}</strong></div></section>
{controls}<section class="panel"><h2>Заказы пользователя</h2><div class="table-wrap"><table><tbody>{order_rows or '<tr><td class="empty">Заказов нет</td></tr>'}</tbody></table></div></section>"""


def audit_page(rows: list[tuple[Any, str | None]]) -> str:
    body = "".join(
        f"<tr><td>{e(event.created_at)}</td><td>{e(username or event.actor_telegram_id or 'system')}</td><td>{e(event.event)}</td>"
        f"<td>{e(event.entity_type or 'order')}: {e(event.entity_id or event.order_id or '—')}</td><td>{e(event.previous_state or '—')}</td><td>{e(event.new_state or '—')}</td><td>{e(event.ip_address or '—')}</td><td><code>{e(event.correlation_id or '—')}</code></td></tr>"
        for event, username in rows
    )
    empty = '<tr><td colspan="8" class="empty">Событий нет</td></tr>'
    return f'<section class="panel table-panel"><div class="table-wrap"><table><thead><tr><th>Когда</th><th>Кто</th><th>Действие</th><th>Объект</th><th>Было</th><th>Стало</th><th>IP</th><th>Связанный ID</th></tr></thead><tbody>{body or empty}</tbody></table></div></section>'


def _order_row(order: Any, payment: Any = None, attempt: Any = None) -> str:
    cells = [
        f'<a href="/admin/orders/{e(order.order_number)}">{e(order.order_number)}</a>',
        f"{order.buyer_telegram_id}<small>{e(order.buyer_username or '')}</small>",
        e(order.recipient_username or "—"),
        str(order.stars),
        f"{order.rub_amount:.2f} ₽",
    ]
    if payment is not None or attempt is not None:
        cells.extend(
            [badge(payment.status if payment else "—"), badge(attempt.status if attempt else "—")]
        )
    cells.extend([badge(order.status), e(order.updated_at or order.created_at)])
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"
