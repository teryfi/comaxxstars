# Final pre-deploy report

Дата проверки: 2026-08-11. Реальные платежи, возвраты и покупки Stars не выполнялись.

1. **Код:** финансовые состояния, провайдеры и фоновые задачи разделены; лишние production-заглушки отсутствуют.
2. **Производительность:** Redis/Celery/Node не добавлялись; для текущей нагрузки достаточно PostgreSQL и трёх Python-сервисов.
3. **Процессы:** bot, purchase-worker, payment-gateway и admin запускаются раздельно; Caddy обслуживает только внешний HTTPS.
4. **База:** production требует PostgreSQL, Alembic head `0005`; ограничения защищают provider IDs и идемпотентность.
5. **Polling:** PostgreSQL advisory lock не позволяет запустить два Telegram poller одновременно.
6. **Worker:** только worker получает seed; неопределённый Fragment POST не повторяется автоматически.
7. **Админка:** loopback-only, RBAC/TOTP/CSRF/audit, фильтры, пагинация, сверка ЮKassa и защищённый refund.
8. **Контейнеры:** non-root/read-only, healthchecks, resource limits, log rotation и stop grace periods.
9. **Секреты:** secret-file mounts, redaction и fail-closed production config; `.env` не изменялся.
10. **ЮKassa:** create/get/refund, redirect, webhook и строгая проверка суммы/RUB/metadata реализованы по официальному API.
11. **Дубли:** стабильные idempotence keys, unique constraints, повторные webhook и reconciliation безопасны.
12. **Эксплуатация:** Caddy, backup PostgreSQL, retention, health endpoints и runbook подготовлены.
13. **Тесты:** Ruff format/check, mypy, 112 pytest, `pip check`, `pip-audit` и безопасный dry-run прошли.
14. **Осталось владельцу:** VPS/DNS, credentials ЮKassa, решение по чекам, secrets, restore-test и контролируемая test payment.
15. **Статус:** READY FOR VPS; NOT LIVE до выполнения владельцем checklist и успешного sandbox-прогона.

Канонические инструкции: [`../YOOKASSA_SETUP.md`](../YOOKASSA_SETUP.md),
[`../VPS_DEPLOY.md`](../VPS_DEPLOY.md), [`../../SECURITY.md`](../../SECURITY.md).
