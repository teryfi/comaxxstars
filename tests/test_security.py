import logging
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.bot.handlers.admin import _admin, show_order
from app.bot.handlers.orders import confirm_order
from app.bot.handlers.orders import test_payment as handle_test_payment
from app.logging_config import JsonFormatter, configure_logging
from app.services.abuse import AbuseGuard
from tests.factories import make_settings


def test_structured_logging_redacts_known_and_pattern_secrets() -> None:
    bot_token = "1234567890:abcdefghijklmnopqrstuvwxyzABCDEFGH"
    wallet_seed = "sensitive wallet seed value"
    formatter = JsonFormatter([bot_token, wallet_seed])
    try:
        raise RuntimeError(f"token={bot_token} seed={wallet_seed}")
    except RuntimeError:
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            f"request failed with {bot_token}",
            (),
            sys.exc_info(),
        )

    rendered = formatter.format(record)

    assert bot_token not in rendered
    assert wallet_seed not in rendered
    assert "REDACTED" in rendered
    assert '"severity":"ERROR"' in rendered


def test_noisy_dependency_info_logs_are_suppressed() -> None:
    configure_logging("INFO")

    assert logging.getLogger("aiogram.event").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("telethon.client.updates").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("aiohttp.access").getEffectiveLevel() == logging.WARNING


async def test_rate_limit_rejects_requests_over_window() -> None:
    guard = AbuseGuard(default_limit=2, window_seconds=60)
    assert await guard.allow(10, "quote") is True
    assert await guard.allow(10, "quote") is True
    assert await guard.allow(10, "quote") is False
    assert await guard.allow(11, "quote") is True


def test_admin_authorization_uses_numeric_allowlist() -> None:
    message = MagicMock()
    container = MagicMock()
    container.settings.admin_ids = [100]
    message.from_user.id = 100
    message.from_user.username = "not_relevant"
    assert _admin(message, container) is True
    message.from_user.id = 101
    message.from_user.username = "admin"
    assert _admin(message, container) is False


def test_single_numeric_admin_id_from_environment_is_accepted() -> None:
    settings = make_settings(ADMIN_IDS=100)
    assert settings.admin_ids == [100]


@pytest.mark.parametrize("value", [0, -1])
def test_nonpositive_admin_id_is_rejected(value: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(ADMIN_IDS=value)


def test_fragment_url_cannot_be_redirected_to_ssrf_target() -> None:
    with pytest.raises(ValidationError):
        make_settings(FRAGMENT_API_BASE_URL="https://127.0.0.1/internal")


def test_ssh_tunnel_admin_access_is_accepted() -> None:
    settings = make_settings(
        ADMIN_ACCESS_MODE="ssh_tunnel",
        ADMIN_PUBLIC_URL="http://127.0.0.1:8080",
        ADMIN_COOKIE_SECURE=False,
    )

    settings.validate_runtime()

    assert settings.admin_access_is_protected is True


@pytest.mark.parametrize(
    ("url", "cookie_secure"),
    [
        ("http://0.0.0.0:8080", False),
        ("http://admin.example.com", False),
        ("http://127.0.0.1:8080", True),
    ],
)
def test_ssh_tunnel_admin_access_rejects_unsafe_origin(url: str, cookie_secure: bool) -> None:
    settings = make_settings(
        ADMIN_ACCESS_MODE="ssh_tunnel",
        ADMIN_PUBLIC_URL=url,
        ADMIN_COOKIE_SECURE=cookie_secure,
    )

    with pytest.raises(RuntimeError, match="ADMIN_ACCESS_MODE"):
        settings.validate_runtime()


def test_bot_role_does_not_require_wallet_seed() -> None:
    settings = make_settings(
        PROCESS_ROLE="bot",
        DATABASE_URL="postgresql+asyncpg://user:pass@database/terstars",
        TEST_PAYMENT_MODE=False,
        REAL_STARS_PURCHASE_ENABLED=True,
        STARS_PURCHASE_PROVIDER="fragment",
        TON_WALLET_ADDRESS="wallet",
        FRAGMENT_WALLET_SEED=None,
    )
    settings.validate_runtime()


@pytest.mark.parametrize("role", ["bot", "admin"])
def test_non_worker_production_role_rejects_wallet_seed(role: str) -> None:
    settings = make_settings(
        PROCESS_ROLE=role,
        DATABASE_URL="postgresql+asyncpg://user:pass@database/terstars",
        TEST_PAYMENT_MODE=False,
        REAL_STARS_PURCHASE_ENABLED=True,
        STARS_PURCHASE_PROVIDER="fragment",
        TON_WALLET_ADDRESS="wallet",
        FRAGMENT_WALLET_SEED=" ".join(f"word{i}" for i in range(24)),
    )

    with pytest.raises(RuntimeError, match="only to the purchase worker"):
        settings.validate_runtime()


@pytest.mark.parametrize("role", ["bot", "admin", "gateway"])
def test_non_worker_production_role_rejects_fragment_kyc_secrets(role: str) -> None:
    settings = make_settings(
        PROCESS_ROLE=role,
        DATABASE_URL="postgresql+asyncpg://user:pass@database/terstars",
        TEST_PAYMENT_MODE=False,
        REAL_STARS_PURCHASE_ENABLED=True,
        STARS_PURCHASE_PROVIDER="fragment",
        FRAGMENT_API_MODE="kyc",
        FRAGMENT_COOKIES="secret-cookie-bundle",
    )

    with pytest.raises(RuntimeError, match="only to the purchase worker"):
        settings.validate_runtime()


def test_purchase_worker_refuses_to_start_without_wallet_seed() -> None:
    settings = make_settings(
        PROCESS_ROLE="worker",
        DATABASE_URL="postgresql+asyncpg://user:pass@database/terstars",
        TEST_PAYMENT_MODE=False,
        REAL_STARS_PURCHASE_ENABLED=True,
        STARS_PURCHASE_PROVIDER="fragment",
        TON_WALLET_ADDRESS="wallet",
        FRAGMENT_WALLET_SEED=None,
    )
    with pytest.raises(RuntimeError, match="FRAGMENT_WALLET_SEED"):
        settings.validate_runtime()


def test_simulated_payment_can_never_trigger_real_purchase() -> None:
    settings = make_settings(
        TEST_PAYMENT_MODE=True,
        REAL_STARS_PURCHASE_ENABLED=True,
        STARS_PURCHASE_PROVIDER="fragment",
    )
    with pytest.raises(RuntimeError, match="simulated payment"):
        settings.validate_runtime()


def test_yookassa_test_shop_can_never_spend_real_fragment_funds() -> None:
    settings = make_settings(
        PROCESS_ROLE="bot",
        DATABASE_URL="postgresql+asyncpg://user:pass@database/terstars",
        TEST_PAYMENT_MODE=False,
        CUSTOMER_PAYMENT_PROVIDER="yookassa",
        YOOKASSA_TEST_MODE=True,
        REAL_STARS_PURCHASE_ENABLED=True,
        STARS_PURCHASE_PROVIDER="fragment",
        YOOKASSA_SHOP_ID="test-shop",
        YOOKASSA_SECRET_KEY="test-secret",
        YOOKASSA_RETURN_URL="https://pay.example.test/payments/return",
        YOOKASSA_WEBHOOK_URL="https://pay.example.test/webhooks/yookassa",
        YOOKASSA_RECEIPT_MODE="disabled_by_contract",
    )

    with pytest.raises(RuntimeError, match="test shop"):
        settings.validate_runtime()


def test_yookassa_endpoint_cannot_be_redirected_to_ssrf_target() -> None:
    with pytest.raises(ValidationError):
        make_settings(YOOKASSA_API_BASE_URL="https://127.0.0.1/v3")


def test_yookassa_test_shop_configuration_is_safe_and_valid() -> None:
    settings = make_settings(
        PROCESS_ROLE="bot",
        DATABASE_URL="postgresql+asyncpg://user:pass@postgres/terstars",
        TEST_PAYMENT_MODE=False,
        CUSTOMER_PAYMENT_PROVIDER="yookassa",
        YOOKASSA_TEST_MODE=True,
        REAL_STARS_PURCHASE_ENABLED=False,
        STARS_PURCHASE_PROVIDER="fragment",
        YOOKASSA_SHOP_ID="test-shop",
        YOOKASSA_SECRET_KEY="test-secret",
        YOOKASSA_RETURN_URL="https://pay.example.test/payments/return",
        YOOKASSA_WEBHOOK_URL="https://pay.example.test/webhooks/yookassa",
        YOOKASSA_RECEIPT_MODE="disabled_by_contract",
        ADMIN_ACCESS_MODE="ssh_tunnel",
        ADMIN_PUBLIC_URL="http://127.0.0.1:8080",
        ADMIN_COOKIE_SECURE=False,
    )

    settings.validate_runtime()


def test_secret_file_is_loaded_without_placing_value_in_env(tmp_path) -> None:
    secret_file = tmp_path / "bot-token"
    secret_file.write_text("1234567890:file-backed-secret-token", encoding="utf-8")
    settings = make_settings(BOT_TOKEN="", BOT_TOKEN_FILE=secret_file)
    assert settings.bot_token.get_secret_value() == "1234567890:file-backed-secret-token"


def test_production_refuses_combined_bot_and_wallet_process() -> None:
    settings = make_settings(
        PROCESS_ROLE="all",
        DATABASE_URL="postgresql+asyncpg://user:pass@database/terstars",
        TEST_PAYMENT_MODE=False,
        REAL_STARS_PURCHASE_ENABLED=True,
        STARS_PURCHASE_PROVIDER="fragment",
        TON_WALLET_ADDRESS="wallet",
        FRAGMENT_WALLET_SEED=" ".join(f"word{i}" for i in range(24)),
    )
    with pytest.raises(RuntimeError, match="separate PROCESS_ROLE"):
        settings.validate_runtime()


async def test_forged_confirmation_callback_cannot_create_order() -> None:
    callback = MagicMock()
    callback.data = "order:confirm:forged"
    callback.from_user.id = 10
    callback.answer = AsyncMock()
    state = MagicMock()
    state.get_data = AsyncMock(return_value={"quote_token": "real", "buyer_id": 10})
    container = MagicMock()
    container.order_service.create_order = AsyncMock()

    await confirm_order(callback, state, container)

    callback.answer.assert_awaited_once_with("Подтверждение устарело", show_alert=True)
    container.order_service.create_order.assert_not_awaited()


async def test_another_user_cannot_confirm_test_payment() -> None:
    callback = MagicMock()
    callback.data = "payment:test:123"
    callback.from_user.id = 11
    callback.answer = AsyncMock()
    container = MagicMock()
    container.order_service.is_order_owner = AsyncMock(return_value=False)
    container.order_service.confirm_test_payment = AsyncMock()

    await handle_test_payment(callback, container)

    callback.answer.assert_awaited_once_with("Заказ недоступен", show_alert=True)
    container.order_service.confirm_test_payment.assert_not_awaited()


async def test_unauthorized_admin_handler_does_not_query_orders() -> None:
    message = MagicMock()
    message.from_user.id = 999
    message.answer = AsyncMock()
    container = MagicMock()
    container.settings.admin_ids = [1]
    container.order_service.get_order_summary = AsyncMock()

    await show_order(message, container)

    message.answer.assert_awaited_once_with("Команда недоступна.")
    container.order_service.get_order_summary.assert_not_awaited()
