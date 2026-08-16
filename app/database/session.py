import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.models import Base


def create_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    engine_options = {"poolclass": NullPool} if database_url.startswith("sqlite+") else {}
    engine = create_async_engine(database_url, pool_pre_ping=True, **engine_options)
    return async_sessionmaker(engine, expire_on_commit=False)


async def dispose_session_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = session_factory.kw.get("bind")
    if engine is not None:
        await engine.dispose()


async def create_local_schema(database_url: str) -> None:
    if not database_url.startswith("sqlite+aiosqlite"):
        return

    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        existing_columns = await connection.exec_driver_sql("PRAGMA table_info(orders)")
        column_names = {row[1] for row in existing_columns.fetchall()}
        sqlite_columns = {
            "order_number": "VARCHAR(32)",
            "ton_amount": "NUMERIC(24, 9)",
            "customer_payment_type": "VARCHAR(16)",
            "payment_comment": "VARCHAR(64)",
            "payment_destination": "VARCHAR(128)",
            "payment_network": "VARCHAR(16)",
            "ton_tx_hash": "VARCHAR(128)",
            "fragment_request_id": "VARCHAR(128)",
            "expires_at": "DATETIME",
            "quote_unit_price": "NUMERIC(24, 12)",
            "quote_currency": "VARCHAR(16)",
            "quote_commission_percent": "NUMERIC(9, 4)",
            "quote_expires_at": "DATETIME",
            "error_code": "VARCHAR(64)",
            "status_changed_at": "DATETIME",
            "customer_chat_id": "BIGINT",
            "customer_message_id": "INTEGER",
        }
        for name, definition in sqlite_columns.items():
            if name not in column_names:
                await connection.exec_driver_sql(
                    f"ALTER TABLE orders ADD COLUMN {name} {definition}"
                )

        missing_numbers = await connection.exec_driver_sql(
            "SELECT id, created_at FROM orders WHERE order_number IS NULL OR order_number = ''"
        )
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        for order_id, created_at in missing_numbers.fetchall():
            try:
                date_value = datetime.fromisoformat(str(created_at)).strftime("%Y%m%d")
            except ValueError:
                date_value = datetime.now(UTC).strftime("%Y%m%d")
            suffix = "".join(secrets.choice(alphabet) for _ in range(8))
            await connection.exec_driver_sql(
                "UPDATE orders SET order_number = ? WHERE id = ?",
                (f"TS-{date_value}-{suffix}", order_id),
            )
        await connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_order_number_idx ON orders (order_number)"
        )

        user_columns = await connection.exec_driver_sql("PRAGMA table_info(users)")
        if "admin_note" not in {row[1] for row in user_columns.fetchall()}:
            await connection.exec_driver_sql("ALTER TABLE users ADD COLUMN admin_note TEXT")

        audit_columns = await connection.exec_driver_sql("PRAGMA table_info(audit_events)")
        audit_column_names = {row[1] for row in audit_columns.fetchall()}
        audit_additions = {
            "actor_admin_id": "INTEGER",
            "entity_type": "VARCHAR(32)",
            "entity_id": "VARCHAR(128)",
            "ip_address": "VARCHAR(64)",
            "correlation_id": "VARCHAR(36)",
            "previous_state": "JSON",
            "new_state": "JSON",
        }
        for name, definition in audit_additions.items():
            if name not in audit_column_names:
                await connection.exec_driver_sql(
                    f"ALTER TABLE audit_events ADD COLUMN {name} {definition}"
                )
        await connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_audit_events_correlation_id "
            "ON audit_events (correlation_id)"
        )

        status_mapping = {
            "NEW": "CREATED",
            "PAYMENT_PENDING": "WAITING_FOR_PAYMENT",
            "DELIVERING": "MANUAL_REVIEW",
            "FAILED": "MANUAL_REVIEW",
            "CANCELED": "CANCELLED",
            "EXPIRED": "PAYMENT_EXPIRED",
        }
        for old, new in status_mapping.items():
            await connection.exec_driver_sql(
                "UPDATE orders SET status = ? WHERE status = ?",
                (new, old),
            )
        await connection.exec_driver_sql(
            "UPDATE orders SET status_changed_at = COALESCE(status_changed_at, updated_at, created_at, CURRENT_TIMESTAMP)"
        )

        unique_indexes = {
            "uq_orders_payment_comment_idx": "payment_comment",
            "uq_orders_ton_tx_hash_idx": "ton_tx_hash",
            "uq_orders_fragment_request_id_idx": "fragment_request_id",
            "uq_orders_telegram_transaction_id_idx": "telegram_transaction_id",
        }
        for name, column in unique_indexes.items():
            await connection.exec_driver_sql(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON orders ({column}) WHERE {column} IS NOT NULL"
            )
        await connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_order_id_idx ON payments (order_id)"
        )
        payment_columns = await connection.exec_driver_sql("PRAGMA table_info(payments)")
        payment_column_names = {row[1] for row in payment_columns.fetchall()}
        payment_additions = {
            "idempotency_key": "VARCHAR(64)",
            "confirmation_url": "TEXT",
            "provider_status": "VARCHAR(32)",
            "paid": "BOOLEAN NOT NULL DEFAULT 0",
            "refundable": "BOOLEAN NOT NULL DEFAULT 0",
            "last_provider_sync_at": "DATETIME",
            "last_webhook_event": "VARCHAR(64)",
            "last_webhook_at": "DATETIME",
            "refund_id": "VARCHAR(128)",
            "refund_status": "VARCHAR(32)",
            "refund_idempotency_key": "VARCHAR(64)",
            "refund_amount": "NUMERIC(18, 2)",
        }
        for name, definition in payment_additions.items():
            if name not in payment_column_names:
                await connection.exec_driver_sql(
                    f"ALTER TABLE payments ADD COLUMN {name} {definition}"
                )
        await connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_idempotency_key_idx "
            "ON payments (idempotency_key) WHERE idempotency_key IS NOT NULL"
        )
        await connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_refund_id_idx "
            "ON payments (refund_id) WHERE refund_id IS NOT NULL"
        )
        await connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_payments_provider_status ON payments (provider_status)"
        )
        await connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_orders_status_created_at ON orders (status, created_at)"
        )
        await connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_payments_provider_status_compound "
            "ON payments (provider, provider_status)"
        )
        await connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_audit_events_order_created "
            "ON audit_events (order_id, created_at)"
        )
        await connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_purchase_attempts_order_id_idx "
            "ON purchase_attempts (order_id)"
        )
        await connection.exec_driver_sql(
            "INSERT OR IGNORE INTO users (telegram_id, username, created_at, updated_at) "
            "SELECT buyer_telegram_id, MAX(buyer_username), MIN(created_at), MAX(updated_at) "
            "FROM orders GROUP BY buyer_telegram_id"
        )
        await connection.exec_driver_sql(
            """
            INSERT OR IGNORE INTO payments (
                order_id, provider, provider_reference, transaction_hash, destination,
                expected_amount, received_amount, currency, network, status,
                detected_at, confirmed_at, created_at, updated_at
            )
            SELECT
                id,
                CASE WHEN customer_payment_type = 'TON' THEN 'toncenter' ELSE 'test' END,
                payment_id,
                ton_tx_hash,
                payment_destination,
                CASE WHEN customer_payment_type = 'TON' THEN COALESCE(ton_amount, 0) ELSE rub_amount END,
                CASE WHEN ton_tx_hash IS NOT NULL THEN ton_amount ELSE NULL END,
                CASE WHEN customer_payment_type = 'TON' THEN 'TON' ELSE 'RUB' END,
                CASE WHEN customer_payment_type = 'TON' THEN 'TON' ELSE 'test' END,
                COALESCE(payment_status, 'CREATED'),
                CASE WHEN ton_tx_hash IS NOT NULL THEN updated_at ELSE NULL END,
                CASE WHEN payment_status = 'SUCCEEDED' THEN updated_at ELSE NULL END,
                created_at,
                updated_at
            FROM orders
            WHERE payment_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM payments WHERE payments.order_id = orders.id)
            """
        )
        await connection.exec_driver_sql(
            """
            INSERT INTO order_notifications (order_id, status, audience, attempts, next_attempt_at)
            SELECT id, LOWER(status), 'user', 0, CURRENT_TIMESTAMP
            FROM orders
            WHERE NOT EXISTS (
                SELECT 1 FROM order_notifications
                WHERE order_notifications.order_id = orders.id
            )
            """
        )
        await connection.exec_driver_sql(
            """
            INSERT INTO order_notifications (order_id, status, audience, attempts, next_attempt_at)
            SELECT id, LOWER(status), 'admin', 0, CURRENT_TIMESTAMP
            FROM orders
            WHERE status IN ('MANUAL_REVIEW', 'REFUND_REQUIRED')
              AND NOT EXISTS (
                SELECT 1 FROM order_notifications
                WHERE order_notifications.order_id = orders.id
                  AND order_notifications.audience = 'admin'
              )
            """
        )
        await connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_order_notifications_pending "
            "ON order_notifications (next_attempt_at, delivered_at, abandoned_at)"
        )
        await connection.exec_driver_sql(
            """
            INSERT OR IGNORE INTO purchase_attempts (
                order_id, idempotency_key, provider, status, provider_request_id,
                transaction_id, error_code, started_at, completed_at, created_at, updated_at
            )
            SELECT
                id,
                'purchase:' || id,
                'legacy',
                CASE
                    WHEN status = 'COMPLETED' THEN 'SUCCEEDED'
                    WHEN fragment_request_id IS NOT NULL THEN 'QUEUED'
                    ELSE 'UNCERTAIN'
                END,
                fragment_request_id,
                telegram_transaction_id,
                CASE WHEN status = 'MANUAL_REVIEW' THEN 'LEGACY_DELIVERY_UNCERTAIN' ELSE NULL END,
                updated_at,
                CASE WHEN status = 'COMPLETED' THEN updated_at ELSE NULL END,
                created_at,
                updated_at
            FROM orders
            WHERE (fragment_request_id IS NOT NULL OR telegram_transaction_id IS NOT NULL)
              AND NOT EXISTS (
                  SELECT 1 FROM purchase_attempts WHERE purchase_attempts.order_id = orders.id
              )
            """
        )
    await engine.dispose()


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        async with session.begin():
            yield session
