# Fragment KYC: безопасное подключение

KYC-режим Fragment использует cookies аккаунта владельца и заявленную комиссию API `0%`.
Cookies и seed рабочего кошелька являются критическими секретами. Не отправляйте их в чат,
почту, тикеты или Git.

## 1. Подготовьте отдельный аккаунт и кошелёк

1. Используйте отдельный operational hot wallet, а не основной кошелёк.
2. Держите на нём только утверждённый дневной запас и TON для комиссии сети.
3. Войдите на `https://fragment.com` под рабочим Telegram-аккаунтом.
4. Завершите требуемую Fragment проверку личности.
5. Подключите именно operational hot wallet. Без этого cookie `stel_ton_token` не позволит
   совершать покупки.

## 2. Получите четыре cookies

В Chrome или Edge откройте Fragment, затем нажмите `F12` → `Application` → `Cookies` →
`https://fragment.com`. Нужны значения:

- `stel_token`;
- `stel_ssid`;
- `stel_ton_token`;
- `stel_dt`.

Создайте локальный файл `secrets/fragment_cookies.json`:

```json
{
  "stel_token": "VALUE",
  "stel_ssid": "VALUE",
  "stel_ton_token": "VALUE",
  "stel_dt": "VALUE"
}
```

Каталог `secrets/` исключён из Git. Не сохраняйте этот JSON в другом каталоге проекта.

## 3. Закодируйте secret на Windows

Откройте PowerShell в папке проекта и выполните:

```powershell
$json = Get-Content -Raw -LiteralPath ".\secrets\fragment_cookies.json"
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
[IO.File]::WriteAllText("$PWD\secrets\fragment_cookies", $encoded, [Text.UTF8Encoding]::new($false))
Remove-Item -LiteralPath ".\secrets\fragment_cookies.json"
```

Команда не печатает cookies в терминал. Итоговый файл — `secrets/fragment_cookies`.

## 4. Настройте production

В `.env.production` должны быть только ссылки и режим, но не сами cookies:

```env
FRAGMENT_API_MODE=kyc
FRAGMENT_COOKIES_SECRET_FILE=./secrets/fragment_cookies
FRAGMENT_PAYMENT_METHOD=ton
```

Для оплаты из USDT в сети TON используйте:

```env
FRAGMENT_PAYMENT_METHOD=usdt_ton
```

Docker Compose передаёт cookies только `purchase-worker` и одноразовому `preflight`. Bot,
admin и payment-gateway этот secret не получают.

## 5. Передайте secret на VPS

Передавайте файл по SSH, а не через Telegram:

```powershell
scp ".\secrets\fragment_cookies" root@VPS_IP:/opt/terstars/secrets/fragment_cookies
```

На VPS ограничьте права:

```bash
cd /opt/terstars
chmod 600 secrets/fragment_cookies
```

## 6. Проверка без покупки

Пока оставьте:

```env
MAINTENANCE_MODE=true
PURCHASES_ENABLED=false
```

Проверьте Compose и preflight:

```bash
docker compose --env-file .env.production config --quiet
docker compose --env-file .env.production --profile ops run --rm preflight
```

Preflight проверяет наличие cookies, Base64, JSON и четыре обязательных поля. Он не совершает
покупку и не может гарантировать, что Fragment-сессия ещё действительна.

## 7. Контролируемая проверка KYC

Действительность cookies окончательно подтверждается только одной минимальной покупкой:

1. Оставьте публичный приём заказов выключенным.
2. Пополните hot wallet на сумму минимальной покупки и небольшой gas reserve.
3. Создайте один заказ на 50 Stars для своего аккаунта.
4. Вручную разрешите только этот тестовый заказ.
5. Проверьте, что в БД и Fragment появился один request ID и одна транзакция.
6. Убедитесь, что в котировке комиссия KYC равна `0%`.
7. Только после сверки включайте приём заказов.

При `INVALID_FRAGMENT_COOKIES` заново войдите на Fragment, подключите wallet, получите новый
набор cookies и замените secret-файл. При `FRAGMENT_ADDITIONAL_VERIFICATION_REQUIRED` откройте
Fragment вручную и завершите дополнительную проверку. Не повторяйте неопределённую покупку, пока
не проверены request ID, кошелёк и история Fragment.

## 8. Ротация и инциденты

- Обновляйте cookies после завершения сессии или ошибки авторизации.
- После замены перезапускайте только `purchase-worker` и повторяйте preflight.
- Если cookies попали в чат, лог или архив, завершите Fragment-сессию и получите новые.
- При подозрении на компрометацию выключите покупки, остановите worker и выведите остаток с hot
  wallet.

Источники: документация `fragment-stars-api` — `https://pypi.org/project/fragment-stars-api/` и
`https://api-fragment.duckdns.org`.
