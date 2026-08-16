import asyncio
import ipaddress
import logging
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from aiohttp import web
from sqlalchemy import or_, select

from app.admin.auth import COOKIE_NAME, AdminAuth, AdminPrincipal
from app.admin.panels import security_page, settings_page, system_page
from app.admin.queries import AdminQueries
from app.admin.views import (
    audit_page,
    dashboard_page,
    layout,
    login_page,
    order_detail_page,
    orders_page,
    user_detail_page,
    users_page,
)
from app.config import Settings, get_settings
from app.database.models import BlockedIp
from app.database.session import (
    create_local_schema,
    create_session_factory,
    dispose_session_factory,
)
from app.logging_config import configure_logging
from app.services.container import Container, build_container
from app.services.exchange_rate import ExchangeRateService

logger = logging.getLogger(__name__)
PRINCIPAL_KEY: web.RequestKey[AdminPrincipal | None] = web.RequestKey("principal")


class AdminServer:
    def __init__(self, settings: Settings, container: Container, session_factory) -> None:
        self.settings = settings
        self.container = container
        self.session_factory = session_factory
        self.auth = AdminAuth(settings, session_factory)
        self.queries = AdminQueries(session_factory)

    def application(self) -> web.Application:
        app = web.Application(middlewares=[self.security_middleware])
        app.router.add_get("/favicon.ico", self.favicon)
        app.router.add_get("/admin/login", self.login_get)
        app.router.add_post("/admin/login", self.login_post)
        app.router.add_post("/admin/logout", self.logout_post)
        app.router.add_get("/admin", self.dashboard)
        app.router.add_get("/admin/orders", self.orders)
        app.router.add_get("/admin/orders/{order_number}", self.order_detail)
        app.router.add_post("/admin/orders/{order_number}/action", self.order_action)
        app.router.add_get("/admin/users", self.users)
        app.router.add_get("/admin/users/{telegram_id}", self.user_detail)
        app.router.add_post("/admin/users/{telegram_id}/action", self.user_action)
        app.router.add_get("/admin/security", self.security)
        app.router.add_post("/admin/security/ip-action", self.ip_action)
        app.router.add_get("/admin/audit", self.audit)
        app.router.add_get("/admin/system", self.system)
        app.router.add_post("/admin/system/control", self.system_control)
        app.router.add_get("/admin/settings", self.settings_view)
        app.router.add_get("/admin/api/star-price", self.star_price_api)
        static_dir = Path(__file__).with_name("static")
        app.router.add_static("/admin/static", static_dir, show_index=False)
        return app

    @web.middleware
    async def security_middleware(self, request: web.Request, handler):
        ip = self._client_ip(request)
        if await self._blocked_ip(ip):
            raise web.HTTPForbidden(text="Access denied")
        principal = await self.auth.principal(request.cookies.get(COOKIE_NAME), ip_address=ip)
        request[PRINCIPAL_KEY] = principal
        if request.path not in {"/admin/login", "/favicon.ico"} and not request.path.startswith(
            "/admin/static"
        ):
            if principal is None:
                raise web.HTTPFound("/admin/login")
            if request.method == "POST":
                form = await request.post()
                if form.get("csrf") != principal.csrf_token:
                    raise web.HTTPForbidden(text="Invalid CSRF token")
        response = await handler(request)
        response.headers.update(
            {
                "Content-Security-Policy": "default-src 'self'; style-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                "Cache-Control": "no-store",
            }
        )
        return response

    async def login_get(self, request: web.Request) -> web.Response:
        if request[PRINCIPAL_KEY]:
            raise web.HTTPFound("/admin")
        return self._html(login_page())

    async def favicon(self, request: web.Request) -> web.Response:
        return web.Response(status=204)

    async def login_post(self, request: web.Request) -> web.Response:
        form = await request.post()
        result = await self.auth.login(
            username=str(form.get("username", "")),
            password=str(form.get("password", "")),
            totp_code=str(form.get("totp", "")),
            ip_address=self._client_ip(request),
            user_agent=request.headers.get("User-Agent", ""),
        )
        if result is None:
            return self._html(
                login_page(error="Неверные данные или временная блокировка."), status=401
            )
        cookie_value, _ = result
        response = web.Response(status=302, headers={"Location": "/admin"})
        response.set_cookie(
            COOKIE_NAME,
            cookie_value,
            max_age=self.settings.admin_session_hours * 3600,
            httponly=True,
            secure=self.settings.admin_cookie_secure,
            samesite="Strict",
            path="/admin",
        )
        return response

    async def logout_post(self, request: web.Request) -> web.Response:
        principal = self._principal(request)
        await self.auth.logout(
            request.cookies.get(COOKIE_NAME), principal, self._client_ip(request)
        )
        response = web.Response(status=302, headers={"Location": "/admin/login"})
        response.del_cookie(COOKIE_NAME, path="/admin")
        return response

    async def dashboard(self, request: web.Request) -> web.Response:
        data = await self.queries.dashboard()
        data["health"] = await self._health()
        return self._page(request, "Обзор", dashboard_page(data), active="dashboard")

    async def orders(self, request: web.Request) -> web.Response:
        page = max(self._positive_int(request.query.get("page", "1")), 1)
        page_size = 50
        query, status, paid = (
            request.query.get("q", ""),
            request.query.get("status", ""),
            request.query.get("paid", ""),
        )
        filters = {
            key: request.query.get(key, "")
            for key in (
                "min_stars",
                "max_stars",
                "min_amount",
                "max_amount",
                "date_from",
                "date_to",
            )
        }
        rows = await self.queries.orders(
            query=query,
            status=status,
            paid=paid,
            min_stars=filters["min_stars"],
            max_stars=filters["max_stars"],
            min_amount=filters["min_amount"],
            max_amount=filters["max_amount"],
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            limit=page_size + 1,
            offset=(page - 1) * page_size,
        )
        has_next = len(rows) > page_size
        rows = rows[:page_size]
        return self._page(
            request,
            "Заказы",
            orders_page(
                rows,
                query=query,
                status=status,
                paid=paid,
                filters=filters,
                page=page,
                has_next=has_next,
            ),
            active="orders",
        )

    async def order_detail(self, request: web.Request) -> web.Response:
        data = await self.queries.order_detail(request.match_info["order_number"])
        if data is None:
            raise web.HTTPNotFound(text="Order not found")
        return self._page(
            request,
            f"Заказ {data['order'].order_number}",
            order_detail_page(data, self._principal(request)),
            active="orders",
        )

    async def order_action(self, request: web.Request) -> web.Response:
        principal = self._principal(request)
        self._require_manage(principal)
        form = await request.post()
        data = await self.queries.order_detail(request.match_info["order_number"])
        if data is None:
            raise web.HTTPNotFound(text="Order not found")
        order = data["order"]
        action = str(form.get("action", ""))
        expected_confirmation = (
            f"REFUND {order.order_number}" if action == "refund_yookassa" else "CONFIRM"
        )
        if form.get("confirmation") != expected_confirmation:
            raise web.HTTPBadRequest(text=f"Type {expected_confirmation}")
        actor = -principal.id
        try:
            if action == "retry":
                await self.container.order_service.safe_retry_order(
                    order.id, actor_telegram_id=actor
                )
            elif action == "reconcile":
                await self.container.order_service.reconcile_order(
                    order.id, actor_telegram_id=actor
                )
            elif action == "sync_payment":
                await self.container.order_service.sync_yookassa_payment(order.id)
            elif action == "refund_yookassa":
                await self.container.order_service.request_yookassa_refund(
                    order.id, actor_telegram_id=actor
                )
            elif action == "cancel":
                await self.container.order_service.admin_cancel_order(
                    order.id, actor_telegram_id=actor
                )
            elif action == "manual_review":
                await self.container.order_service.admin_move_to_manual_review(
                    order.id, actor_telegram_id=actor
                )
            elif action == "resend_notification":
                await self.container.order_service.admin_resend_notification(
                    order.id, actor_telegram_id=actor
                )
            else:
                raise web.HTTPBadRequest(text="Unknown action")
        except ValueError as exc:
            raise web.HTTPConflict(text=str(exc)) from exc
        await self.queries.web_audit(
            event=f"order_{action}",
            admin_id=principal.id,
            ip_address=self._client_ip(request),
            order_id=order.id,
            details={"order_number": order.order_number},
        )
        raise web.HTTPFound(f"/admin/orders/{order.order_number}?notice=Действие выполнено")

    async def users(self, request: web.Request) -> web.Response:
        query = request.query.get("q", "")
        rows = await self.queries.users(query=query)
        return self._page(request, "Пользователи", users_page(rows, query), active="users")

    async def user_detail(self, request: web.Request) -> web.Response:
        telegram_id = self._positive_int(request.match_info["telegram_id"])
        data = await self.queries.user_detail(telegram_id)
        if data is None:
            raise web.HTTPNotFound(text="User not found")
        return self._page(
            request,
            f"Пользователь {telegram_id}",
            user_detail_page(data, self._principal(request)),
            active="users",
        )

    async def user_action(self, request: web.Request) -> web.Response:
        principal = self._principal(request)
        self._require_manage(principal)
        telegram_id = self._positive_int(request.match_info["telegram_id"])
        form = await request.post()
        try:
            await self.queries.update_user(
                telegram_id=telegram_id,
                action=str(form.get("action", "")),
                reason=str(form.get("reason", "")),
                note=str(form.get("note", "")),
                admin_id=principal.id,
                ip_address=self._client_ip(request),
            )
        except ValueError as exc:
            query = urlencode({"error": str(exc)[:200]})
            raise web.HTTPFound(f"/admin/users/{telegram_id}?{query}") from exc
        raise web.HTTPFound(f"/admin/users/{telegram_id}?notice=Изменения сохранены")

    async def security(self, request: web.Request) -> web.Response:
        users, ips = await self.queries.blocklists()
        return self._page(
            request,
            "Блокировки",
            security_page(users, ips, self._principal(request)),
            active="security",
        )

    async def ip_action(self, request: web.Request) -> web.Response:
        principal = self._principal(request)
        self._require_manage(principal)
        form = await request.post()
        cidr = str(form.get("cidr", "")).strip()
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            raise web.HTTPBadRequest(text="Invalid IP/CIDR") from exc
        expected = "BLOCK_INTERNAL" if network.is_private or network.is_loopback else "CONFIRM"
        if form.get("confirmation") != expected:
            raise web.HTTPBadRequest(text=f"Type {expected}")
        try:
            await self.queries.update_ip_block(
                cidr=str(network),
                reason=str(form.get("reason", "")),
                action=str(form.get("action", "")),
                admin_id=principal.id,
                ip_address=self._client_ip(request),
            )
        except ValueError as exc:
            query = urlencode({"error": str(exc)[:200]})
            raise web.HTTPFound(f"/admin/security?{query}") from exc
        raise web.HTTPFound("/admin/security?notice=Blocklist обновлён")

    async def audit(self, request: web.Request) -> web.Response:
        return self._page(
            request, "Audit log", audit_page(await self.queries.audit_events()), active="audit"
        )

    async def system(self, request: web.Request) -> web.Response:
        maintenance, purchases = await self.container.order_service.get_runtime_controls()
        health = await self._health()
        body = system_page(
            health=health,
            maintenance=maintenance,
            purchases_enabled=purchases,
            principal=self._principal(request),
        )
        return self._page(request, "Система", body, active="system")

    async def system_control(self, request: web.Request) -> web.Response:
        principal = self._principal(request)
        if not principal.is_owner():
            raise web.HTTPForbidden(text="Owner role required")
        form = await request.post()
        if form.get("confirmation") != "CONFIRM":
            raise web.HTTPBadRequest(text="Type CONFIRM")
        key = str(form.get("key", ""))
        value = form.get("value") == "on"
        await self.container.order_service.set_runtime_control(
            key, value, actor_telegram_id=-principal.id
        )
        await self.queries.web_audit(
            event=f"{key}_{'enabled' if value else 'disabled'}",
            admin_id=principal.id,
            ip_address=self._client_ip(request),
            details={"value": value},
        )
        raise web.HTTPFound("/admin/system?notice=Настройка применена")

    async def settings_view(self, request: web.Request) -> web.Response:
        body = settings_page(config=self.settings, runtime=await self.queries.settings())
        return self._page(request, "Настройки", body, active="settings")

    async def star_price_api(self, request: web.Request) -> web.Response:
        return web.json_response(await self._star_price())

    async def _health(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "database": True,
            "telegram": None,
            "payment": "test" if self.settings.test_payment_mode else None,
            "worker": None,
            "version": "0.2.0",
            "operational_balance": self.settings.operational_balance_confirmed_stars,
            "star_price": None,
        }
        try:
            await self.queries.dashboard()
        except Exception:
            result["database"] = False
        result["fragment"] = None
        if self.container.fragment_client:
            try:
                health, price = await asyncio.gather(
                    asyncio.wait_for(self.container.fragment_client.check_health(), 8),
                    self._star_price(),
                )
                result["fragment"] = health.ok
                result["star_price"] = price
            except Exception:
                result["fragment"] = False
        if not self.settings.test_payment_mode:
            payment_monitor = self.container.payment_monitor
            if payment_monitor is None:
                result["payment"] = False
            else:
                result["payment"] = await payment_monitor.ton_service.check_health()
        runtime = await self.queries.settings()
        result["telegram"] = self._recent_heartbeat(runtime.get("notification_worker_heartbeat"))
        result["worker"] = self._recent_heartbeat(runtime.get("purchase_worker_heartbeat"))
        result["queue"] = (await self.container.order_service.stats()).get(
            "notifications_pending", 0
        )
        return result

    async def _star_price(self) -> dict[str, Any]:
        if not self.container.fragment_client:
            return {"ok": False, "error": "Провайдер Stars не настроен"}
        try:
            unit_price = await asyncio.wait_for(
                self.container.fragment_client.get_stars_unit_price(),
                8,
            )
            usd_rub_rate = Decimal(str(await ExchangeRateService().get_usd_rub_rate()))
            commission_multiplier = (
                Decimal("1") + unit_price.commission_percent / Decimal("100")
            )
            base_amount = (unit_price.amount / commission_multiplier).quantize(
                Decimal("0.0000000001"),
                rounding=ROUND_HALF_UP,
            )
            rub_per_star = (base_amount * usd_rub_rate).quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP,
            )
            rub_per_star_with_commission = (unit_price.amount * usd_rub_rate).quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP,
            )
            return {
                "ok": True,
                "amount": str(base_amount),
                "amount_with_commission": str(unit_price.amount),
                "currency": unit_price.currency,
                "commission_percent": str(unit_price.commission_percent),
                "rub_per_star": str(rub_per_star),
                "rub_per_star_with_commission": str(rub_per_star_with_commission),
                "usd_rub_rate": str(usd_rub_rate),
                "payment_method": self.settings.fragment_payment_method,
                "api_mode": self.settings.fragment_api_mode,
                "cached_at": unit_price.cached_at,
                "checked_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            logger.warning(
                "Fragment Stars price check failed",
                extra={"event": "fragment_price_check_failed", "error_type": exc.__class__.__name__},
            )
            return {
                "ok": False,
                "error": "Не удалось получить цену Fragment",
                "checked_at": datetime.now(UTC).isoformat(),
            }

    async def _blocked_ip(self, value: str) -> bool:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return True
        async with self.session_factory() as session:
            rows = list(
                await session.scalars(
                    select(BlockedIp).where(
                        or_(
                            BlockedIp.expires_at.is_(None), BlockedIp.expires_at > datetime.now(UTC)
                        )
                    )
                )
            )
        for row in rows:
            try:
                if address in ipaddress.ip_network(row.cidr, strict=False):
                    return True
            except ValueError:
                logger.warning("Invalid CIDR in blocklist", extra={"event": "invalid_block_cidr"})
        return False

    def _page(self, request: web.Request, title: str, body: str, *, active: str) -> web.Response:
        return self._html(
            layout(
                title,
                body,
                self._principal(request),
                active=active,
                notice=request.query.get("notice", "")[:200],
                error=request.query.get("error", "")[:200],
            )
        )

    @staticmethod
    def _html(value: str, status: int = 200) -> web.Response:
        return web.Response(text=value, content_type="text/html", charset="utf-8", status=status)

    @staticmethod
    def _principal(request: web.Request) -> AdminPrincipal:
        principal = request[PRINCIPAL_KEY]
        if not isinstance(principal, AdminPrincipal):
            raise web.HTTPUnauthorized()
        return principal

    @staticmethod
    def _require_manage(principal: AdminPrincipal) -> None:
        if not principal.can_manage():
            raise web.HTTPForbidden(text="Insufficient role")

    @staticmethod
    def _positive_int(value: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(text="Invalid ID") from exc
        if parsed <= 0:
            raise web.HTTPBadRequest(text="Invalid ID")
        return parsed

    @staticmethod
    def _client_ip(request: web.Request) -> str:
        peer = request.transport.get_extra_info("peername") if request.transport else None
        return str(peer[0]) if peer else "0.0.0.0"

    def _recent_heartbeat(self, value: str | None) -> bool:
        if not value:
            return False
        try:
            timestamp = datetime.fromisoformat(value)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            return datetime.now(UTC) - timestamp <= timedelta(
                seconds=self.settings.order_worker_interval_seconds * 3
            )
        except ValueError:
            return False


async def run() -> None:
    settings = get_settings()
    settings.validate_runtime()
    configure_logging(
        settings.log_level,
        secrets=(
            settings.database_url,
            settings.secret_value(settings.yookassa_secret_key) or "",
        ),
    )
    await create_local_schema(settings.database_url)
    session_factory = create_session_factory(settings.database_url)
    container = build_container(settings, session_factory)
    app = AdminServer(settings, container, session_factory).application()
    runner = web.AppRunner(app, access_log=logger)
    await runner.setup()
    site = web.TCPSite(runner, settings.admin_host, settings.admin_port)
    await site.start()
    logger.info(
        "Admin panel started", extra={"event": "admin_started", "port": settings.admin_port}
    )
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await dispose_session_factory(session_factory)


if __name__ == "__main__":
    asyncio.run(run())
