"""production-safe order, payment, purchase, and audit schema

Revision ID: 0003_production_safety
Revises: 0002_customer_payment
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_production_safety"
down_revision = "0002_customer_payment"
branch_labels = None
depends_on = None


ORDER_STATUSES = (
    "CREATED",
    "WAITING_FOR_PAYMENT",
    "PAYMENT_DETECTED",
    "PAYMENT_CONFIRMING",
    "PAID",
    "PURCHASE_PROCESSING",
    "STARS_SENDING",
    "COMPLETED",
    "PAYMENT_EXPIRED",
    "PAYMENT_FAILED",
    "PURCHASE_FAILED",
    "REFUND_REQUIRED",
    "REFUNDED",
    "CANCELLED",
    "MANUAL_REVIEW",
)
PAYMENT_STATUSES = (
    "CREATED",
    "DETECTED",
    "CONFIRMING",
    "SUCCEEDED",
    "FAILED",
    "EXPIRED",
    "MANUAL_REVIEW",
)
PURCHASE_STATUSES = (
    "CREATED",
    "SUBMITTING",
    "QUEUED",
    "PROCESSING",
    "SUCCEEDED",
    "FAILED",
    "UNCERTAIN",
)


def _add_enum_values(enum_name: str, values: tuple[str, ...]) -> None:
    with op.get_context().autocommit_block():
        for value in values:
            op.execute(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError(
            "Migration 0003 is for production PostgreSQL. Development SQLite is upgraded at startup."
        )

    _add_enum_values("orderstatus", ORDER_STATUSES)
    _add_enum_values("paymentstatus", PAYMENT_STATUSES)

    purchase_status = postgresql.ENUM(
        *PURCHASE_STATUSES,
        name="purchaseattemptstatus",
    )
    purchase_status.create(op.get_bind(), checkfirst=True)

    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("payment_destination", sa.String(length=128)))
        batch_op.add_column(sa.Column("payment_network", sa.String(length=16)))
        batch_op.add_column(sa.Column("quote_unit_price", sa.Numeric(24, 12)))
        batch_op.add_column(sa.Column("quote_currency", sa.String(length=16)))
        batch_op.add_column(sa.Column("quote_commission_percent", sa.Numeric(9, 4)))
        batch_op.add_column(sa.Column("quote_expires_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("error_code", sa.String(length=64)))
        batch_op.add_column(
            sa.Column(
                "status_changed_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            )
        )
        batch_op.alter_column(
            "ton_amount",
            existing_type=sa.Numeric(14, 4),
            type_=sa.Numeric(24, 9),
        )
        batch_op.alter_column(
            "telegram_currency",
            existing_type=sa.String(length=8),
            type_=sa.String(length=16),
        )
        batch_op.alter_column(
            "telegram_amount_minor",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
        )
        batch_op.alter_column(
            "usd_rub_rate",
            existing_type=sa.Numeric(12, 4),
            type_=sa.Numeric(18, 6),
        )
        batch_op.alter_column(
            "markup_percent",
            existing_type=sa.Numeric(7, 3),
            type_=sa.Numeric(9, 4),
        )
        batch_op.alter_column(
            "rub_amount",
            existing_type=sa.Numeric(12, 2),
            type_=sa.Numeric(18, 2),
        )
        batch_op.create_unique_constraint("uq_orders_payment_comment", ["payment_comment"])
        batch_op.create_unique_constraint("uq_orders_ton_tx_hash", ["ton_tx_hash"])
        batch_op.create_unique_constraint("uq_orders_fragment_request_id", ["fragment_request_id"])
        batch_op.create_unique_constraint(
            "uq_orders_telegram_transaction_id", ["telegram_transaction_id"]
        )
        batch_op.create_check_constraint("ck_orders_stars_positive", "stars > 0")
        batch_op.create_check_constraint("ck_orders_rub_amount_nonnegative", "rub_amount >= 0")

    op.execute(
        """
        UPDATE orders SET status = CASE status::text
            WHEN 'NEW' THEN 'CREATED'::orderstatus
            WHEN 'PAYMENT_PENDING' THEN 'WAITING_FOR_PAYMENT'::orderstatus
            WHEN 'DELIVERING' THEN 'MANUAL_REVIEW'::orderstatus
            WHEN 'FAILED' THEN 'MANUAL_REVIEW'::orderstatus
            WHEN 'CANCELED' THEN 'CANCELLED'::orderstatus
            WHEN 'EXPIRED' THEN 'PAYMENT_EXPIRED'::orderstatus
            ELSE status
        END
        """
    )
    op.execute(
        "UPDATE orders SET status_changed_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)"
    )

    payment_status = postgresql.ENUM(*PAYMENT_STATUSES, name="paymentstatus", create_type=False)
    purchase_status_ref = postgresql.ENUM(
        *PURCHASE_STATUSES,
        name="purchaseattemptstatus",
        create_type=False,
    )

    op.create_table(
        "users",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.String(length=64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute(
        """
        INSERT INTO users (telegram_id, username)
        SELECT DISTINCT ON (buyer_telegram_id) buyer_telegram_id, buyer_username
        FROM orders
        ORDER BY buyer_telegram_id, updated_at DESC
        ON CONFLICT (telegram_id) DO NOTHING
        """
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_reference", sa.String(length=128), nullable=False),
        sa.Column("transaction_hash", sa.String(length=128)),
        sa.Column("destination", sa.String(length=128)),
        sa.Column("expected_amount", sa.Numeric(24, 9), nullable=False),
        sa.Column("received_amount", sa.Numeric(24, 9)),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("network", sa.String(length=16), nullable=False),
        sa.Column("status", payment_status, nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("order_id", name="uq_payments_order_id"),
        sa.UniqueConstraint("provider_reference", name="uq_payments_provider_reference"),
        sa.UniqueConstraint("transaction_hash", name="uq_payments_transaction_hash"),
        sa.CheckConstraint("expected_amount >= 0", name="ck_payments_expected_nonnegative"),
    )
    op.create_index("ix_payments_order_id", "payments", ["order_id"])
    op.create_index("ix_payments_status", "payments", ["status"])
    op.execute(
        """
        INSERT INTO payments (
            order_id, provider, provider_reference, transaction_hash, destination,
            expected_amount, received_amount, currency, network, status,
            detected_at, confirmed_at
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
            COALESCE(payment_status, 'CREATED'::paymentstatus),
            CASE WHEN ton_tx_hash IS NOT NULL THEN updated_at ELSE NULL END,
            CASE WHEN payment_status = 'SUCCEEDED' THEN updated_at ELSE NULL END
        FROM orders
        WHERE payment_id IS NOT NULL
        """
    )

    op.create_table(
        "purchase_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", purchase_status_ref, nullable=False),
        sa.Column("provider_request_id", sa.String(length=128)),
        sa.Column("transaction_id", sa.String(length=128)),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("order_id", name="uq_purchase_attempts_order_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_purchase_attempts_idempotency_key"),
        sa.UniqueConstraint("provider_request_id", name="uq_purchase_attempts_provider_request_id"),
        sa.UniqueConstraint("transaction_id", name="uq_purchase_attempts_transaction_id"),
    )
    op.create_index("ix_purchase_attempts_order_id", "purchase_attempts", ["order_id"])
    op.create_index("ix_purchase_attempts_status", "purchase_attempts", ["status"])
    op.execute(
        """
        INSERT INTO purchase_attempts (
            order_id, idempotency_key, provider, status, provider_request_id,
            transaction_id, error_code, started_at, completed_at
        )
        SELECT
            id,
            'purchase:' || id,
            'legacy',
            CASE
                WHEN status = 'COMPLETED' THEN 'SUCCEEDED'::purchaseattemptstatus
                WHEN fragment_request_id IS NOT NULL THEN 'QUEUED'::purchaseattemptstatus
                ELSE 'UNCERTAIN'::purchaseattemptstatus
            END,
            fragment_request_id,
            telegram_transaction_id,
            CASE WHEN status = 'MANUAL_REVIEW' THEN 'LEGACY_DELIVERY_UNCERTAIN' ELSE NULL END,
            updated_at,
            CASE WHEN status = 'COMPLETED' THEN updated_at ELSE NULL END
        FROM orders
        WHERE status IN (
            'PURCHASE_PROCESSING'::orderstatus,
            'STARS_SENDING'::orderstatus,
            'COMPLETED'::orderstatus,
            'MANUAL_REVIEW'::orderstatus
        )
          AND (fragment_request_id IS NOT NULL OR telegram_transaction_id IS NOT NULL)
        """
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="RESTRICT")),
        sa.Column("actor_telegram_id", sa.BigInteger()),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_audit_events_order_id", "audit_events", ["order_id"])
    op.create_index("ix_audit_events_event", "audit_events", ["event"])

    op.create_table(
        "runtime_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.String(length=256), nullable=False),
        sa.Column("updated_by", sa.BigInteger()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "order_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("audience", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_token", sa.String(length=36)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("abandoned_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(length=64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "audience IN ('user', 'admin')",
            name="ck_order_notifications_audience",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_order_notifications_attempts_nonnegative",
        ),
    )
    op.create_index("ix_order_notifications_order_id", "order_notifications", ["order_id"])
    op.create_index("ix_order_notifications_claim_token", "order_notifications", ["claim_token"])
    op.create_index(
        "ix_order_notifications_pending",
        "order_notifications",
        ["next_attempt_at", "delivered_at", "abandoned_at"],
    )
    op.execute(
        """
        INSERT INTO order_notifications (order_id, status, audience, attempts, next_attempt_at)
        SELECT id, LOWER(status::text), 'user', 0, CURRENT_TIMESTAMP FROM orders
        """
    )
    op.execute(
        """
        INSERT INTO order_notifications (order_id, status, audience, attempts, next_attempt_at)
        SELECT id, LOWER(status::text), 'admin', 0, CURRENT_TIMESTAMP
        FROM orders
        WHERE status IN ('MANUAL_REVIEW'::orderstatus, 'REFUND_REQUIRED'::orderstatus)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_order_notifications_pending", table_name="order_notifications")
    op.drop_index("ix_order_notifications_claim_token", table_name="order_notifications")
    op.drop_index("ix_order_notifications_order_id", table_name="order_notifications")
    op.drop_table("order_notifications")
    op.drop_table("runtime_settings")
    op.drop_index("ix_audit_events_event", table_name="audit_events")
    op.drop_index("ix_audit_events_order_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_purchase_attempts_status", table_name="purchase_attempts")
    op.drop_index("ix_purchase_attempts_order_id", table_name="purchase_attempts")
    op.drop_table("purchase_attempts")
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_order_id", table_name="payments")
    op.drop_table("payments")
    op.drop_table("users")
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint("ck_orders_rub_amount_nonnegative", type_="check")
        batch_op.drop_constraint("ck_orders_stars_positive", type_="check")
        batch_op.drop_constraint("uq_orders_telegram_transaction_id", type_="unique")
        batch_op.drop_constraint("uq_orders_fragment_request_id", type_="unique")
        batch_op.drop_constraint("uq_orders_ton_tx_hash", type_="unique")
        batch_op.drop_constraint("uq_orders_payment_comment", type_="unique")
        for column in (
            "status_changed_at",
            "error_code",
            "quote_expires_at",
            "quote_commission_percent",
            "quote_currency",
            "quote_unit_price",
            "payment_network",
            "payment_destination",
        ):
            batch_op.drop_column(column)
    postgresql.ENUM(name="purchaseattemptstatus").drop(op.get_bind(), checkfirst=True)
