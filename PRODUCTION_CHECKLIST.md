# Production Checklist

Актуальные инструкции: [`docs/VPS_DEPLOY.md`](docs/VPS_DEPLOY.md) и [`docs/YOOKASSA_SETUP.md`](docs/YOOKASSA_SETUP.md). Для рублёвой схемы используется `CUSTOMER_PAYMENT_PROVIDER=yookassa`; `TON_WALLET_ADDRESS` относится только к устаревшему прямому TON-платежу покупателя. Публичны только 80/443 для YooKassa, админка — исключительно `127.0.0.1:8080` через SSH-туннель.

Все пункты должны быть отмечены владельцем. Код не может самостоятельно выполнить действия с
wallet, hosting, firewall, DNS, backup storage и реальными credentials.

## Credentials и wallet

- [ ] Созданы новые отдельные production credentials; тестовые credentials не переиспользуются.
- [ ] Bot token, API hash, session, DB URLs/passwords и wallet seed отсутствуют в Git/history.
- [ ] Secret scan выполнен; при старом попадании credentials они отозваны, а не просто удалены.
- [ ] Используется отдельный operational hot wallet, не основной treasury/cold wallet.
- [ ] На hot wallet находится только утверждённый дневной лимит и gas reserve.
- [ ] Seed доступна только `purchase-worker` через read-only secret mount.
- [ ] Fragment KYC cookies содержат `stel_token`, `stel_ssid`, `stel_ton_token`, `stel_dt`,
      передаются только через Docker secret и отсутствуют у bot/admin/gateway.
- [ ] KYC cookies проверены preflight, а их действительность подтверждена одной контролируемой
      покупкой по `docs/FRAGMENT_KYC_SETUP.md`.
- [ ] Риск хранения seed на worker явно принят либо выбран внешний signing service.
- [ ] Настроены alerts по низкому balance и необычным outgoing transactions.
- [ ] Проверена процедура быстрой замены wallet и вывода средств при компрометации.

## Инфраструктура

- [ ] PostgreSQL не имеет публичного порта и доступен только application network.
- [ ] Migration и runtime database roles разделены; runtime role не имеет DDL/superuser прав.
- [ ] Firewall/security groups и host updates настроены.
- [ ] Admin доступна только через HTTPS либо SSH-туннель; порт `8080` не открыт в интернет.
- [ ] Контейнеры запускаются non-root, read-only, без privileged mode и с resource limits.
- [ ] Secrets не находятся в image layers или Docker build context.
- [ ] Для webhook настроены HTTPS, proxy и body/rate limits; событие перепроверяется через GET API ЮKassa.
- [ ] Central JSON log collection и append-only/off-host storage настроены.
- [ ] Health checks подключены к внешнему monitoring.
- [ ] Alerts для provider/DB errors, MANUAL_REVIEW, failed purchases и queue growth проверены.

## База и recovery

- [ ] Свежий encrypted backup создан перед migration.
- [ ] Автоматическое расписание backups и off-host retention настроены.
- [ ] Restore проверен на отдельной staging-БД, включая counts, constraints и audit trail.
- [ ] `alembic upgrade head` успешно выполнен migration-ролью.
- [ ] Reconciliation конкретного order ID проверен без второго provider POST.
- [ ] Crash/restart worker во время queued purchase проверен на staging.
- [ ] Late, duplicate, underpaid и overpaid TON transactions имеют operational runbook.

## Конфигурация

- [ ] `TEST_PAYMENT_MODE=false`, `REAL_STARS_PURCHASE_ENABLED=true`, provider `fragment`.
- [ ] Production использует PostgreSQL и отдельные `PROCESS_ROLE=bot`/`worker`.
- [ ] `ADMIN_IDS` содержит только актуальные numeric user IDs.
- [ ] `MIN_STARS`, `MAX_STARS`, `MAX_ORDER_RUB_AMOUNT`, `MAX_PAYMENT_TON` утверждены.
- [ ] Daily/open-order/rate limits утверждены для ожидаемого трафика.
- [ ] `TON_WALLET_ADDRESS` независимо сверен с receiving hot wallet.
- [ ] `MAINTENANCE_MODE=true` и `PURCHASES_ENABLED=false` на первом запуске.
- [ ] `/maintenance` и `/purchases` kill switches протестированы.

## Quality gates

- [ ] Formatter check проходит.
- [ ] Linter проходит.
- [ ] Mypy проходит либо все исключения документированы.
- [ ] Unit/integration/security tests проходят.
- [ ] `pip check` проходит.
- [ ] `pip-audit -r requirements.lock` не сообщает известных уязвимостей либо риск принят.
- [ ] Source/config scan не обнаруживает token, seed, private key, session или password.
- [ ] Docker image собирается из `requirements.lock` и Compose config проверен.
- [ ] Happy path и failure paths проверены только mock/sandbox до production approval.

## Controlled go-live

- [ ] `/health`, `/fragment_status`, `/stats` успешны при выключенных покупках.
- [ ] Реальная минимальная покупка 50 Stars вручную одобрена владельцем и выполнена один раз.
- [ ] В Fragment/provider и БД присутствует один request/transaction, не два.
- [ ] Пользователь получил все status notifications.
- [ ] TON incoming transaction и итоговая доставка сверены вручную.
- [ ] Refund/manual-review workflow протестирован контролируемо.
- [ ] Только после этого выполнено `/purchases on CONFIRM`.
- [ ] В первые часы go-live ведётся усиленное наблюдение и готов emergency stop.

## ЮKassa (обязательный этап перед публичной оплатой рублями)

- [ ] Магазин и KYC в ЮKassa одобрены, получены отдельные test credentials.
- [ ] Схема чеков, предмет расчёта, налоговая система и НДС подтверждены владельцем/бухгалтером.
- [ ] Выполнен flow из `docs/YOOKASSA_SETUP.md`; секрет доступен только bot/admin/gateway.
- [ ] На каждый POST используется стабильный `Idempotence-Key`; provider payment ID уникален.
- [ ] Webhook доступен только по HTTPS и перепроверяет платёж через GET API ЮKassa.
- [ ] Проверены duplicate webhook, canceled payment, неверная сумма/валюта/metadata и timeout.
- [ ] `payment.succeeded` переводит заказ в PAID один раз; Fragment до этого не вызывается.
- [ ] Возврат имеет отдельную идемпотентность и невозможен при неопределённом Fragment status.
- [ ] Production shopId/secret подключены только после успешного sandbox прогона.
