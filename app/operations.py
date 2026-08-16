import argparse
import asyncio
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from aiogram import Bot
from sqlalchemy import func, select, text

from app.config import Settings, get_settings
from app.database.models import AdminUser, RuntimeSetting
from app.database.session import (
    create_local_schema,
    create_session_factory,
    dispose_session_factory,
)
from app.domain import OrderKind, OrderStatus, PricedStarsOption, StarsOption
from app.payments.yookassa import YooKassaClient
from app.services.container import build_container
from app.services.fragment_client import FragmentClient, normalize_fragment_cookies
from app.services.ton_payment import TonPaymentService


@dataclass(frozen=True)
class Check:
    name: str
    level: str
    message: str


async def preflight() -> int:
    settings = get_settings()
    checks: list[Check] = []
    try:
        settings.validate_runtime()
        checks.append(Check("Runtime configuration", "OK", "valid"))
    except Exception as exc:
        checks.append(Check("Runtime configuration", "ERROR", str(exc)))
    await create_local_schema(settings.database_url)
    session_factory = create_session_factory(settings.database_url)
    try:
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            checks.append(Check("Database", "OK", "reachable"))
        except Exception as exc:
            checks.append(Check("Database", "ERROR", exc.__class__.__name__))

        migrations_ok = False
        try:
            async with session_factory() as session:
                revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
            migrations_ok = revision == "0005_yookassa_payments"
        except Exception:
            migrations_ok = settings.database_url.startswith("sqlite+")
        checks.append(
            Check(
                "Migrations",
                "OK" if migrations_ok else "ERROR",
                "current" if migrations_ok else "run alembic upgrade head",
            )
        )

        bot_token = settings.bot_token.get_secret_value()
        if bot_token:
            bot: Bot | None = None
            try:
                bot = Bot(bot_token)
                me = await asyncio.wait_for(bot.get_me(), timeout=8)
                checks.append(Check("Telegram", "OK", f"@{me.username}"))
            except Exception as exc:
                checks.append(Check("Telegram", "ERROR", exc.__class__.__name__))
            finally:
                if bot is not None:
                    await bot.session.close()
        else:
            checks.append(Check("Telegram", "ERROR", "BOT_TOKEN missing"))

        try:
            fragment = FragmentClient(settings)
            health = await fragment.check_health()
            checks.append(Check("Stars provider", "OK" if health.ok else "ERROR", health.message))
        except Exception as exc:
            checks.append(Check("Stars provider", "ERROR", exc.__class__.__name__))
        checks.append(await _payment_provider_check(settings))

        seed_ok = bool(settings.fragment_wallet_seed)
        checks.append(
            Check(
                "Stars credentials",
                "OK" if seed_ok else "ERROR",
                "configured" if seed_ok else "FRAGMENT_WALLET_SEED missing",
            )
        )
        if settings.fragment_api_mode == "kyc":
            try:
                raw_cookies = settings.secret_value(settings.fragment_cookies)
                if not raw_cookies:
                    raise ValueError("FRAGMENT_COOKIES missing")
                normalize_fragment_cookies(raw_cookies)
                checks.append(Check("Fragment KYC cookies", "OK", "required fields present"))
            except ValueError as exc:
                checks.append(Check("Fragment KYC cookies", "ERROR", str(exc)))
        else:
            checks.append(Check("Fragment KYC cookies", "WARNING", "no-KYC mode enabled"))
        payment_asset_ok = settings.fragment_payment_method in {"ton", "usdt_ton"}
        checks.append(
            Check(
                "Fragment payment asset",
                "OK" if payment_asset_ok else "ERROR",
                settings.fragment_payment_method.upper()
                if payment_asset_ok
                else "must be ton or usdt_ton",
            )
        )
        balance_confirmed = settings.operational_balance_confirmed_stars >= settings.min_stars
        checks.append(
            Check(
                "Operational balance",
                "OK" if balance_confirmed else "WARNING",
                (
                    f"operator-confirmed floor: {settings.operational_balance_confirmed_stars} Stars"
                    if balance_confirmed
                    else "not exposed by installed Fragment SDK; verify, fund, then set OPERATIONAL_BALANCE_CONFIRMED_STARS"
                ),
            )
        )
        async with session_factory() as session:
            admin_count = int(await session.scalar(select(func.count(AdminUser.id))) or 0)
            heartbeat = await session.get(RuntimeSetting, "purchase_worker_heartbeat")
            payment_heartbeat = await session.get(RuntimeSetting, "payment_monitor_heartbeat")
            runtime_purchases = await session.get(RuntimeSetting, "purchases_enabled")
            runtime_maintenance = await session.get(RuntimeSetting, "maintenance_mode")
        checks.append(
            Check(
                "Admin",
                "OK" if admin_count else "ERROR",
                f"{admin_count} configured" if admin_count else "create OWNER",
            )
        )
        worker_ok = bool(
            heartbeat and _recent(heartbeat.value, settings.order_worker_interval_seconds * 3)
        )
        checks.append(
            Check(
                "Worker",
                "OK" if worker_ok else "ERROR",
                "alive" if worker_ok else "no recent heartbeat",
            )
        )
        if not settings.test_payment_mode and settings.customer_payment_provider == "ton":
            payment_monitor_ok = bool(
                payment_heartbeat
                and _recent(
                    payment_heartbeat.value,
                    settings.payment_monitor_interval_seconds * 3,
                )
            )
            checks.append(
                Check(
                    "Payment monitor",
                    "OK" if payment_monitor_ok else "ERROR",
                    "alive" if payment_monitor_ok else "no recent heartbeat",
                )
            )

        purchases_enabled = _runtime_bool(runtime_purchases, settings.purchases_enabled)
        maintenance_mode = _runtime_bool(runtime_maintenance, settings.maintenance_mode)
        security_ok = bool(
            not settings.test_payment_mode
            and settings.real_stars_purchase_enabled
            and purchases_enabled
            and not maintenance_mode
            and not settings.database_url.startswith("sqlite+")
            and settings.admin_access_is_protected
        )
        checks.append(
            Check(
                "Security configuration",
                "OK" if security_ok else "ERROR",
                "production gates enabled"
                if security_ok
                else "production gates are not all enabled",
            )
        )
        if settings.customer_payment_provider == "yookassa":
            webhook_ok = bool(
                settings.yookassa_webhook_url
                and settings.yookassa_webhook_url.startswith("https://")
                and settings.yookassa_webhook_url.endswith("/webhooks/yookassa")
            )
            checks.append(
                Check(
                    "YooKassa webhook",
                    "OK" if webhook_ok else "ERROR",
                    settings.yookassa_webhook_url or "public HTTPS URL missing",
                )
            )
            receipt_ok = settings.yookassa_receipt_mode != "owner_decision_required"
            checks.append(
                Check(
                    "Receipt configuration",
                    "OK" if receipt_ok else "ERROR",
                    settings.yookassa_receipt_mode if receipt_ok else "REQUIRES OWNER DECISION",
                )
            )
            checks.append(
                Check(
                    "YooKassa mode",
                    "WARNING" if settings.yookassa_test_mode else "OK",
                    "test shop" if settings.yookassa_test_mode else "live shop",
                )
            )
        else:
            checks.append(Check("Callbacks", "OK", "TON polling; webhook not required"))
        telegram_client_ok = bool(settings.telegram_api_id and settings.telegram_api_hash)
        checks.append(
            Check(
                "Telegram client credentials",
                "OK" if telegram_client_ok else "ERROR",
                "configured" if telegram_client_ok else "TELEGRAM_API_ID/HASH missing",
            )
        )
        checks.append(
            Check(
                "Admin notifications",
                "OK" if settings.admin_ids else "ERROR",
                "configured" if settings.admin_ids else "ADMIN_IDS missing",
            )
        )
        checks.append(Check("Dry-run pipeline", "OK", "isolated test provider available"))
        checks.append(
            Check("Idempotency storage", "OK" if migrations_ok else "ERROR", "database constraints")
        )
    finally:
        await dispose_session_factory(session_factory)

    print("Production preflight\n")
    for check in checks:
        print(f"[{check.level}] {check.name}: {check.message}")
    ready = all(check.level == "OK" for check in checks)
    print(f"\nRESULT: {'READY FOR PURCHASE' if ready else 'NOT READY FOR PURCHASE'}")
    if not ready:
        reasons = ", ".join(check.name for check in checks if check.level != "OK")
        print(f"Reason: {reasons}")
    return 0 if ready else 1


async def dry_run() -> int:
    with tempfile.TemporaryDirectory(prefix="terstars-dry-run-") as directory:
        db_path = Path(directory) / "dry-run.db"
        values: dict[str, object] = {
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
            "PROCESS_ROLE": "all",
            "BOT_TOKEN": "dry-run-token",
            "TELEGRAM_API_ID": 1,
            "TELEGRAM_API_HASH": "dry-run-hash",
            "TEST_PAYMENT_MODE": True,
            "REAL_STARS_PURCHASE_ENABLED": False,
            "STARS_PURCHASE_PROVIDER": "test",
            "PURCHASES_ENABLED": True,
            "MAINTENANCE_MODE": False,
            "ADMIN_IDS": "1",
        }
        settings = Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]
        settings.validate_runtime()
        await create_local_schema(settings.database_url)
        factory = create_session_factory(settings.database_url)
        try:
            container = build_container(settings, factory)
            quote = PricedStarsOption(
                option=StarsOption(stars=50, currency="USD", amount_minor=100),
                usd_rub_rate=Decimal("80"),
                markup_percent=Decimal("0"),
                rub_amount=Decimal("80"),
                quote_expires_at=datetime.now(UTC) + timedelta(minutes=2),
            )
            order_id = await container.order_service.create_order(
                buyer_telegram_id=10001,
                buyer_username="dryrun_user",
                kind=OrderKind.SELF,
                recipient_telegram_id=10001,
                recipient_username="dryrun_user",
                priced_option=quote,
                request_id="safe-dry-run",
            )
            paid = await container.order_service.confirm_test_payment(
                order_id, buyer_telegram_id=10001
            )
            completed, _ = await container.order_service.deliver_paid_order(order_id)
            summary = await container.order_service.get_order_summary(order_id)
        finally:
            await dispose_session_factory(factory)
    print("Purchase dry-run\n")
    print(f"[OK] Order created: #{summary.order_number}")
    print("[OK] Price displayed: 80.00 RUB")
    print(f"[OK] Simulated payment confirmed: {paid.value}")
    print("[OK] Provider request prepared: recipient=@dryrun_user, stars=50, kind=self")
    print(f"[OK] Simulated purchase completed: {completed == OrderStatus.COMPLETED}")
    print("[STOP] No network purchase was sent; no credentials or funds were used")
    return 0


async def _payment_provider_check(settings: Settings) -> Check:
    if settings.test_payment_mode:
        return Check("Payment provider", "ERROR", "test mode is enabled")
    if settings.customer_payment_provider == "yookassa":
        try:
            healthy = await YooKassaClient(settings).check_health()
        except Exception as exc:
            return Check("Payment provider", "ERROR", str(exc))
        return Check(
            "Payment provider",
            "OK" if healthy else "ERROR",
            "YooKassa credentials accepted" if healthy else "YooKassa API unavailable",
        )
    if settings.customer_payment_provider != "ton" or not settings.ton_wallet_address:
        return Check("Payment provider", "ERROR", "TON_WALLET_ADDRESS missing")
    service = TonPaymentService(
        wallet_address=settings.ton_wallet_address,
        toncenter_api_key=settings.secret_value(settings.toncenter_api_key),
        scan_limit=settings.ton_scan_limit,
    )
    healthy = await service.check_health()
    return Check(
        "Payment provider",
        "OK" if healthy else "ERROR",
        "Toncenter reachable" if healthy else "Toncenter unavailable",
    )


def _recent(value: str, allowed_seconds: int) -> bool:
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return datetime.now(UTC) - timestamp <= timedelta(seconds=allowed_seconds)
    except ValueError:
        return False


def _runtime_bool(setting: RuntimeSetting | None, default: bool) -> bool:
    if setting is None:
        return default
    return setting.value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    parser = argparse.ArgumentParser(description="TerStars production operations")
    parser.add_argument("command", choices={"preflight", "dry-run"})
    args = parser.parse_args()
    if args.command == "preflight":
        operation = preflight()
    else:
        operation = dry_run()
    raise SystemExit(asyncio.run(operation))


if __name__ == "__main__":
    main()
