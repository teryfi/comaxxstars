from typing import Any

from app.admin.auth import AdminPrincipal
from app.admin.views import badge, e, health_badge


def system_page(
    *,
    health: dict[str, Any],
    maintenance: bool,
    purchases_enabled: bool,
    principal: AdminPrincipal,
) -> str:
    services = [
        ("База данных", health.get("database", False)),
        ("Telegram API", health.get("telegram")),
        ("Провайдер Stars", health.get("fragment")),
        ("Платёжный провайдер", health.get("payment")),
        ("Обработчик заказов", health.get("worker")),
    ]
    cards = "".join(
        f'<div class="health-card"><span>{e(name)}</span>{health_badge(value)}</div>'
        for name, value in services
    )
    controls = ""
    if principal.is_owner():
        controls = f"""<section class="panel"><h2>Критические переключатели</h2>
<form class="stack" method="post" action="/admin/system/control"><input type="hidden" name="csrf" value="{e(principal.csrf_token)}">
<label>Действие<select name="key"><option value="maintenance_mode">Режим обслуживания</option><option value="purchases_enabled">Приём заказов</option></select></label>
<label>Значение<select name="value"><option value="on">Включить</option><option value="off">Выключить</option></select></label>
<label>Подтверждение<input name="confirmation" placeholder="CONFIRM" required></label>
<button type="submit">Применить</button></form></section>"""
    return f"""<section class="health-grid">{cards}</section><section class="grid two">
<article class="panel"><h2>Рабочее состояние</h2><div class="health-list"><div><span>Режим обслуживания</span>{badge("on" if maintenance else "off")}</div>
<div><span>Приём заказов</span>{badge("on" if purchases_enabled else "off")}</div><div><span>Очередь</span><strong>{e(health.get("queue", "—"))}</strong></div>
<div><span>Операционный резерв</span><strong>{e(str(health.get("operational_balance", 0)) + " Stars (минимум)" if health.get("operational_balance") else "Недоступен в SDK Fragment")}</strong></div><div><span>Сборка</span><strong>{e(health.get("version", "dev"))}</strong></div></div></article>
<article class="panel"><h2>Безопасность</h2><p>Секреты скрыты. Проверка баланса отмечается как недоступная, поскольку установленный Fragment SDK не публикует balance endpoint.</p></article></section>{controls}"""


def settings_page(
    *,
    config: Any,
    runtime: dict[str, str],
) -> str:
    values = [
        ("MIN_STARS", config.min_stars),
        ("MAX_STARS", config.max_stars),
        ("STARS_MARKUP_PERCENT", config.stars_markup_percent),
        ("STARS_STANDARD_ORDER_MARKUP_PERCENT", config.stars_standard_order_markup_percent),
        ("STARS_STANDARD_ORDER_THRESHOLD", config.stars_standard_order_threshold),
        ("STARS_LARGE_ORDER_MARKUP_PERCENT", config.stars_large_order_markup_percent),
        ("STARS_LARGE_ORDER_THRESHOLD", config.stars_large_order_threshold),
        ("ORDER_TIMEOUT_MINUTES", config.order_timeout_minutes),
        ("RATE_LIMIT_REQUESTS", config.rate_limit_requests),
        ("DAILY_STARS_LIMIT_PER_USER", config.daily_stars_limit_per_user),
        ("MAINTENANCE_MODE", runtime.get("maintenance_mode", str(config.maintenance_mode))),
        ("PURCHASES_ENABLED", runtime.get("purchases_enabled", str(config.purchases_enabled))),
        ("BOT_TOKEN", "configured" if config.bot_token.get_secret_value() else "missing"),
        ("FRAGMENT_WALLET_SEED", "configured" if config.fragment_wallet_seed else "missing"),
        ("TONCENTER_API_KEY", "configured" if config.toncenter_api_key else "optional / missing"),
        ("OPERATIONAL_BALANCE_CONFIRMED_STARS", config.operational_balance_confirmed_stars),
    ]
    rows = "".join(f"<tr><td>{e(key)}</td><td>{e(value)}</td></tr>" for key, value in values)
    return f"""<section class="panel"><p class="muted">Показываются только безопасные значения. Секреты никогда не выводятся.</p>
<div class="table-wrap"><table><thead><tr><th>Параметр</th><th>Значение</th></tr></thead><tbody>{rows}</tbody></table></div></section>"""


def security_page(
    blocked_users: list[Any], blocked_ips: list[Any], principal: AdminPrincipal
) -> str:
    users = "".join(
        f'<tr><td><a href="/admin/users/{row.telegram_user_id}">{row.telegram_user_id}</a></td><td>{e(row.reason)}</td><td>{e(row.created_at)}</td><td>{e(row.expires_at or "—")}</td></tr>'
        for row in blocked_users
    )
    ips = "".join(
        f"<tr><td>{e(row.cidr)}</td><td>{e(row.reason)}</td><td>{e(row.created_at)}</td><td>{e(row.expires_at or '—')}</td></tr>"
        for row in blocked_ips
    )
    controls = ""
    if principal.can_manage():
        controls = f"""<section class="panel"><h2>Управление блокировкой IP</h2>
<form class="stack" method="post" action="/admin/security/ip-action"><input type="hidden" name="csrf" value="{e(principal.csrf_token)}">
<label>IP или CIDR<input name="cidr" placeholder="203.0.113.10/32" required></label><label>Причина<input name="reason"></label>
<label>Подтверждение<input name="confirmation" placeholder="CONFIRM; для private/loopback — BLOCK_INTERNAL" required></label>
<div class="actions"><button name="action" value="add">Добавить</button><button name="action" value="remove">Удалить</button></div></form></section>"""
    return f"""{controls}<section class="panel"><h2>Заблокированные пользователи</h2><p class="muted">Пользователи Telegram блокируются по числовому ID. Это основная защита бота.</p>
<div class="table-wrap"><table><thead><tr><th>Telegram ID</th><th>Причина</th><th>Создано</th><th>Истекает</th></tr></thead><tbody>{users or '<tr><td colspan="4" class="empty">Список пуст</td></tr>'}</tbody></table></div></section>
<section class="panel"><h2>Блокировка IP админ-панели</h2><p class="muted">IP Telegram-пользователей не собираются. Этот список защищает только вход в админ-панель, поэтому его лучше оставить.</p>
<div class="table-wrap"><table><thead><tr><th>IP / CIDR</th><th>Причина</th><th>Создано</th><th>Истекает</th></tr></thead><tbody>{ips or '<tr><td colspan="4" class="empty">Список пуст</td></tr>'}</tbody></table></div></section>"""
