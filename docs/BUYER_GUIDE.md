# TerStars: полное руководство нового владельца

Версия руководства: 12 августа 2026 года.

Это руководство предназначено для человека, который получил исходный код TerStars и хочет:

1. безопасно проверить проект на Windows без реальных денег;
2. войти в Telegram-бот и web-админку;
3. подготовить собственные Telegram credentials и Telegram session;
4. развернуть production на VPS;
5. подключить PostgreSQL, домен, HTTPS, ЮKassa и Fragment;
6. выполнить контролируемый переход из тестового режима в live.

> Важно. Не копируйте credentials предыдущего владельца. Создайте собственный bot token,
> Telegram API ID/hash, Telegram session, OWNER, пароли PostgreSQL, магазин ЮKassa и отдельный
> Fragment hot wallet. Никогда не запускайте реальные покупки до успешного тестового сценария и
> production preflight.

## 1. Что представляет собой TerStars

TerStars — Telegram-магазин для продажи Telegram Stars. Пользователь выбирает покупку себе или
в подарок, указывает произвольное количество Stars от настроенного минимума, видит актуальную
цену и создаёт заказ. В production пользователь платит рублями через ЮKassa, после подтверждения
платежа отдельный worker покупает Stars через Fragment из заранее пополненного hot wallet.

### Возможности для покупателя

- покупка Stars себе и другому Telegram-пользователю;
- готовые пакеты и ввод произвольного количества;
- автоматически обновляемая цена Fragment;
- плавное снижение процента наценки при увеличении заказа;
- понятные статусы заказа и повторная проверка оплаты;
- защита от случайной двойной покупки при повторных нажатиях, webhook и restart;
- тестовый режим без реальной оплаты и без списания с кошелька.

### Возможности владельца

- закрытая web-админка с заказами, пользователями, блокировками и audit log;
- пароль OWNER, TOTP-коды, CSRF, RBAC и ограничение попыток входа;
- фильтры, пагинация, просмотр payment/provider/refund состояния;
- сверка платежа ЮKassa без создания новой оплаты;
- ручной подтверждаемый refund workflow;
- Telegram admin-команды, maintenance и аварийное отключение покупок;
- production preflight, dry-run, healthchecks, backup и ротация логов.

### Финансовая безопасность

- сумма, количество Stars и получатель фиксируются сервером;
- `return_url` ЮKassa не считается подтверждением оплаты;
- webhook вызывает server-to-server проверку платежа через API ЮKassa;
- стабильные idempotence keys и уникальные ограничения БД защищают от дублей;
- один платёж может привести максимум к одной успешной выдаче;
- неопределённый Fragment POST не повторяется автоматически;
- seed доступна только `purchase-worker`, но не bot/admin/gateway;
- test-платёж не может запустить реальное списание Fragment;
- admin и PostgreSQL не публикуются в интернет.

### Лёгкая архитектура

В проекте нет Redis, Celery, Node.js, React, Prometheus и Grafana. Production состоит из
PostgreSQL, bot, purchase-worker, admin, payment-gateway и лёгкого Caddy для HTTPS. Telegram bot
работает через polling; входящий публичный HTTP нужен только для webhook ЮKassa.

## 2. Что передаётся покупателю проекта

В каталоге должны находиться:

- `app/` — приложение;
- `migrations/` — миграции PostgreSQL;
- `tests/` — автоматические тесты;
- `scripts/` — setup, healthcheck и backup scripts;
- `deploy/Caddyfile` — HTTPS и маршрутизация webhook;
- `docker-compose.yml`, `Dockerfile`, `.dockerignore`;
- `.env.example`, `.env.production.example`;
- `requirements.lock`, `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`;
- `README.md`, `SECURITY.md`, `PRODUCTION_CHECKLIST.md` и `docs/`.

В передаваемом архиве не должно быть `.env`, `secrets/`, `sessions/`, `.venv/`, `logs/`,
`backups/`, локальной `.db`, wallet seed, bot token или session предыдущего владельца.

## 3. Что новому владельцу нужно получить самостоятельно

Перед началом создайте список:

- Telegram bot через `@BotFather` и новый `BOT_TOKEN`;
- собственный numeric Telegram ID;
- `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` на https://my.telegram.org;
- Telegram-аккаунт с номером телефона для однократного создания Telethon session;
- отдельный Fragment hot wallet с минимальным рабочим остатком;
- VPS с Ubuntu 24.04 LTS, желательно от 2 GB RAM;
- домен или поддомен для payment webhook, например `pay.example.com`;
- test-магазин и затем live-магазин ЮKassa;
- решение по чекам/54-ФЗ вместе с ЮKassa или бухгалтером.

Fragment API не требует отдельного клиентского API key. Нельзя использовать admin key чужого
сервиса. Документация Fragment: https://api-fragment.duckdns.org.

## 4. CMD, PowerShell и Terminal: куда вводить команды

На Windows можно использовать CMD или PowerShell. Не смешивайте их синтаксис в одной строке.

### Как открыть CMD

Нажмите `Win + R`, введите `cmd`, нажмите Enter.

### Как открыть PowerShell

Нажмите `Win + X`, выберите «Терминал» или «Windows PowerShell».

### Как перейти в проект

CMD:

```cmd
cd /d "C:\Projects\tg-stars-seller"
```

PowerShell:

```powershell
Set-Location "C:\Projects\tg-stars-seller"
```

Команды Linux/VPS вводятся после SSH-подключения в терминал VPS, а не в локальный CMD до
подключения.

## 5. Безопасный локальный тест на Windows без реальных денег

Это рекомендуемый первый запуск. Он использует SQLite, настоящие публичные котировки Fragment,
но симулирует оплату и выдачу. Seed и PostgreSQL не нужны.

### Шаг 5.1. Установите Python

Установите 64-bit Python 3.12 с https://www.python.org/downloads/windows/. В установщике
отметьте `Add python.exe to PATH`.

Проверка в CMD:

```cmd
py -3.12 --version
```

### Шаг 5.2. Распакуйте проект

Создайте каталог `C:\Projects\tg-stars-seller` и распакуйте туда переданный архив. Внутри него
должны сразу находиться `app`, `docker-compose.yml` и `pyproject.toml`, без дополнительной
вложенной папки.

### Шаг 5.3. Создайте виртуальное окружение

В новом CMD:

```cmd
cd /d "C:\Projects\tg-stars-seller"
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Проверка зависимостей:

```cmd
.venv\Scripts\python.exe -m pip check
```

### Шаг 5.4. Создайте рабочий `.env`

CMD:

```cmd
copy .env.example .env
notepad .env
```

Заполните только собственные значения:

```env
BOT_TOKEN=PASTE_NEW_BOTFATHER_TOKEN_HERE
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=PASTE_MY_TELEGRAM_ORG_HASH_HERE
TELEGRAM_USER_PHONE=+70000000000
ADMIN_IDS=123456789
```

Для безопасного теста обязательно оставьте:

```env
DATABASE_URL=sqlite+aiosqlite:///./terstars.db
PROCESS_ROLE=all
TEST_PAYMENT_MODE=true
CUSTOMER_PAYMENT_PROVIDER=test
REAL_STARS_PURCHASE_ENABLED=false
STARS_PURCHASE_PROVIDER=fragment
PURCHASES_ENABLED=true
MAINTENANCE_MODE=false
ADMIN_HOST=127.0.0.1
ADMIN_PORT=8080
```

Не добавляйте wallet seed в локальный `.env` для тестового запуска.

### Шаг 5.5. Как получить Telegram bot token

1. Откройте официальный `@BotFather`.
2. Выполните `/newbot`.
3. Укажите название и username, заканчивающийся на `bot`.
4. Сохраните выданный token в `.env` как `BOT_TOKEN`.
5. Настройте `/setdescription`, `/setabouttext`, `/setuserpic` и `/setcommands`.

Рекомендуемое описание в BotFather:

```text
Покупка Telegram Stars себе или в подарок. Актуальная цена, удобные пакеты, произвольное количество и автоматическое отслеживание заказа.
```

Рекомендуемые команды:

```text
start - Открыть магазин
orders - Мои последние заказы
help - Как пользоваться ботом
cancel - Отменить текущий ввод
```

Если token попал в чат, screenshot, Git или чужой компьютер, используйте `/revoke` в BotFather и
сразу замените token.

### Шаг 5.6. Как узнать Telegram ID

Используйте доверенного бота для показа numeric ID либо временно получите ID через Telegram Bot
API. В `ADMIN_IDS` вводится только число, без `@`. Не используйте username как авторизацию.

### Шаг 5.7. Получите API ID и API hash

1. Откройте https://my.telegram.org.
2. Войдите по номеру своего Telegram-аккаунта.
3. Откройте `API development tools`.
4. Создайте приложение с нейтральным названием.
5. Скопируйте `api_id` и `api_hash` в `.env`.

`api_hash` является секретом. Не отправляйте его покупателям и не публикуйте.

### Шаг 5.8. Создайте Telegram session

В CMD из корня проекта:

```cmd
.venv\Scripts\python.exe scripts\create_telegram_session.py
```

Скрипт запросит код Telegram. Если включён облачный пароль Telegram, введите 2FA-пароль. При
вводе пароль может не отображаться — это нормально. После сообщения
`Telegram user session authorized successfully` в `sessions/` появится session-файл.

Session позволяет приложению разрешать username получателя. Это секрет уровня аккаунта. Не
вкладывайте `sessions/` в архив и не переносите session предыдущего владельца.

### Шаг 5.9. Создайте первого OWNER

CMD:

```cmd
.venv\Scripts\python.exe -m app.admin.cli create-owner --username owner
```

Введите новый пароль дважды. Ввод не отображается. Пароли должны полностью совпасть. Затем
скрипт покажет длинный TOTP secret и URI. Это не шестизначный код: secret нужно один раз добавить
в Google Authenticator, Microsoft Authenticator, Aegis или 1Password. Уже приложение будет
генерировать шестизначные коды.

Сохраните TOTP recovery безопасно. Он больше не показывается в admin UI.

Только для временной локальной разработки можно создать OWNER без 2FA:

```cmd
.venv\Scripts\python.exe -m app.admin.cli create-owner --username owner --without-2fa
```

Для VPS/live так делать нельзя.

### Шаг 5.10. Запустите bot

Оставьте первое окно CMD открытым:

```cmd
cd /d "C:\Projects\tg-stars-seller"
.venv\Scripts\python.exe -m app.main
```

Остановка: `Ctrl + C`.

### Шаг 5.11. Запустите admin panel

Откройте второе окно CMD:

```cmd
cd /d "C:\Projects\tg-stars-seller"
set PROCESS_ROLE=admin
.venv\Scripts\python.exe -m app.admin.server
```

Откройте в браузере:

```text
http://127.0.0.1:8080/admin
```

Войдите с username OWNER, паролем и шестизначным TOTP-кодом.

### Шаг 5.12. Проведите безопасную тестовую покупку

1. Откройте bot и нажмите `/start`.
2. Выберите покупку себе.
3. Выберите 50 Stars или отправьте число сообщением.
4. Проверьте получателя и цену.
5. Создайте заказ.
6. Нажмите тестовое подтверждение оплаты.
7. Убедитесь, что заказ дошёл до `completed`.
8. Откройте заказ в admin panel и проверьте audit timeline.

В этом режиме реальные деньги и Stars не расходуются.

### Шаг 5.13. Автоматические проверки

Остановите bot перед тестами, затем выполните:

```cmd
.venv\Scripts\python.exe -m ruff format --check app migrations tests scripts
.venv\Scripts\python.exe -m ruff check app migrations tests scripts
.venv\Scripts\python.exe -m mypy app tests
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m app.operations dry-run
```

Dry-run всегда использует временную БД, тестовый provider и не отправляет покупку в сеть.

## 6. Локальный PostgreSQL на Windows

Для простого UI-теста достаточно SQLite. PostgreSQL нужен для проверки production-подобной
конкурентности и обязателен для реальных денег.

### Шаг 6.1. Установите PostgreSQL

1. Откройте https://www.postgresql.org/download/windows/.
2. Скачайте официальный Windows installer, на который ссылается PostgreSQL.
3. Выберите поддерживаемую стабильную версию PostgreSQL 17 или 16 x64.
4. Достаточно компонентов `PostgreSQL Server` и `Command Line Tools`.
5. Задайте новый пароль superuser `postgres`.
6. Оставьте локальный порт `5432`.
7. Не открывайте порт 5432 в Windows Firewall.

Проверка в CMD после установки; путь версии при необходимости измените:

```cmd
"C:\Program Files\PostgreSQL\17\bin\psql.exe" --version
"C:\Program Files\PostgreSQL\17\bin\pg_isready.exe" -h 127.0.0.1 -p 5432
```

### Шаг 6.2. Создайте БД и роли

Откройте SQL Shell (`psql`) из меню Пуск либо выполните:

```cmd
"C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h 127.0.0.1 -p 5432
```

Введите пароль `postgres`. Затем последовательно выполните SQL, заменив оба placeholder-пароля
на разные длинные буквенно-цифровые строки:

```sql
CREATE ROLE terstars_migrator LOGIN PASSWORD 'CHANGE_MIGRATOR_PASSWORD';
CREATE DATABASE terstars OWNER terstars_migrator;
\connect terstars
CREATE ROLE terstars_runtime LOGIN PASSWORD 'CHANGE_RUNTIME_PASSWORD';
GRANT CONNECT ON DATABASE terstars TO terstars_runtime;
GRANT USAGE ON SCHEMA public TO terstars_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE terstars_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO terstars_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE terstars_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO terstars_runtime;
\quit
```

### Шаг 6.3. Выполните миграции

PowerShell удобнее для временной переменной:

```powershell
Set-Location "C:\Projects\tg-stars-seller"
$env:DATABASE_URL="postgresql+asyncpg://terstars_migrator:CHANGE_MIGRATOR_PASSWORD@127.0.0.1:5432/terstars"
.\.venv\Scripts\alembic.exe upgrade head
Remove-Item Env:DATABASE_URL
```

После миграций снова войдите в `psql` и выдайте runtime-роли права на уже созданные объекты:

```sql
\connect terstars
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO terstars_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO terstars_runtime;
\quit
```

### Шаг 6.4. Переключите приложение на PostgreSQL

В `.env` замените `DATABASE_URL`:

```env
DATABASE_URL=postgresql+asyncpg://terstars_runtime:CHANGE_RUNTIME_PASSWORD@127.0.0.1:5432/terstars
```

Если пароль содержит `@`, `:`, `/`, `#` или другие специальные символы URL, его нужно
percent-encode. Для первого локального запуска проще использовать длинный случайный пароль из
букв и цифр.

Создайте OWNER заново уже в PostgreSQL и запустите bot/admin по шагам 5.9–5.11.

## 7. Как полностью сбросить только локальную тестовую БД

Остановите bot и admin. SQLite содержит локальные заказы и OWNER. Для резервного сброса в
PowerShell:

```powershell
Set-Location "C:\Projects\tg-stars-seller"
New-Item -ItemType Directory -Force .\local-backup | Out-Null
Move-Item -LiteralPath .\terstars.db -Destination .\local-backup\terstars-before-reset.db
.\.venv\Scripts\python.exe -m app.admin.cli create-owner --username owner
```

Не выполняйте это для production PostgreSQL. Production восстанавливается только из проверенного
backup по отдельному плану.

## 8. Подготовка к продаже/передаче проекта

Перед передачей выполните secret scan и удалите runtime-артефакты:

- `.env`;
- `secrets/`;
- `sessions/`;
- `.venv/`;
- `logs/`, `backups/`;
- `terstars.db`, `*.session`, `*.dump`;
- `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`.

Не удаляйте `.env.example` и `.env.production.example`: это безопасные шаблоны.

Если credential когда-либо был отправлен третьему лицу или попал в Git history, его нужно
отозвать. Простого удаления файла недостаточно.

## 9. Production: рекомендуемая схема VPS

Рекомендуется один VPS с Ubuntu 24.04 LTS, минимум 2 vCPU, 2 GB RAM, 20 GB SSD и публичным IPv4.
Docker Compose является единственным поддерживаемым production-способом запуска.

Публично открываются только:

- `22/tcp` — SSH;
- `80/tcp` — получение сертификата/redirect;
- `443/tcp` — webhook и return page ЮKassa.

Не открываются PostgreSQL 5432, admin 8080, gateway 8090, worker и internal metrics.

## 10. Первичная настройка VPS

### Шаг 10.1. Подключитесь как root

Локальный CMD:

```cmd
ssh root@VPS_IP
```

### Шаг 10.2. Обновите систему и создайте deploy-user

В терминале VPS:

```bash
apt update
apt upgrade -y
apt install -y ca-certificates curl openssl ufw unzip
adduser deploy
usermod -aG sudo deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
```

В новом локальном CMD проверьте:

```cmd
ssh deploy@VPS_IP
```

Только после успешного входа по ключу отключайте root/password login в `/etc/ssh/sshd_config`:

```text
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
```

Проверка и reload:

```bash
sudo sshd -t
sudo systemctl reload ssh
```

Не закрывайте первую root-сессию до проверки второго входа.

### Шаг 10.3. Настройте firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

Учтите особенности совместной работы Docker и firewall из официальной документации:
https://docs.docker.com/engine/install/ubuntu/.

### Шаг 10.4. Установите Docker из официального репозитория

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker deploy
```

Выйдите из SSH и войдите снова, затем проверьте:

```bash
docker version
docker compose version
docker run --rm hello-world
```

Членство в группе `docker` фактически даёт root-подобные полномочия. Не добавляйте туда
посторонних пользователей.

## 11. Передача проекта на VPS

На локальном компьютере создайте архив без секретов и runtime-файлов. Можно использовать ZIP
Проводника, предварительно убедившись, что перечисленных в разделе 8 каталогов нет.

Передайте архив из локального CMD:

```cmd
scp "C:\Projects\terstars-release.zip" deploy@VPS_IP:/home/deploy/
```

На VPS:

```bash
mkdir -p /home/deploy/terstars
unzip /home/deploy/terstars-release.zip -d /home/deploy/terstars
cd /home/deploy/terstars
ls -la
```

Если после распаковки появился дополнительный вложенный каталог, перейдите в него. Команда
`ls` должна показывать `app`, `Dockerfile`, `docker-compose.yml`.

## 12. Production secrets на VPS

Создайте каталог:

```bash
cd /home/deploy/terstars
mkdir -p secrets
chmod 700 secrets
```

Сгенерируйте разные пароли PostgreSQL и соответствующие URL. Эти команды используют hex без
URL-спецсимволов:

```bash
POSTGRES_PASSWORD="$(openssl rand -hex 32)"
RUNTIME_PASSWORD="$(openssl rand -hex 32)"
printf '%s' "$POSTGRES_PASSWORD" > secrets/postgres_password
printf '%s' "$RUNTIME_PASSWORD" > secrets/runtime_database_password
printf 'postgresql+asyncpg://terstars_migrator:%s@postgres:5432/terstars' "$POSTGRES_PASSWORD" > secrets/migration_database_url
printf 'postgresql+asyncpg://terstars_runtime:%s@postgres:5432/terstars' "$RUNTIME_PASSWORD" > secrets/runtime_database_url
unset POSTGRES_PASSWORD RUNTIME_PASSWORD
```

Безопасно введите bot token и Telegram API hash без записи в shell history:

```bash
read -rsp 'Bot token: ' BOT_TOKEN && echo
printf '%s' "$BOT_TOKEN" > secrets/bot_token
unset BOT_TOKEN
read -rsp 'Telegram API hash: ' TELEGRAM_API_HASH && echo
printf '%s' "$TELEGRAM_API_HASH" > secrets/telegram_api_hash
unset TELEGRAM_API_HASH
```

Для test-магазина ЮKassa:

```bash
read -rp 'YooKassa shopId: ' YOOKASSA_SHOP_ID
printf '%s' "$YOOKASSA_SHOP_ID" > secrets/yookassa_shop_id
unset YOOKASSA_SHOP_ID
read -rsp 'YooKassa secret key: ' YOOKASSA_SECRET_KEY && echo
printf '%s' "$YOOKASSA_SECRET_KEY" > secrets/yookassa_secret_key
unset YOOKASSA_SECRET_KEY
```

Seed создавайте только для отдельного hot wallet. Не используйте основной кошелёк:

```bash
read -rsp 'Fragment hot-wallet 24-word seed: ' FRAGMENT_SEED && echo
printf '%s' "$FRAGMENT_SEED" > secrets/fragment_wallet_seed
unset FRAGMENT_SEED
chmod 600 secrets/*
```

Не вводите seed прямо в команду `echo "word1 ..."`, потому что она попадёт в shell history.

## 13. Production `.env.production`

```bash
cd /home/deploy/terstars
cp .env.production.example .env.production
chmod 600 .env.production
nano .env.production
```

Замените:

```env
PAYMENT_PUBLIC_HOST=pay.example.com
TELEGRAM_API_ID=12345678
TELEGRAM_USER_PHONE=+70000000000
ADMIN_IDS=123456789
YOOKASSA_RETURN_URL=https://pay.example.com/payments/return
YOOKASSA_WEBHOOK_URL=https://pay.example.com/webhooks/yookassa
```

Первый безопасный запуск должен оставаться закрытым:

```env
TEST_PAYMENT_MODE=false
CUSTOMER_PAYMENT_PROVIDER=yookassa
YOOKASSA_TEST_MODE=true
REAL_STARS_PURCHASE_ENABLED=false
PURCHASES_ENABLED=false
MAINTENANCE_MODE=true
YOOKASSA_RECEIPT_MODE=owner_decision_required
OPERATIONAL_BALANCE_CONFIRMED_STARS=0
```

`YOOKASSA_RECEIPT_MODE=owner_decision_required` намеренно блокирует ошибочный live. Только после
официального решения по чекам допускается поддерживаемое договором значение.

## 14. Домен и DNS

В панели регистратора создайте A-запись:

```text
Тип: A
Имя: pay
Значение: VPS_IP
TTL: 300 или Auto
```

Проверьте с компьютера:

```cmd
nslookup pay.example.com
```

Адрес должен указывать на VPS. Caddy автоматически получает TLS-сертификат после запуска, если
DNS корректен и порты 80/443 доступны.

Публично маршрутизируются только `/webhooks/yookassa` и `/payments/return`. `/admin` через Caddy
не публикуется.

## 15. Первый Compose-запуск

Проверьте конфигурацию и соберите image:

```bash
cd /home/deploy/terstars
docker compose --env-file .env.production config --quiet
docker compose --env-file .env.production build --pull
docker compose --env-file .env.production up -d postgres migrate
docker compose --env-file .env.production ps
docker compose --env-file .env.production logs migrate
```

Migration должна завершиться с exit code 0.

### Создайте Telegram session внутри volume

```bash
docker compose --env-file .env.production --profile setup run --rm telegram-session
```

Введите код Telegram и 2FA при запросе. Session останется в Docker volume и не должна попадать в
backup исходников.

### Создайте OWNER

```bash
docker compose --env-file .env.production --profile setup run --rm admin-cli
```

Введите пароль дважды и добавьте показанный TOTP secret в authenticator.

### Запустите сервисы

```bash
docker compose --env-file .env.production up -d
docker compose --env-file .env.production ps
docker compose --env-file .env.production logs --tail=100 bot purchase-worker payment-gateway caddy admin
```

## 16. Доступ к закрытой admin panel

Не открывайте порт 8080 в firewall. На локальном Windows-компьютере откройте CMD и оставьте
команду работающей:

```cmd
ssh -N -L 8080:127.0.0.1:8080 deploy@VPS_IP
```

Откройте:

```text
http://127.0.0.1:8080/admin
```

Соединение ПК→VPS шифруется SSH, а admin на VPS остаётся loopback-only. SSH-туннель не заменяет
пароль и TOTP.

## 17. Подключение ЮKassa

Официальная документация: https://yookassa.ru/developers.

### Шаг 17.1. Создайте магазин

Зарегистрируйтесь в ЮKassa, пройдите проверку и создайте test-магазин. В личном кабинете нужны:

- `shopId` из настроек магазина;
- test secret key из раздела API-интеграции;
- затем отдельный live secret key.

Проект использует документированный HTTP Basic Auth: shopId как username, secret key как
password. Значения хранятся только в Docker secrets.

### Шаг 17.2. Решите вопрос чеков

До live уточните у ЮKassa/бухгалтера:

- нужна ли встроенная онлайн-касса;
- должен ли запрос содержать `receipt`;
- какие email/phone нужны от клиента;
- VAT code, payment subject/mode и налоговый режим;
- кто формирует и отправляет чек.

Код намеренно не угадывает налоговые значения. Нельзя ставить
`disabled_by_contract`, если это не подтверждено вашим договором и режимом.

### Шаг 17.3. Настройте webhook

Для Basic Auth уведомления настраиваются в личном кабинете ЮKassa. URL:

```text
https://pay.example.com/webhooks/yookassa
```

Нужные события:

- `payment.succeeded`;
- `payment.canceled`;
- `refund.succeeded`;
- `payment.waiting_for_capture`, если выбранный сценарий его использует.

Webhook не доверяет входящему `succeeded`: приложение получает актуальный payment через API и
сверяет ID, RUB, сумму и metadata.

### Шаг 17.4. Выполните test payment

Оставьте:

```env
YOOKASSA_TEST_MODE=true
REAL_STARS_PURCHASE_ENABLED=false
PURCHASES_ENABLED=false
MAINTENANCE_MODE=true
```

Перезапустите сервисы и создайте тестовый заказ. Проверьте:

- redirect ведёт на официальный confirmation URL;
- webhook приходит по HTTPS;
- payment виден в admin;
- повтор webhook не меняет результат второй раз;
- Stars не покупаются;
- кнопка «Проверить статус» делает GET/reconciliation;
- canceled payment не становится paid.

## 18. Fragment hot wallet

Создайте отдельный кошелёк только для проекта. Держите на нём не больше утверждённого дневного
лимита и резерва комиссии. Основной/накопительный кошелёк не используйте.

Выберите актив:

```env
FRAGMENT_PAYMENT_METHOD=ton
```

или:

```env
FRAGMENT_PAYMENT_METHOD=usdt_ton
```

`usdt_ton` означает USDT в сети TON. Пополните именно выбранный актив. Non-KYC режим использует
документированную комиссию Fragment, которую приложение получает из API. Для KYC режима нужны
собственные Fragment cookies; они также являются секретом.

Seed монтируется только в `purchase-worker`. Не добавляйте endpoint её просмотра и не показывайте
её в admin.

## 19. Production preflight и переход в live

### Безопасный dry-run

```bash
docker compose --env-file .env.production --profile ops run --rm dry-run
```

### Preflight

```bash
docker compose --env-file .env.production --profile ops run --rm preflight
```

Preflight не создаёт payment и не покупает Stars. Он проверяет configuration, БД, migrations,
Telegram, Fragment, ЮKassa, webhook, OWNER, worker heartbeat, idempotency storage и safety gates.

### Контролируемый live cutover

Только после test-магазина, решения по чекам, backup и проверки hot wallet:

- замените test shopId/key на новые live credentials;
- установите `YOOKASSA_TEST_MODE=false`;
- установите `REAL_STARS_PURCHASE_ENABLED=true`;
- задайте подтверждённое значение `OPERATIONAL_BALANCE_CONFIRMED_STARS`;
- пока оставьте `PURCHASES_ENABLED=false` и `MAINTENANCE_MODE=true`;
- перезапустите и добейтесь preflight без ERROR/WARNING;
- выполните одну минимальную покупку под наблюдением владельца;
- убедитесь, что существует ровно один YooKassa payment и один Fragment request;
- только после сверки отключите maintenance и включите покупки.

Telegram admin-команды:

```text
/health
/fragment_status
/stats
/maintenance off
/purchases on CONFIRM
```

## 20. Основные admin-команды

```text
/order ID
/user_orders TELEGRAM_ID
/stuck
/manual_review
/reconcile ID
/retry ID CONFIRM
/cancel_order ID CONFIRM
/refund ID CONFIRM
/stats
/health
/fragment_status
/maintenance on|off|status
/purchases on CONFIRM
/purchases off
```

`/reconcile` проверяет сохранённый provider request и не отправляет новый purchase POST.
`/refund` не должен использоваться при неопределённом результате Fragment.

## 21. Backup и восстановление

Создайте каталог:

```bash
cd /home/deploy/terstars
mkdir -p backups
chmod 700 backups
```

Ручной backup:

```bash
docker compose --env-file .env.production exec postgres pg_dump -U terstars_migrator -d terstars -Fc -f /tmp/terstars.dump
docker compose --env-file .env.production cp postgres:/tmp/terstars.dump ./backups/terstars-$(date +%Y%m%d-%H%M%S).dump
```

Проверьте, что файл не пустой, зашифруйте копию и храните её вне VPS. Backup БД содержит IDs,
username snapshots и финансовую историю, но не seed. Каталог `secrets/` копируется отдельно в
защищённое хранилище, не вместе с обычным backup.

Проверку restore выполняйте в отдельной staging-БД, не поверх production.

## 22. Логи, monitoring и обслуживание

Статус:

```bash
docker compose --env-file .env.production ps
docker compose --env-file .env.production logs --since=10m
docker stats --no-stream
df -h
free -h
```

Логи Docker ограничены ротацией. Не переключайте `LOG_LEVEL=DEBUG` постоянно. Настройте внешний
alert хотя бы на недоступность HTTPS, заполнение диска, low hot-wallet balance, provider errors и
заказы `MANUAL_REVIEW`.

## 23. Обновление проекта

1. Включите maintenance и остановите новые покупки.
2. Создайте backup.
3. Передайте новую версию исходников без `.env` и secrets.
4. Проверьте изменения и dependency lock.
5. Выполните:

```bash
docker compose --env-file .env.production build --pull
docker compose --env-file .env.production up -d postgres migrate
docker compose --env-file .env.production up -d
docker compose --env-file .env.production ps
docker compose --env-file .env.production logs --since=5m
```

После выполнения команд запустите preflight и reconciliation тестового заказа.

Только после успешной проверки снимайте maintenance.

## 24. Аварийная остановка

Если есть риск двойной покупки, неверной цены или компрометации:

```text
/maintenance on
/purchases off
```

Затем остановите worker:

```bash
docker compose --env-file .env.production stop purchase-worker
```

Bot может продолжать сохранять статусы, но новые Fragment POST не отправляются. Проверьте admin,
`/stuck`, `/manual_review`, ЮKassa и историю Fragment. Не просите клиента платить повторно.

При утечке seed выведите средства на новый кошелёк и замените secret. При утечке bot token
выполните `/revoke` в BotFather. При утечке Telegram session завершите её в Telegram → Настройки →
Устройства. При утечке ЮKassa key перевыпустите ключ в кабинете.

## 25. Диагностика распространённых проблем

### `Passwords do not match`

Пароли OWNER при первом и втором вводе различаются. Повторите команду и вводите одинаковый
пароль. Символы не отображаются.

### «Длинный секрет», а поле просит 6 цифр

Длинная строка — TOTP secret для добавления в authenticator. Поле входа принимает текущий
шестизначный код, созданный приложением-аутентификатором.

### Bot не отвечает

Проверьте token, интернет, что bot не запущен вторым экземпляром, и логи:

```bash
docker compose --env-file .env.production logs --tail=200 bot
```

### Telegram session не создаётся

Проверьте `TELEGRAM_API_ID`, API hash, номер в международном формате и 2FA. Удалять рабочую
session без необходимости не нужно.

### Admin не открывается с VPS_IP:8080

Это правильно: порт закрыт. Используйте SSH-туннель и открывайте `127.0.0.1:8080` локально.

### PostgreSQL connection refused

```bash
docker compose --env-file .env.production ps postgres
docker compose --env-file .env.production logs postgres
```

Проверьте, что URL внутри Compose использует hostname `postgres`, а не `127.0.0.1`.

### Payment succeeded, но Stars не отправлены

Не создавайте второй payment и не делайте автоматический refund. Проверьте заказ, worker,
provider request ID и выполните reconciliation. При неопределённом результате оставьте
`MANUAL_REVIEW`.

### Preflight сообщает test mode

Для sandbox это ожидаемо. Для live проверьте test/live credentials, receipt decision,
`REAL_STARS_PURCHASE_ENABLED`, maintenance, purchases, PostgreSQL, OWNER и heartbeat worker.

## 26. Контрольный checklist перед первой реальной продажей

- [ ] У нового владельца собственный bot token.
- [ ] Созданы собственные API ID/hash и Telegram session.
- [ ] Создан новый OWNER с TOTP.
- [ ] Старые credentials отсутствуют в проекте и истории.
- [ ] Используется отдельный hot wallet с ограниченным балансом.
- [ ] PostgreSQL не опубликован наружу.
- [ ] Admin доступен только через SSH-туннель.
- [ ] DNS и HTTPS работают.
- [ ] Test-магазин ЮKassa проверен.
- [ ] Решение по чекам/54-ФЗ подтверждено.
- [ ] Webhook и duplicate webhook протестированы.
- [ ] Payment reconciliation виден в admin.
- [ ] Backup создан и restore проверен на staging.
- [ ] Dry-run успешен.
- [ ] Production preflight без ERROR/WARNING.
- [ ] Минимальная live-покупка одобрена владельцем.
- [ ] Один payment создал ровно одну выдачу.
- [ ] Monitoring и аварийная остановка понятны владельцу.

## 27. Что проект не делает за владельца

Код не заменяет регистрацию бизнеса, договор с ЮKassa, бухгалтерию, решение по чекам,
пополнение hot wallet, юридическую проверку правил Telegram/Fragment, поддержку клиентов и
мониторинг инфраструктуры. До продажи услуг проверьте актуальные условия провайдеров и
законодательство вашей юрисдикции.

## 28. Официальные ссылки

- Telegram API ID/hash: https://my.telegram.org
- Telegram API documentation: https://core.telegram.org/api
- Fragment Stars API: https://api-fragment.duckdns.org
- PostgreSQL Windows: https://www.postgresql.org/download/windows/
- Docker Engine Ubuntu: https://docs.docker.com/engine/install/ubuntu/
- ЮKassa API: https://yookassa.ru/developers
- Формат/Basic Auth ЮKassa: https://yookassa.ru/developers/using-api/interaction-format
- Webhook ЮKassa: https://yookassa.ru/developers/using-api/webhooks

## 29. Короткий маршрут нового владельца

1. Получить чистый архив без credentials.
2. Создать bot, API ID/hash и numeric admin ID.
3. Запустить локальный SQLite test mode.
4. Создать Telegram session и OWNER.
5. Провести тестовый заказ и проверить admin.
6. Купить VPS, настроить SSH/Docker/firewall.
7. Создать новые secrets и `.env.production`.
8. Настроить DNS, HTTPS и test-магазин ЮKassa.
9. Запустить PostgreSQL/migrations/session/OWNER через Compose.
10. Проверить sandbox webhook и reconciliation без выдачи Stars.
11. Создать и минимально пополнить отдельный hot wallet.
12. Выполнить backup, dry-run и production preflight.
13. Провести одну контролируемую минимальную live-покупку.
14. Только после сверки открыть продажи.
