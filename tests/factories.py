from app.config import Settings

TEST_SEED = " ".join(f"word{i}" for i in range(24))


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "BOT_TOKEN": "1234567890:test-token-value-for-tests-only",
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "PROCESS_ROLE": "all",
        "TELEGRAM_API_ID": 12345,
        "TELEGRAM_API_HASH": "test-api-hash",
        "TELEGRAM_USER_PHONE": None,
        "ADMIN_IDS": "1",
        "STARS_MARKUP_PERCENT": "0",
        "TEST_PAYMENT_MODE": True,
        "REAL_STARS_PURCHASE_ENABLED": False,
        "STARS_PURCHASE_PROVIDER": "test",
        "PURCHASES_ENABLED": True,
        "MAINTENANCE_MODE": False,
        "FRAGMENT_WALLET_SEED": None,
        "FRAGMENT_COOKIES": None,
        "FRAGMENT_LOCAL_STORAGE": None,
        "TON_WALLET_ADDRESS": None,
        "TONCENTER_API_KEY": None,
    }
    values.update(overrides)
    # Pydantic Settings accepts aliases and _env_file dynamically; its generated signature does not.
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]
