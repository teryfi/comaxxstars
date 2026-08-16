import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from logging.config import dictConfig

KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|cookie|password|private[_-]?key|seed|token)=([^&\s]+)"
)
TELEGRAM_TOKEN_RE = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")
NOISY_DEPENDENCY_LOGGERS = ("aiogram.event", "telethon.client.updates", "aiohttp.access")


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(sorted({value for value in secrets if value}, key=len, reverse=True))

    def _redact(self, value: str) -> str:
        result = value
        for secret in self._secrets:
            result = result.replace(secret, "[REDACTED]")
        result = KEY_VALUE_SECRET_RE.sub(r"\1=[REDACTED]", result)
        return TELEGRAM_TOKEN_RE.sub("[REDACTED_TELEGRAM_TOKEN]", result)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "message": self._redact(record.getMessage()),
        }
        for field in (
            "order_id",
            "user_id",
            "request_id",
            "error_code",
            "error_type",
            "provider",
            "status",
            "latency_seconds",
            "queue_size",
            "count",
        ):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = self._redact(str(value))
        if record.exc_info:
            payload["exception"] = self._redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_logging(level: str, *, secrets: Iterable[str] = ()) -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter, "secrets": tuple(secrets)}},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                }
            },
            "loggers": {
                logger_name: {"level": "WARNING"} for logger_name in NOISY_DEPENDENCY_LOGGERS
            },
            "root": {"handlers": ["console"], "level": level.upper()},
        }
    )
