# Размещение на VPS (Ubuntu + Docker Compose)

Эта инструкция предполагает один небольшой VPS, домен для YooKassa и приватную админку через SSH-туннель.

## Порты

| Порт | Доступ | Назначение |
|---|---|---|
| 22/TCP | ваш IP/интернет | SSH |
| 80/TCP | публичный | сертификат и redirect на HTTPS |
| 443/TCP, 443/UDP | публичный | webhook/return YooKassa |
| 8080/TCP | только `127.0.0.1` VPS | админка через SSH-туннель |
| 5432, 8090 | только Docker networks | PostgreSQL и payment gateway |

## 1. Подготовка VPS

Подключитесь из CMD или PowerShell:

```powershell
ssh root@VPS_IP
```

На VPS:

```bash
apt update
apt upgrade -y
apt install -y ca-certificates curl git ufw
curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
sh /tmp/get-docker.sh
docker compose version
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw enable
```

После проверки Docker удалите `/tmp/get-docker.sh`. Создайте отдельного пользователя для приложения и добавьте его в группу `docker`; помните, что эта группа эквивалентна root-доступу.

## 2. Доставка проекта

На ПК упакуйте только исходники, без `.env`, БД, session, backup и secrets:

```powershell
cd "C:\Projects\tg-stars-seller"
tar --exclude=.venv --exclude=.env --exclude=.env.production --exclude=secrets --exclude=sessions --exclude=backups --exclude=logs --exclude=.cache --exclude=__pycache__ -czf ..\terstars-deploy.tgz .
scp ..\terstars-deploy.tgz app@VPS_IP:/srv/
```

На VPS:

```bash
mkdir -p /srv/terstars
tar -xzf /srv/terstars-deploy.tgz -C /srv/terstars
cd /srv/terstars
cp .env.production.example .env.production
mkdir -p secrets backups/postgres
chmod 700 secrets backups backups/postgres
```

## 3. Конфигурация и secrets

Отредактируйте `.env.production`: домен, Telegram API ID/phone, owner ID, лимиты, Fragment asset. Не включайте продажи: первоначально `MAINTENANCE_MODE=true`, `PURCHASES_ENABLED=false`, `YOOKASSA_TEST_MODE=true`.

Создайте файлы из `.env.production.example`. Каждый содержит ровно одно значение:

```text
postgres_password
runtime_database_password
migration_database_url
runtime_database_url
bot_token
telegram_api_hash
fragment_wallet_seed
fragment_cookies
yookassa_shop_id
yookassa_secret_key
```

Права: `chmod 600 secrets/*`. URL runtime использует `terstars_runtime`, migration — `terstars_migrator`; спецсимволы пароля в URL должны быть percent-encoded. Seed — отдельный Fragment hot wallet с ограниченным остатком; он монтируется только в `purchase-worker`. KYC cookies подготовьте по [`FRAGMENT_KYC_SETUP.md`](FRAGMENT_KYC_SETUP.md); не вставляйте их напрямую в `.env.production`.

## 4. DNS, сборка и первый старт

Создайте A-запись `PAYMENT_PUBLIC_HOST` на IPv4 VPS, дождитесь распространения DNS. Затем:

```bash
cd /srv/terstars
docker compose --env-file .env.production config --quiet
docker compose --env-file .env.production build --pull
docker compose --env-file .env.production up -d postgres migrate
docker compose --env-file .env.production logs migrate
```

Создайте Telethon session в Docker volume (команда интерактивна):

```bash
docker compose --env-file .env.production --profile setup run --rm telegram-session
```

Создайте владельца админки; пароль вводится дважды, длинный TOTP secret добавляется в приложение-аутентификатор, а в форме входа вводится текущий шестизначный код:

```bash
docker compose --env-file .env.production --profile setup run --rm admin-cli
```

Запустите всё с закрытыми продажами:

```bash
docker compose --env-file .env.production up -d
docker compose --env-file .env.production ps
docker compose --env-file .env.production logs --tail=100 bot purchase-worker payment-gateway caddy
```

## 5. Админка только локально

На своём ПК откройте отдельный CMD и держите его запущенным:

```powershell
ssh -N -L 8080:127.0.0.1:8080 app@VPS_IP
```

Открывайте `http://127.0.0.1:8080/admin`. Порт 8080 не открывать в firewall/security group и не добавлять в Caddy.

## 6. Проверки и backup

```bash
docker compose --env-file .env.production --profile ops run --rm dry-run
docker compose --env-file .env.production --profile ops run --rm preflight
BACKUP_DIR=/srv/terstars/backups/postgres sh scripts/backup_postgres.sh
```

Добавьте backup в root cron ежедневно и копируйте зашифрованную копию вне VPS. Раз в месяц проверяйте restore в отдельную staging-БД. Перед миграцией всегда создавайте свежий backup.

Проверка рестарта:

```bash
docker compose --env-file .env.production restart bot purchase-worker payment-gateway admin
docker compose --env-file .env.production ps
docker compose --env-file .env.production logs --since=5m
```

## 7. Controlled go-live

Выполните `PRODUCTION_CHECKLIST.md`, `docs/YOOKASSA_SETUP.md` и [`FRAGMENT_KYC_SETUP.md`](FRAGMENT_KYC_SETUP.md). Сначала официальный test-shop сценарий, затем новые live credentials и минимальный заказ. Только после ручной сверки YooKassa, PostgreSQL, Fragment и получения Stars отключите maintenance и включите покупки. Не включайте live, если preflight возвращает хотя бы ERROR/WARNING.
