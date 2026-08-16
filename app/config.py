import os
import stat
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    bot_token: SecretStr = Field(default=SecretStr(""), alias="BOT_TOKEN")
    bot_token_file: Path | None = Field(default=None, alias="BOT_TOKEN_FILE")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./terstars.db",
        alias="DATABASE_URL",
    )
    database_url_file: Path | None = Field(default=None, alias="DATABASE_URL_FILE")
    process_role: str = Field(default="all", alias="PROCESS_ROLE")
    admin_host: str = Field(default="127.0.0.1", alias="ADMIN_HOST")
    admin_port: int = Field(default=8080, ge=1, le=65535, alias="ADMIN_PORT")
    admin_public_url: str = Field(default="http://127.0.0.1:8080", alias="ADMIN_PUBLIC_URL")
    admin_cookie_secure: bool = Field(default=False, alias="ADMIN_COOKIE_SECURE")
    admin_access_mode: str = Field(default="local", alias="ADMIN_ACCESS_MODE")
    admin_session_hours: int = Field(default=8, ge=1, le=168, alias="ADMIN_SESSION_HOURS")
    admin_login_attempts: int = Field(default=5, ge=2, le=20, alias="ADMIN_LOGIN_ATTEMPTS")
    admin_login_window_seconds: int = Field(
        default=900, ge=60, le=86400, alias="ADMIN_LOGIN_WINDOW_SECONDS"
    )

    telegram_api_id: int | None = Field(default=None, alias="TELEGRAM_API_ID")
    telegram_api_hash: SecretStr | None = Field(default=None, alias="TELEGRAM_API_HASH")
    telegram_api_hash_file: Path | None = Field(default=None, alias="TELEGRAM_API_HASH_FILE")
    telegram_user_phone: str | None = Field(default=None, alias="TELEGRAM_USER_PHONE")
    telegram_session_dir: Path = Field(default=Path("./sessions"), alias="TELEGRAM_SESSION_DIR")
    telegram_session_name: str = Field(default="stars_user", alias="TELEGRAM_SESSION_NAME")

    admin_ids: list[int] = Field(default_factory=list, alias="ADMIN_IDS")
    stars_markup_percent: Decimal = Field(
        default=Decimal("0"), ge=0, le=100, alias="STARS_MARKUP_PERCENT"
    )
    stars_standard_order_markup_percent: Decimal | None = Field(
        default=None, ge=0, le=100, alias="STARS_STANDARD_ORDER_MARKUP_PERCENT"
    )
    stars_standard_order_threshold: int = Field(
        default=100, ge=50, le=1_000_000, alias="STARS_STANDARD_ORDER_THRESHOLD"
    )
    stars_large_order_markup_percent: Decimal | None = Field(
        default=None, ge=0, le=100, alias="STARS_LARGE_ORDER_MARKUP_PERCENT"
    )
    stars_large_order_threshold: int = Field(
        default=10_000, ge=50, le=1_000_000, alias="STARS_LARGE_ORDER_THRESHOLD"
    )
    usd_rub_source: str = Field(default="cbr", alias="USD_RUB_SOURCE")
    min_stars: int = Field(default=50, ge=50, alias="MIN_STARS")
    max_stars: int = Field(default=100_000, ge=50, le=1_000_000, alias="MAX_STARS")
    popular_stars_amounts: list[int] = Field(
        default_factory=lambda: [50, 100, 250, 500, 1000],
        alias="POPULAR_STARS_AMOUNTS",
    )
    quote_ttl_seconds: int = Field(default=120, ge=30, le=900, alias="QUOTE_TTL_SECONDS")

    test_payment_mode: bool = Field(default=True, alias="TEST_PAYMENT_MODE")
    test_payment_status_delay_seconds: int = Field(
        default=5, ge=0, le=300, alias="TEST_PAYMENT_STATUS_DELAY_SECONDS"
    )
    customer_payment_provider: str = Field(default="ton", alias="CUSTOMER_PAYMENT_PROVIDER")
    real_stars_purchase_enabled: bool = Field(default=False, alias="REAL_STARS_PURCHASE_ENABLED")
    stars_purchase_provider: str = Field(default="fragment", alias="STARS_PURCHASE_PROVIDER")
    purchases_enabled: bool = Field(default=False, alias="PURCHASES_ENABLED")
    maintenance_mode: bool = Field(default=True, alias="MAINTENANCE_MODE")

    yookassa_api_base_url: str = Field(
        default="https://api.yookassa.ru/v3",
        alias="YOOKASSA_API_BASE_URL",
    )
    yookassa_shop_id: str | None = Field(default=None, alias="YOOKASSA_SHOP_ID")
    yookassa_shop_id_file: Path | None = Field(default=None, alias="YOOKASSA_SHOP_ID_FILE")
    yookassa_secret_key: SecretStr | None = Field(default=None, alias="YOOKASSA_SECRET_KEY")
    yookassa_secret_key_file: Path | None = Field(default=None, alias="YOOKASSA_SECRET_KEY_FILE")
    yookassa_return_url: str | None = Field(default=None, alias="YOOKASSA_RETURN_URL")
    yookassa_webhook_url: str | None = Field(default=None, alias="YOOKASSA_WEBHOOK_URL")
    yookassa_test_mode: bool = Field(default=True, alias="YOOKASSA_TEST_MODE")
    yookassa_receipt_mode: str = Field(
        default="owner_decision_required", alias="YOOKASSA_RECEIPT_MODE"
    )
    yookassa_timeout_seconds: int = Field(default=15, ge=3, le=60, alias="YOOKASSA_TIMEOUT_SECONDS")
    webhook_host: str = Field(default="0.0.0.0", alias="WEBHOOK_HOST")
    webhook_port: int = Field(default=8090, ge=1, le=65535, alias="WEBHOOK_PORT")
    webhook_max_body_bytes: int = Field(
        default=65536, ge=1024, le=1048576, alias="WEBHOOK_MAX_BODY_BYTES"
    )
    webhook_rate_limit_per_minute: int = Field(
        default=120, ge=10, le=10000, alias="WEBHOOK_RATE_LIMIT_PER_MINUTE"
    )

    fragment_api_base_url: str = Field(
        default="https://api-fragment.duckdns.org",
        alias="FRAGMENT_API_BASE_URL",
    )
    fragment_wallet_seed: SecretStr | None = Field(default=None, alias="FRAGMENT_WALLET_SEED")
    fragment_wallet_seed_file: Path | None = Field(default=None, alias="FRAGMENT_WALLET_SEED_FILE")
    fragment_payment_method: str = Field(default="usdt_ton", alias="FRAGMENT_PAYMENT_METHOD")
    fragment_api_mode: str = Field(default="kyc", alias="FRAGMENT_API_MODE")
    fragment_cookies: SecretStr | None = Field(default=None, alias="FRAGMENT_COOKIES")
    fragment_cookies_file: Path | None = Field(default=None, alias="FRAGMENT_COOKIES_FILE")
    fragment_local_storage: SecretStr | None = Field(default=None, alias="FRAGMENT_LOCAL_STORAGE")
    fragment_local_storage_file: Path | None = Field(
        default=None,
        alias="FRAGMENT_LOCAL_STORAGE_FILE",
    )
    fragment_poll_timeout_seconds: int = Field(
        default=300, ge=30, le=1800, alias="FRAGMENT_POLL_TIMEOUT_SECONDS"
    )
    operational_balance_confirmed_stars: int = Field(
        default=0,
        ge=0,
        le=100_000_000,
        alias="OPERATIONAL_BALANCE_CONFIRMED_STARS",
    )

    ton_wallet_address: str | None = Field(default=None, alias="TON_WALLET_ADDRESS")
    toncenter_api_key: SecretStr | None = Field(default=None, alias="TONCENTER_API_KEY")
    toncenter_api_key_file: Path | None = Field(default=None, alias="TONCENTER_API_KEY_FILE")
    order_timeout_minutes: int = Field(default=10, ge=2, le=1440, alias="ORDER_TIMEOUT_MINUTES")
    payment_confirmation_seconds: int = Field(
        default=20, ge=0, le=600, alias="PAYMENT_CONFIRMATION_SECONDS"
    )
    payment_monitor_interval_seconds: int = Field(
        default=15, ge=3, le=300, alias="PAYMENT_MONITOR_INTERVAL_SECONDS"
    )
    provider_health_interval_seconds: int = Field(
        default=60,
        ge=15,
        le=3600,
        alias="PROVIDER_HEALTH_INTERVAL_SECONDS",
    )
    order_worker_interval_seconds: int = Field(
        default=5, ge=2, le=300, alias="ORDER_WORKER_INTERVAL_SECONDS"
    )
    worker_batch_size: int = Field(default=100, ge=1, le=1000, alias="WORKER_BATCH_SIZE")
    payment_monitor_batch_size: int = Field(
        default=200, ge=1, le=2000, alias="PAYMENT_MONITOR_BATCH_SIZE"
    )
    notification_retry_limit: int = Field(
        default=10, ge=1, le=100, alias="NOTIFICATION_RETRY_LIMIT"
    )
    notification_max_backoff_seconds: int = Field(
        default=300,
        ge=1,
        le=3600,
        alias="NOTIFICATION_MAX_BACKOFF_SECONDS",
    )
    ton_scan_limit: int = Field(default=100, ge=20, le=1000, alias="TON_SCAN_LIMIT")
    late_payment_scan_hours: int = Field(default=24, ge=1, le=168, alias="LATE_PAYMENT_SCAN_HOURS")

    rate_limit_requests: int = Field(default=30, ge=1, le=1000, alias="RATE_LIMIT_REQUESTS")
    rate_limit_window_seconds: int = Field(
        default=60, ge=1, le=3600, alias="RATE_LIMIT_WINDOW_SECONDS"
    )
    order_create_limit: int = Field(default=5, ge=1, le=100, alias="ORDER_CREATE_LIMIT")
    price_quote_limit: int = Field(default=10, ge=1, le=100, alias="PRICE_QUOTE_LIMIT")
    payment_check_limit: int = Field(default=10, ge=1, le=100, alias="PAYMENT_CHECK_LIMIT")
    max_open_orders_per_user: int = Field(default=3, ge=1, le=100, alias="MAX_OPEN_ORDERS_PER_USER")
    max_order_rub_amount: Decimal = Field(
        default=Decimal("500000"),
        gt=0,
        le=Decimal("100000000"),
        alias="MAX_ORDER_RUB_AMOUNT",
    )
    max_payment_ton: Decimal = Field(
        default=Decimal("1000"),
        gt=0,
        le=Decimal("1000000"),
        alias="MAX_PAYMENT_TON",
    )
    daily_stars_limit_per_user: int = Field(
        default=20_000, ge=50, le=10_000_000, alias="DAILY_STARS_LIMIT_PER_USER"
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("telegram_api_id", mode="before")
    @classmethod
    def parse_optional_int(cls, value: str | int | None) -> int | None:
        if value == "" or value is None:
            return None
        return int(value)

    @field_validator(
        "telegram_api_hash",
        "fragment_wallet_seed",
        "fragment_cookies",
        "fragment_local_storage",
        "toncenter_api_key",
        "yookassa_secret_key",
        mode="before",
    )
    @classmethod
    def parse_optional_secret(cls, value: str | SecretStr | None) -> SecretStr | None:
        if value == "" or value is None:
            return None
        return value if isinstance(value, SecretStr) else SecretStr(value)

    @field_validator(
        "telegram_user_phone",
        "ton_wallet_address",
        "yookassa_shop_id",
        "yookassa_return_url",
        "yookassa_webhook_url",
        mode="before",
    )
    @classmethod
    def parse_optional_text(cls, value: str | None) -> str | None:
        if value == "" or value is None:
            return None
        return value.strip()

    @field_validator("admin_ids", "popular_stars_amounts", mode="before")
    @classmethod
    def parse_int_list(cls, value: str | int | list[int]) -> list[int]:
        if isinstance(value, list):
            parsed = value
        elif isinstance(value, int):
            parsed = [value]
        elif not value:
            parsed = []
        else:
            parsed = [int(item.strip()) for item in value.split(",") if item.strip()]
        if any(item <= 0 for item in parsed):
            raise ValueError("Telegram IDs and Stars presets must be positive integers")
        return parsed

    @field_validator("stars_purchase_provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"test", "fragment"}:
            raise ValueError("STARS_PURCHASE_PROVIDER must be test or fragment")
        return normalized

    @field_validator("customer_payment_provider")
    @classmethod
    def validate_customer_payment_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"test", "ton", "yookassa"}:
            raise ValueError("CUSTOMER_PAYMENT_PROVIDER must be test, ton, or yookassa")
        return normalized

    @field_validator("process_role")
    @classmethod
    def validate_process_role(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"all", "bot", "worker", "admin", "gateway"}:
            raise ValueError("PROCESS_ROLE must be all, bot, worker, admin, or gateway")
        return normalized

    @field_validator("admin_access_mode")
    @classmethod
    def validate_admin_access_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"local", "https", "ssh_tunnel"}:
            raise ValueError("ADMIN_ACCESS_MODE must be local, https, or ssh_tunnel")
        return normalized

    @field_validator("fragment_api_base_url")
    @classmethod
    def validate_fragment_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api-fragment.duckdns.org"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "FRAGMENT_API_BASE_URL must be the documented production HTTPS endpoint"
            )
        return normalized

    @field_validator("yookassa_api_base_url")
    @classmethod
    def validate_yookassa_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.yookassa.ru"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/v3"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("YOOKASSA_API_BASE_URL must be https://api.yookassa.ru/v3")
        return normalized

    @field_validator("yookassa_receipt_mode")
    @classmethod
    def validate_receipt_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"owner_decision_required", "disabled_by_contract"}:
            raise ValueError(
                "YOOKASSA_RECEIPT_MODE must be owner_decision_required or disabled_by_contract"
            )
        return normalized

    @model_validator(mode="after")
    def load_secret_files(self) -> "Settings":
        mappings = (
            ("bot_token", self.bot_token_file, True),
            ("database_url", self.database_url_file, False),
            ("telegram_api_hash", self.telegram_api_hash_file, True),
            ("fragment_wallet_seed", self.fragment_wallet_seed_file, True),
            ("fragment_cookies", self.fragment_cookies_file, True),
            ("fragment_local_storage", self.fragment_local_storage_file, True),
            ("toncenter_api_key", self.toncenter_api_key_file, True),
            ("yookassa_shop_id", self.yookassa_shop_id_file, False),
            ("yookassa_secret_key", self.yookassa_secret_key_file, True),
        )
        for field_name, path, secret in mappings:
            if path is None:
                continue
            value = self._read_secret_file(path)
            object.__setattr__(self, field_name, SecretStr(value) if secret else value)
        return self

    @staticmethod
    def _read_secret_file(path: Path) -> str:
        try:
            metadata = path.stat()
            in_container = Path("/.dockerenv").exists()
            if os.name != "nt" and not in_container and stat.S_IMODE(metadata.st_mode) & 0o077:
                raise RuntimeError(f"Secret file permissions are too broad: {path}")
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"Could not read configured secret file: {path}") from exc
        if not value:
            raise RuntimeError(f"Configured secret file is empty: {path}")
        return value

    @property
    def telegram_session_path(self) -> Path:
        return self.telegram_session_dir / self.telegram_session_name

    @property
    def is_fragment_mode(self) -> bool:
        return self.stars_purchase_provider == "fragment"

    @property
    def admin_access_is_protected(self) -> bool:
        parsed = urlsplit(self.admin_public_url)
        common_url_ok = bool(
            parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
        if self.admin_access_mode == "https":
            return common_url_ok and parsed.scheme == "https" and self.admin_cookie_secure
        if self.admin_access_mode == "ssh_tunnel":
            return bool(
                common_url_ok
                and parsed.scheme == "http"
                and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                and not self.admin_cookie_secure
            )
        return False

    def secret_value(self, value: SecretStr | None) -> str | None:
        return value.get_secret_value() if value is not None else None

    def validate_runtime(self) -> None:
        runs_bot = self.process_role in {"all", "bot"}
        runs_worker = self.process_role in {"all", "worker"}
        if runs_bot and not self.bot_token.get_secret_value():
            raise RuntimeError("BOT_TOKEN is required")
        if runs_bot and (not self.telegram_api_id or not self.telegram_api_hash):
            raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required for the bot")
        if self.min_stars > self.max_stars:
            raise RuntimeError("MIN_STARS cannot be greater than MAX_STARS")
        if self.stars_standard_order_threshold <= self.min_stars:
            raise RuntimeError("STARS_STANDARD_ORDER_THRESHOLD must be greater than MIN_STARS")
        if self.stars_large_order_threshold <= self.stars_standard_order_threshold:
            raise RuntimeError(
                "STARS_LARGE_ORDER_THRESHOLD must be greater than STARS_STANDARD_ORDER_THRESHOLD"
            )
        if (
            self.stars_standard_order_markup_percent is not None
            and self.stars_standard_order_markup_percent > self.stars_markup_percent
        ):
            raise RuntimeError(
                "STARS_STANDARD_ORDER_MARKUP_PERCENT cannot exceed STARS_MARKUP_PERCENT"
            )
        if (
            self.stars_large_order_markup_percent is not None
            and self.stars_large_order_markup_percent
            > (
                self.stars_standard_order_markup_percent
                if self.stars_standard_order_markup_percent is not None
                else self.stars_markup_percent
            )
        ):
            raise RuntimeError(
                "STARS_LARGE_ORDER_MARKUP_PERCENT cannot exceed STARS_MARKUP_PERCENT"
            )
        if any(
            amount < self.min_stars or amount > self.max_stars
            for amount in self.popular_stars_amounts
        ):
            raise RuntimeError("POPULAR_STARS_AMOUNTS must be inside MIN_STARS..MAX_STARS")
        if self.test_payment_mode and self.real_stars_purchase_enabled:
            raise RuntimeError(
                "REAL_STARS_PURCHASE_ENABLED cannot be true with TEST_PAYMENT_MODE; "
                "a simulated payment must never spend real wallet funds"
            )
        if self.is_fragment_mode:
            if (
                not self.test_payment_mode
                and self.process_role in {"bot", "admin"}
                and self.fragment_wallet_seed is not None
            ):
                raise RuntimeError(
                    "FRAGMENT_WALLET_SEED must be available only to the purchase worker"
                )
            if (
                not self.test_payment_mode
                and self.process_role in {"bot", "admin", "gateway"}
                and (self.fragment_cookies is not None or self.fragment_local_storage is not None)
            ):
                raise RuntimeError(
                    "Fragment KYC session secrets must be available only to the purchase worker"
                )
            if self.real_stars_purchase_enabled and runs_worker and not self.fragment_wallet_seed:
                raise RuntimeError("FRAGMENT_WALLET_SEED is required for real Fragment purchases")
            if self.fragment_payment_method not in {"ton", "usdt_ton"}:
                raise RuntimeError("FRAGMENT_PAYMENT_METHOD must be ton or usdt_ton")
            if self.fragment_api_mode not in {"kyc", "no_kyc"}:
                raise RuntimeError("FRAGMENT_API_MODE must be kyc or no_kyc")
            if (
                self.fragment_api_mode == "kyc"
                and self.real_stars_purchase_enabled
                and runs_worker
                and not self.fragment_cookies
            ):
                raise RuntimeError("FRAGMENT_COOKIES is required for Fragment KYC purchases")
        if not self.test_payment_mode:
            if self.process_role == "all":
                raise RuntimeError(
                    "Production money flow requires separate PROCESS_ROLE=bot and PROCESS_ROLE=worker processes"
                )
            if self.customer_payment_provider == "test":
                raise RuntimeError("Production customer payment provider cannot be test")
            yookassa_safe_test = (
                self.customer_payment_provider == "yookassa" and self.yookassa_test_mode
            )
            if not yookassa_safe_test and not self.is_fragment_mode:
                raise RuntimeError("Live production requires STARS_PURCHASE_PROVIDER=fragment")
            if not self.real_stars_purchase_enabled and not yookassa_safe_test:
                raise RuntimeError(
                    "Production payment mode requires REAL_STARS_PURCHASE_ENABLED=true"
                )
            if yookassa_safe_test and self.real_stars_purchase_enabled:
                raise RuntimeError("YooKassa test shop must use REAL_STARS_PURCHASE_ENABLED=false")
            if self.customer_payment_provider == "ton" and not self.ton_wallet_address:
                raise RuntimeError("TON_WALLET_ADDRESS is required for TON customer payments")
            if self.customer_payment_provider == "yookassa":
                if self.process_role in {"bot", "admin", "gateway"}:
                    if not self.yookassa_shop_id or not self.yookassa_secret_key:
                        raise RuntimeError("YooKassa shop ID and secret key are required")
                    if self.yookassa_receipt_mode == "owner_decision_required":
                        raise RuntimeError("YooKassa receipt configuration REQUIRES OWNER DECISION")
                    if not self.yookassa_return_url or not self.yookassa_webhook_url:
                        raise RuntimeError("YooKassa return and webhook URLs are required")
                    for name, value in (
                        ("YOOKASSA_RETURN_URL", self.yookassa_return_url),
                        ("YOOKASSA_WEBHOOK_URL", self.yookassa_webhook_url),
                    ):
                        parsed = urlsplit(value)
                        if parsed.scheme != "https" or not parsed.hostname:
                            raise RuntimeError(f"{name} must be a public HTTPS URL")
                    if not self.yookassa_webhook_url.endswith("/webhooks/yookassa"):
                        raise RuntimeError("YOOKASSA_WEBHOOK_URL must end with /webhooks/yookassa")
            if not self.admin_ids:
                raise RuntimeError("ADMIN_IDS is required in production mode")
            if self.database_url.startswith("sqlite+"):
                raise RuntimeError(
                    "Production money flow requires PostgreSQL; SQLite is test/development only"
                )
        if self.admin_access_mode in {"https", "ssh_tunnel"} and not self.admin_access_is_protected:
            raise RuntimeError("Admin access configuration does not match ADMIN_ACCESS_MODE")
        if self.admin_cookie_secure and not self.admin_public_url.lower().startswith("https://"):
            raise RuntimeError("ADMIN_PUBLIC_URL must use HTTPS when ADMIN_COOKIE_SECURE=true")


@lru_cache
def get_settings() -> Settings:
    return Settings()
