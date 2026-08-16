"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    order_kind = postgresql.ENUM("SELF", "GIFT", name="orderkind", create_type=False)
    order_status = postgresql.ENUM(
        "NEW",
        "PAYMENT_PENDING",
        "PAID",
        "DELIVERING",
        "COMPLETED",
        "FAILED",
        "CANCELED",
        name="orderstatus",
        create_type=False,
    )
    payment_status = postgresql.ENUM(
        "CREATED", "SUCCEEDED", "FAILED", name="paymentstatus", create_type=False
    )
    order_kind.create(op.get_bind(), checkfirst=True)
    order_status.create(op.get_bind(), checkfirst=True)
    payment_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("buyer_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("buyer_username", sa.String(length=64), nullable=True),
        sa.Column("kind", order_kind, nullable=False),
        sa.Column("status", order_status, nullable=False),
        sa.Column("recipient_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("recipient_username", sa.String(length=64), nullable=True),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("telegram_currency", sa.String(length=8), nullable=False),
        sa.Column("telegram_amount_minor", sa.Integer(), nullable=False),
        sa.Column("usd_rub_rate", sa.Numeric(12, 4), nullable=False),
        sa.Column("markup_percent", sa.Numeric(7, 3), nullable=False),
        sa.Column("rub_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("payment_id", sa.String(length=128), nullable=True),
        sa.Column("payment_status", payment_status, nullable=True),
        sa.Column("telegram_transaction_id", sa.String(length=128), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
    )
    op.create_index("ix_orders_buyer_telegram_id", "orders", ["buyer_telegram_id"])
    op.create_index("ix_orders_status", "orders", ["status"])


def downgrade() -> None:
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_buyer_telegram_id", table_name="orders")
    op.drop_table("orders")
    sa.Enum(name="paymentstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="orderstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="orderkind").drop(op.get_bind(), checkfirst=True)
