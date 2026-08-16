# YooKassa: настройка и безопасный запуск

Дата сверки документации: 11 августа 2026 года. Используются только официальные материалы YooKassa:

- [процесс платежа](https://yookassa.ru/developers/payment-acceptance/getting-started/payment-process);
- [формат API и Basic Auth](https://yookassa.ru/developers/using-api/interaction-format);
- [входящие уведомления](https://yookassa.ru/developers/using-api/webhooks);
- [тестирование](https://yookassa.ru/developers/payment-acceptance/testing-and-going-live/testing);
- [чеки по 54-ФЗ](https://yookassa.ru/developers/payment-acceptance/receipts/54fz/yoomoney/basics);
- [значения параметров чека](https://yookassa.ru/developers/payment-acceptance/receipts/54fz/yoomoney/parameters-values).

## Что уже реализовано

Покупатель получает redirect-ссылку YooKassa. Сумма берётся только из сохранённого заказа. Для создания платежа используется `capture=true`, metadata с внутренним ID/номером заказа и стабильный UUID `Idempotence-Key`.

`return_url` — только страница «вернитесь в Telegram». Она никогда не подтверждает оплату. Webhook тоже не считается доказательством сам по себе: сервер выполняет `GET /v3/payments/{payment_id}`, затем сверяет payment ID, status, `paid`, RUB-сумму и metadata с PostgreSQL. Только проверенный `succeeded` переводит заказ в `PAID`; уникальные ограничения и блокировка строки не позволяют запустить две покупки Stars.

Поддержаны состояния `pending`, `waiting_for_capture`, `succeeded`, `canceled`, повторные webhook и ручная проверка из бота/админки. Неопределённый результат Fragment запрещает возврат. Возврат запускается только подтверждённым действием администратора и имеет отдельный стабильный ключ идемпотентности.

## Что нужно получить у YooKassa

1. Зарегистрировать магазин и пройти требуемую YooKassa идентификацию.
2. Получить отдельные данные тестового магазина: `shopId` и `secret key`.
3. Иметь домен, например `pay.example.ru`, с A/AAAA-записью на VPS.
4. Решить с бухгалтером схему чеков, НДС, предмет и способ расчёта.

Важное блокирующее решение: `YOOKASSA_RECEIPT_MODE=owner_decision_required` намеренно не даёт платёжному сервису запуститься. Код не угадывает налоговые параметры. Если по договору чеки через YooKassa не нужны, после письменного решения установите `disabled_by_contract`. Если нужны — сначала добавьте сбор email покупателя и корректные `receipt.items`; не подменяйте контакт покупателя адресом владельца.

## Секреты и конфигурация

Скопируйте `.env.production.example` в `.env.production`. В нём задаются только несекретные параметры:

```env
CUSTOMER_PAYMENT_PROVIDER=yookassa
YOOKASSA_API_BASE_URL=https://api.yookassa.ru/v3
PAYMENT_PUBLIC_HOST=pay.example.ru
YOOKASSA_RETURN_URL=https://pay.example.ru/payments/return
YOOKASSA_WEBHOOK_URL=https://pay.example.ru/webhooks/yookassa
YOOKASSA_TEST_MODE=true
YOOKASSA_RECEIPT_MODE=owner_decision_required
```

Создайте два файла вне Git, по одному значению без кавычек:

```text
secrets/yookassa_shop_id
secrets/yookassa_secret_key
```

Ограничьте каталог и файлы владельцем (`chmod 700 secrets`, `chmod 600 secrets/*`). Не помещайте значения в `.env`, команды, Telegram, issue, скриншоты или логи. Секрет не отображается в админке и API-ответах.

## HTTPS и webhook

Compose запускает Caddy. Наружу открыты только TCP 80/443 и UDP 443. Caddy автоматически получает сертификат и публикует ровно:

- `POST /webhooks/yookassa`;
- `GET /payments/return`.

Админка не проходит через Caddy и остаётся на `127.0.0.1:8080` VPS. PostgreSQL и порт gateway `8090` наружу не публикуются.

В личном кабинете YooKassa укажите `https://pay.example.ru/webhooks/yookassa` и события:

- `payment.waiting_for_capture`;
- `payment.succeeded`;
- `payment.canceled`.
- `refund.succeeded`.

YooKassa не документирует универсальную подпись этого Basic Auth webhook-потока, поэтому код не использует выдуманный HMAC. Подлинность и актуальность проверяются повторным GET к YooKassa. Тело ограничено 64 KiB, проверяются метод, Content-Type, JSON-схема и длина ID. Успешно обработанное или уже известное событие получает HTTP 200; временная ошибка проверки — 503, чтобы YooKassa повторила доставку.

## Тестовый запуск

1. Оставьте `MAINTENANCE_MODE=true`, `PURCHASES_ENABLED=false`, `YOOKASSA_TEST_MODE=true` и `REAL_STARS_PURCHASE_ENABLED=false`. Test-платёж никогда не должен расходовать настоящий Fragment wallet.
2. Заполните test `shopId`/`secret key` и завершите решение по чекам.
3. Запустите миграции и сервисы по `docs/VPS_DEPLOY.md`.
4. В кабинете YooKassa зарегистрируйте test webhook.
5. Выполните preflight: `docker compose --env-file .env.production --profile ops run --rm preflight`.
6. Проведите тестовые сценарии официальными тестовыми данными: success, cancel, повтор webhook, неверная сумма/metadata и недоступность API.
7. Убедитесь в админке, что видны provider status, payment ID, сумма, `paid`, `refundable`, время последнего GET и webhook.

Тестовые данные не смешиваются с live credentials. Проект не выполняет реальный платёж или возврат при preflight/dry-run.

## Переход в live

До live нужны: одобренный магазин, принятое решение по чекам, новый live secret, публичный HTTPS webhook, успешный restore-тест БД, небольшой пополненный Fragment hot wallet, alerts и ручной тест минимального заказа.

Только после этого замените тестовые credentials на live, установите `YOOKASSA_TEST_MODE=false` и `REAL_STARS_PURCHASE_ENABLED=true`, повторите preflight, отключите maintenance и отдельно включите покупки. Возврат после `UNCERTAIN`, `SUBMITTING`, `QUEUED`, `PROCESSING` или `SUCCEEDED` результата Fragment заблокирован: сначала оператор обязан установить окончательный результат покупки.

## Что не сделано автоматически

- регистрация/верификация магазина и договор с YooKassa;
- выбор налоговых параметров и схема чеков — **REQUIRES OWNER DECISION**;
- DNS и открытие 80/443 на VPS;
- создание/ротация настоящих credentials;
- реальный платёж, возврат или покупка Stars.
