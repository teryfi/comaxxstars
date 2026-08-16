# terstars

## Описание проекта

TerStars — готовый Telegram-магазин для продажи Telegram Stars с автоматической динамической
ценой, заказами для себя и в подарок, локализованным интерфейсом и закрытой web-админкой.
Проект поддерживает тестовый режим без денег, покупку Stars через
Fragment, PostgreSQL, безопасную обработку повторных webhook/retry и развёртывание на небольшом
VPS через Docker Compose.

Ключевые преимущества:

- произвольное количество Stars и автоматически уменьшающаяся наценка для крупных заказов;
- серверная фиксация котировки, суммы и получателя — клиент не управляет финансовыми полями;
- максимум одна успешная выдача на один платёж благодаря state machine, idempotency и DB locks;
- отдельный `purchase-worker`: seed hot wallet недоступна боту, админке и payment gateway;
- закрытая админка через SSH-туннель с паролем, TOTP, CSRF, RBAC и audit log;
- YooKassa webhook подтверждается обратным GET-запросом, а возврат защищён ручным подтверждением;
- kill switches, лимиты, durable notifications, reconciliation, backup и production preflight;
- лёгкий стек без Redis, Celery, Node.js и тяжёлого frontend.

Полное руководство для нового владельца находится в
[`docs/BUYER_GUIDE.md`](docs/BUYER_GUIDE.md) и в готовом
[`Word-документе`](docs/TerStars_Полное_руководство_для_покупателя.docx).


Безопасное подключение Fragment KYC и cookies: [`docs/FRAGMENT_KYC_SETUP.md`](docs/FRAGMENT_KYC_SETUP.md).

Рекомендуемая production-схема: рубли принимаются через ЮKassa, а отдельный
`purchase-worker` покупает Stars из заранее пополненного hot wallet. Прямой TON-платёж покупателя
сохранён только как совместимый режим и не является рекомендуемой публичной схемой.

Telegram-бот продаёт произвольное количество Telegram Stars от 50 штук. Цена берётся из
документированного Fragment API в момент котировки, комиссия Fragment включается в цену за
единицу, затем применяется плавная скидка за объём. Для минимального заказа используется
`STARS_MARKUP_PERCENT`, для обычного — `STARS_STANDARD_ORDER_MARKUP_PERCENT`, а к
`STARS_LARGE_ORDER_THRESHOLD` наценка постепенно снижается до
`STARS_LARGE_ORDER_MARKUP_PERCENT`. Между тремя опорными точками работает логарифмическая
интерполяция, поэтому собственную цену получает любое количество Stars без скачков между
пакетами. Денежные расчёты выполняются через `Decimal`.

Проект по умолчанию запускается безопасно: цена Fragment настоящая, но оплата и доставка
симулируются. Реальные деньги включаются только отдельной production-конфигурацией.

Документация SDK фиксирует минимум 50 Stars и не публикует верхний предел. Поэтому `MAX_STARS`
является явным operator/fraud cap (по умолчанию 100000), а не выдуманным лимитом Fragment.

## Как считается цена

Для `FRAGMENT_PAYMENT_METHOD=usdt_ton`:

```text
цена_единицы = Fragment price_per_star_usdt_ton * (1 + комиссия_Fragment / 100)
USD_итого    = цена_единицы * Stars * (1 + наценка / 100)
RUB_итого    = round(USD_итого * курс_USD_RUB_ЦБ)
```

Для `FRAGMENT_PAYMENT_METHOD=ton` сначала используется цена Fragment в TON с комиссией,
затем живые TON/USD и USD/RUB. Котировка хранится с TTL; после подтверждения заказа она не
пересчитывается задним числом. При истечении TTL пользователь получает новую котировку.

## Архитектура

```text
Telegram user
    -> bot process (Bot API polling, UI, TON payment monitor, notifications)
    -> PostgreSQL (orders, payments, purchase attempts, audit, outbox)
    -> purchase-worker (единственный процесс с доступом к seed)
    -> documented Fragment SDK/API

TON payment monitor -> Toncenter (server-side transaction verification)
Pricing             -> Fragment prices + CBR + TON/USD provider
```

Production-процесс бота не получает seed. Seed монтируется только в `purchase-worker` как
Docker secret. Worker сначала сохраняет попытку и idempotency key, получает `request_id`, а
после timeout проверяет статус по этому ID и не отправляет второй POST вслепую.

## Что нужно получить

- `BOT_TOKEN`: у `@BotFather`.
- `TELEGRAM_API_ID` и `TELEGRAM_API_HASH`: на [my.telegram.org](https://my.telegram.org),
  раздел API development tools.
- Номер телефона вводится только при однократном создании Telethon session. Это номер
  пользовательского Telegram-аккаунта, через который бот разрешает username получателя.
- Fragment API key не нужен. Используется официальный endpoint библиотеки
  `https://api-fragment.duckdns.org`.

При вводе 2FA-пароля в CMD символы специально не отображаются. Введите пароль вслепую и
нажмите Enter.

## Локальная установка Windows

Рекомендуется Python 3.12:

```powershell
cd "C:\Projects\tg-stars-seller"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Заполните в `.env`:

```env
BOT_TOKEN=<BotFather token>
TELEGRAM_API_ID=<numeric api id>
TELEGRAM_API_HASH=<api hash>
TELEGRAM_USER_PHONE=<phone in international format>
ADMIN_IDS=<your numeric Telegram user ID>
```

Создайте session один раз из корня проекта:

```powershell
.\.venv\Scripts\python.exe scripts\create_telegram_session.py
```

Session-файлы находятся в `sessions/`, исключены из Git и тоже являются секретом.

## Безопасная проверка сейчас

Оставьте эти значения:

```env
DATABASE_URL=sqlite+aiosqlite:///./terstars.db
PROCESS_ROLE=all
TEST_PAYMENT_MODE=true
REAL_STARS_PURCHASE_ENABLED=false
STARS_PURCHASE_PROVIDER=fragment
STARS_MARKUP_PERCENT=31.15
STARS_STANDARD_ORDER_MARKUP_PERCENT=25.41
STARS_STANDARD_ORDER_THRESHOLD=100
STARS_LARGE_ORDER_MARKUP_PERCENT=24.59
STARS_LARGE_ORDER_THRESHOLD=10000
PURCHASES_ENABLED=true
```

Запуск:

```powershell
.\.venv\Scripts\python.exe -m app.main
```

Безопасная startup-проверка БД, конфигурации и Fragment GET endpoints без polling/покупки:

```powershell
.\.venv\Scripts\python.exe scripts\startup_check.py
```

В этом режиме:

- `/start` показывает рабочий интерфейс;
- цена запрашивается у Fragment в реальном времени;
- курс USD/RUB запрашивается у ЦБ;
- кнопка `TEST PAYMENT` не проверяет блокчейн;
- доставка Stars симулируется (`PURCHASES_ENABLED=true` включает только test provider);
- seed не нужен;
- средства не расходуются.

Остановка: `Ctrl+C`.

## Состояния заказа

Основной путь:

```text
CREATED -> WAITING_FOR_PAYMENT -> PAYMENT_DETECTED -> PAYMENT_CONFIRMING
-> PAID -> PURCHASE_PROCESSING -> STARS_SENDING -> COMPLETED
```

Ошибки и ручная обработка: `PAYMENT_EXPIRED`, `PAYMENT_FAILED`, `PURCHASE_FAILED`,
`REFUND_REQUIRED`, `REFUNDED`, `CANCELLED`, `MANUAL_REVIEW`.

Каждый переход, платёж, попытка покупки и важное admin-действие записываются в БД. Durable
outbox доставляет пользователю сообщения о статусах после рестарта и повторяет временно
неудавшиеся Telegram-уведомления с backoff.

## Production prerequisites

До включения реальных денег выполните весь [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md).
Минимально нужны:

- отдельный operational hot wallet с небольшой суммой;
- PostgreSQL, не опубликованный в интернет;
- Docker Engine и Docker Compose;
- новые production credentials;
- backup, restore test, мониторинг и alerts;
- `ADMIN_IDS`;
- ручная минимальная end-to-end покупка только после всех проверок.

Основной treasury/cold wallet нельзя передавать приложению. Код не способен ограничить общий
баланс кошелька: лимит достигается тем, что владелец держит на hot wallet только допустимую
сумму.

## Production secrets

Скопируйте `.env.production.example` в `.env.production`. Создайте каталог `secrets/` вне Git.
Файлы должны содержать ровно одно значение без кавычек:

```text
secrets/postgres_password
secrets/runtime_database_password
secrets/migration_database_url
secrets/runtime_database_url
secrets/bot_token
secrets/telegram_api_hash
secrets/fragment_wallet_seed
secrets/yookassa_shop_id
secrets/yookassa_secret_key
```

Примеры двух URL PostgreSQL:

```text
postgresql+asyncpg://terstars_migrator:<migrator-password>@postgres:5432/terstars
postgresql+asyncpg://terstars_runtime:<runtime-password>@postgres:5432/terstars
```

Спецсимволы пароля в URL должны быть percent-encoded. Пароли в URL-файлах должны совпадать с
`postgres_password` и `runtime_database_password`. Init-скрипт создаёт runtime-роль без DDL
прав и выдаёт ей доступ к таблицам/sequence; Alembic использует отдельную migration-роль.

В `fragment_wallet_seed` допустима исходная 24-словная seed phrase. Приложение кодирует её для
SDK в памяти. Base64 не шифрует секрет и не делает его безопаснее. Ограничьте файл правами
владельца; предпочтительнее использовать secret manager хостинга с read-only mount.

Никогда не отправляйте seed, cookies, session или токены в Telegram и не помещайте их в `.env`,
Git, образ Docker, логи или backup исходников.

## Production configuration

Проверьте `.env.production`:

```env
TEST_PAYMENT_MODE=false
REAL_STARS_PURCHASE_ENABLED=true
STARS_PURCHASE_PROVIDER=fragment
PURCHASES_ENABLED=false
MAINTENANCE_MODE=true
TON_WALLET_ADDRESS=<receiving hot-wallet address>
ADMIN_IDS=123456789
FRAGMENT_PAYMENT_METHOD=ton
```

`FRAGMENT_PAYMENT_METHOD` задаёт только актив расходного Fragment hot wallet: `ton` для TON или
`usdt_ton` для USDT в сети TON. Он не связан с валютой покупателя. После подключения ЮKassa
покупатель платит рублями, а `purchase-worker` по-прежнему расходует заранее пополненный hot wallet.

`PROCESS_ROLE` переопределяется Compose отдельно для `bot` и `purchase-worker`. Production
startup отклоняет `PROCESS_ROLE=all`, SQLite, пустой admin allowlist, отсутствие wallet address
и комбинацию simulated payment + real purchase.

Запуск с закрытыми покупками:

```bash
docker compose --env-file .env.production build
docker compose --env-file .env.production up -d
docker compose --env-file .env.production ps
docker compose --env-file .env.production logs migrate
docker compose --env-file .env.production logs --tail=100 bot purchase-worker
```

Затем в Telegram от admin ID:

```text
/health
/fragment_status
/stats
/maintenance off
```

Проверьте интерфейс и котировки. Реальные покупки остаются остановлены. Только после проверки
wallet, backup, alerts и минимального реального сценария включите:

```text
/purchases on CONFIRM
```

## Миграции

SQLite обновляется только для локального dev/test через `create_local_schema`. Production
PostgreSQL обновляется исключительно Alembic:

```bash
alembic current
alembic upgrade head
```

Compose запускает одноразовый service `migrate` до бота и worker. Перед миграцией создайте
backup. Миграция `0003_production_safety` сохраняет старые заказы, переносит статусы и создаёт
`users`, `payments`, `purchase_attempts`, `audit_events`, runtime controls и notification outbox.

## Backup и restore

Пример custom-format backup:

```bash
docker compose --env-file .env.production exec postgres \
  pg_dump -U terstars_migrator -d terstars -Fc -f /tmp/terstars.dump
docker compose --env-file .env.production cp \
  postgres:/tmp/terstars.dump ./backups/terstars-YYYYMMDD.dump
```

Шифруйте backup отдельным ключом и храните копию вне сервера. Backup содержит Telegram IDs,
username snapshots и финансовую историю, но не должен содержать seed.

Restore проверяется на отдельной staging-БД, никогда поверх production без отдельного change
plan:

```bash
createdb terstars_restore_test
pg_restore --exit-on-error -d terstars_restore_test terstars-YYYYMMDD.dump
```

После restore сравните количество заказов/платежей, статусы, constraints и выполните тестовый
reconciliation без финансового POST.

## Admin-команды

- `/order ID`: безопасная сводка заказа.
- `/user_orders TELEGRAM_ID`: поиск по numeric ID.
- `/stuck`: незавершённые и неоднозначные заказы.
- `/manual_review`: очередь ручной проверки.
- `/reconcile ID`: GET-проверка уже сохранённого provider request ID.
- `/retry ID CONFIRM`: только безопасный путь без слепого повторного POST.
- `/cancel_order ID CONFIRM`: только неоплаченный заказ.
- `/refund ID CONFIRM`: отметить уже выполненный вручную возврат.
- `/stats`, `/health`, `/fragment_status`.
- `/maintenance on|off|status`.
- `/purchases on CONFIRM`, `/purchases off`.

Admin определяется только по numeric Telegram ID. Команд для вывода seed/token не существует.

## Web admin panel

Панель использует ту же БД, что бот и worker. URL по умолчанию:
`http://127.0.0.1:8080/admin`. Контейнер публикует порт только на loopback VPS.

Самый простой закрытый production-доступ — SSH-туннель без домена и публичной админки:

```cmd
ssh -N -L 8080:127.0.0.1:8080 root@VPS_IP
```

Пока команда работает, откройте `http://127.0.0.1:8080/admin` на своём компьютере. Для этого
режима задайте `ADMIN_ACCESS_MODE=ssh_tunnel`, `ADMIN_PUBLIC_URL=http://127.0.0.1:8080` и
`ADMIN_COOKIE_SECURE=false`. HTTP существует только на loopback с обеих сторон, а передача между
компьютером и VPS зашифрована SSH. Порт `8080` не открывайте в firewall.

Если панель должна быть доступна без SSH-туннеля, используйте HTTPS reverse proxy, задайте
`ADMIN_ACCESS_MODE=https`, HTTPS URL и `ADMIN_COOKIE_SECURE=true`.

Создайте первого OWNER после применения миграций:

```powershell
.\.venv\Scripts\python.exe -m app.admin.cli create-owner --username owner
```

Для локальной SQLite команда сама создаст схему. Она запросит пароль без эха и выдаст TOTP
secret/URI один раз. Добавьте его в приложение-
аутентификатор. `--without-2fa` допускается только для локальной разработки.

Для production Compose используйте ту же команду внутри admin-контейнера:

```powershell
docker compose --env-file .env.production run --rm admin python -m app.admin.cli create-owner --username owner
```

Локальный запуск в отдельном терминале:

```powershell
$env:PROCESS_ROLE="admin"
.\.venv\Scripts\python.exe -m app.admin.server
```

Production через Compose запускает `bot`, `purchase-worker` и `admin` раздельно:

```powershell
docker compose --env-file .env.production up -d postgres migrate bot purchase-worker admin
```

Перед первой покупкой выполните безопасные проверки:

```powershell
.\.venv\Scripts\python.exe -m app.operations preflight
.\.venv\Scripts\python.exe -m app.operations dry-run
```

В production Compose используйте изолированные одноразовые сервисы. `preflight` получает
read-only mounts необходимых secrets, а `dry-run` не имеет сети и production-БД:

```bash
docker compose --env-file .env.production --profile ops run --rm preflight
docker compose --env-file .env.production --profile ops run --rm dry-run
```

`preflight` не совершает списаний. Установленный Fragment SDK не публикует balance endpoint,
поэтому operational balance нужно проверить и пополнить вручную минимум для заказа 50 Stars,
затем установить `OPERATIONAL_BALANCE_CONFIRMED_STARS=50` (или фактический безопасный минимум).
`/refund` не переводит TON автоматически: сначала владелец вручную выполняет и проверяет
возврат, затем фиксирует факт командой.

## Аварийная остановка

1. Выполните `/maintenance on`: новые заказы перестанут создаваться.
2. Выполните `/purchases off`: новые Fragment POST для оплаченных заказов остановятся.
3. Уже отправленные Fragment requests продолжают проверяться через безопасный status GET.
4. При необходимости остановите `purchase-worker`; bot продолжит видеть платежи и сохранять их.
5. Проверьте `/stuck`, `/manual_review`, wallet и provider history.
6. Не просите пользователей платить повторно.

## Обновление

1. Сделайте backup и проверьте его файл.
2. Оставьте `MAINTENANCE_MODE=true` и `PURCHASES_ENABLED=false`.
3. Запустите formatter, linter, mypy, tests и `pip-audit`.
4. Соберите новый image из `requirements.lock`.
5. Выполните Alembic migration отдельной migration-ролью.
6. Запустите bot/worker, проверьте health и reconciliation.
7. Снимайте maintenance и kill switch только после наблюдения за логами.

## Проверки разработчика

```powershell
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy app scripts tests
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pip_audit -r requirements.lock
.\.venv\Scripts\python.exe -m pip check
```

Реальные внешние финансовые операции тестами не выполняются.

## Ограничения

- Используется Telegram polling, поэтому публичного webhook/HTTP endpoint нет. Это уменьшает
  HTTP attack surface для текущего deployment; reverse proxy не требуется самому боту.
- In-memory rate limiter рассчитан на один bot replica. Для нескольких replicas нужен общий
  limiter на Redis/proxy.
- Toncenter scan ограничен `TON_SCAN_LIMIT`; при очень большом входящем потоке нужен индексатор
  с pagination/streaming.
- Подтверждение TON реализовано повторным наблюдением после задержки, а не собственным full node.
- Fragment API требует wallet seed для реальной покупки. Полная компрометация worker может
  раскрыть hot-wallet seed, поэтому hot wallet должен быть малым и изолированным.
- Баланс hot wallet и аномальные выводы должны контролироваться внешним blockchain-monitoring;
  Fragment SDK не предоставляет надёжного treasury control endpoint.

Подробная модель угроз и действия при компрометации описаны в [SECURITY.md](SECURITY.md).
