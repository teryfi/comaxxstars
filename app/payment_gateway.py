import asyncio
import logging
from contextlib import suppress
from hashlib import sha256
from uuid import uuid4

from aiohttp import web

from app.config import get_settings
from app.database.session import create_session_factory, dispose_session_factory
from app.logging_config import configure_logging
from app.payments.yookassa import YooKassaError
from app.services.container import Container, build_container

logger = logging.getLogger(__name__)
PAYMENT_EVENTS = frozenset(
    {
        "payment.waiting_for_capture",
        "payment.succeeded",
        "payment.canceled",
        "refund.succeeded",
    }
)


class PaymentGateway:
    def __init__(self, container: Container) -> None:
        self.container = container

    def application(self) -> web.Application:
        app = web.Application(client_max_size=self.container.settings.webhook_max_body_bytes)
        app.router.add_post("/webhooks/yookassa", self.yookassa_webhook)
        app.router.add_get("/payments/return", self.payment_return)
        app.router.add_get("/health", self.health)
        return app

    async def yookassa_webhook(self, request: web.Request) -> web.Response:
        correlation_id = self._correlation_id(request)
        peer = request.remote or "unknown"
        peer_key = int.from_bytes(sha256(peer.encode("utf-8")).digest()[:8], "big")
        if not await self.container.abuse_guard.allow(
            peer_key,
            "yookassa_webhook",
            limit=self.container.settings.webhook_rate_limit_per_minute,
        ):
            raise web.HTTPTooManyRequests(text="Rate limit exceeded")
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="application/json required")
        try:
            payload = await request.json()
        except (ValueError, web.HTTPRequestEntityTooLarge) as exc:
            raise web.HTTPBadRequest(text="Malformed notification") from exc
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="Malformed notification")
        event = payload.get("event")
        payment_object = payload.get("object")
        payment_id = payment_object.get("id") if isinstance(payment_object, dict) else None
        if event not in PAYMENT_EVENTS or not isinstance(payment_id, str) or not payment_id:
            raise web.HTTPBadRequest(text="Unsupported notification")
        try:
            if event == "refund.succeeded":
                status = await self.container.order_service.sync_yookassa_refund(payment_id[:128])
            else:
                status = await self.container.order_service.sync_yookassa_payment_by_provider_id(
                    payment_id[:128], webhook_event=str(event)
                )
        except ValueError:
            logger.warning(
                "YooKassa notification does not match an internal order",
                extra={
                    "event": "yookassa_webhook_unmatched",
                    "correlation_id": correlation_id,
                    "payment_id": payment_id[:128],
                },
            )
            return web.json_response({"ok": True})
        except YooKassaError as exc:
            logger.warning(
                "YooKassa notification verification failed",
                extra={
                    "event": "yookassa_webhook_verification_failed",
                    "correlation_id": correlation_id,
                    "error_type": exc.__class__.__name__,
                },
            )
            raise web.HTTPServiceUnavailable(text="Verification unavailable") from exc
        logger.info(
            "YooKassa notification verified",
            extra={
                "event": "yookassa_webhook_verified",
                "correlation_id": correlation_id,
                "payment_id": payment_id[:128],
                "order_status": status.value,
            },
        )
        return web.json_response({"ok": True})

    async def payment_return(self, request: web.Request) -> web.Response:
        return web.Response(
            content_type="text/html",
            text=(
                "<!doctype html><html lang='ru'><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>Платёж обрабатывается</title>"
                "<body style='font:18px system-ui;max-width:620px;margin:10vh auto;padding:24px'>"
                "<h1>Платёж обрабатывается</h1>"
                "<p>Вернитесь в Telegram и нажмите «Проверить оплату». "
                "Звёзды будут отправлены только после подтверждения YooKassa.</p></body></html>"
            ),
        )

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    @staticmethod
    def _correlation_id(request: web.Request) -> str:
        candidate = request.headers.get("X-Request-ID", "")
        if 0 < len(candidate) <= 64 and candidate.replace("-", "").isalnum():
            return candidate
        return str(uuid4())


async def run() -> None:
    settings = get_settings()
    settings.validate_runtime()
    if settings.process_role != "gateway":
        raise RuntimeError("Payment gateway requires PROCESS_ROLE=gateway")
    configure_logging(
        settings.log_level,
        secrets=(
            settings.database_url,
            settings.secret_value(settings.yookassa_secret_key) or "",
        ),
    )
    session_factory = create_session_factory(settings.database_url)
    container = build_container(settings, session_factory)
    runner = web.AppRunner(PaymentGateway(container).application(), access_log=logger)
    await runner.setup()
    site = web.TCPSite(runner, settings.webhook_host, settings.webhook_port)
    await site.start()
    logger.info(
        "Payment gateway started",
        extra={"event": "payment_gateway_started", "port": settings.webhook_port},
    )
    stop = asyncio.Event()
    try:
        await stop.wait()
    finally:
        with suppress(Exception):
            await runner.cleanup()
        await dispose_session_factory(session_factory)


if __name__ == "__main__":
    asyncio.run(run())
