"""YooKassa customer payment state and idempotency

Revision ID: 0005_yookassa_payments
Revises: 0004_admin_panel
Create Date: 2026-08-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_yookassa_payments"
down_revision = "0004_admin_panel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError(
            "Migration 0005 is for production PostgreSQL. Development SQLite is upgraded at startup."
        )
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE customerpaymenttype ADD VALUE IF NOT EXISTS 'YOOKASSA'")
        op.execute("ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'PENDING'")
        op.execute("ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'WAITING_FOR_CAPTURE'")
        op.execute("ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'CANCELED'")

    op.add_column("payments", sa.Column("idempotency_key", sa.String(64)))
    op.add_column("payments", sa.Column("confirmation_url", sa.Text()))
    op.add_column("payments", sa.Column("provider_status", sa.String(32)))
    op.add_column(
        "payments", sa.Column("paid", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "payments",
        sa.Column("refundable", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("payments", sa.Column("last_provider_sync_at", sa.DateTime(timezone=True)))
    op.add_column("payments", sa.Column("last_webhook_event", sa.String(64)))
    op.add_column("payments", sa.Column("last_webhook_at", sa.DateTime(timezone=True)))
    op.add_column("payments", sa.Column("refund_id", sa.String(128)))
    op.add_column("payments", sa.Column("refund_status", sa.String(32)))
    op.add_column("payments", sa.Column("refund_idempotency_key", sa.String(64)))
    op.add_column("payments", sa.Column("refund_amount", sa.Numeric(18, 2)))
    op.create_unique_constraint("uq_payments_idempotency_key", "payments", ["idempotency_key"])
    op.create_unique_constraint("uq_payments_refund_id", "payments", ["refund_id"])
    op.create_index("ix_payments_provider_status", "payments", ["provider_status"])
    op.create_index("ix_orders_status_created_at", "orders", ["status", "created_at"])
    op.create_index(
        "ix_payments_provider_status_compound", "payments", ["provider", "provider_status"]
    )
    op.create_index("ix_audit_events_order_created", "audit_events", ["order_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_order_created", table_name="audit_events")
    op.drop_index("ix_payments_provider_status_compound", table_name="payments")
    op.drop_index("ix_orders_status_created_at", table_name="orders")
    op.drop_index("ix_payments_provider_status", table_name="payments")
    op.drop_constraint("uq_payments_refund_id", "payments", type_="unique")
    op.drop_constraint("uq_payments_idempotency_key", "payments", type_="unique")
    for column in (
        "refund_amount",
        "refund_idempotency_key",
        "refund_status",
        "refund_id",
        "last_webhook_at",
        "last_webhook_event",
        "last_provider_sync_at",
        "refundable",
        "paid",
        "provider_status",
        "confirmation_url",
        "idempotency_key",
    ):
        op.drop_column("payments", column)
    # PostgreSQL enum values are intentionally retained; removing them is unsafe.
