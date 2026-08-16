"""customer payment fields

Revision ID: 0002_customer_payment
Revises: 0001_initial
Create Date: 2026-08-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_customer_payment"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    customer_payment_type = sa.Enum("TEST", "TON", name="customerpaymenttype")
    customer_payment_type.create(op.get_bind(), checkfirst=True)

    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("ton_amount", sa.Numeric(14, 4), nullable=True))
        batch_op.add_column(
            sa.Column("customer_payment_type", customer_payment_type, nullable=True)
        )
        batch_op.add_column(sa.Column("payment_comment", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("ton_tx_hash", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("fragment_request_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("ALTER TYPE orderstatus ADD VALUE IF NOT EXISTS 'EXPIRED'")


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("expires_at")
        batch_op.drop_column("fragment_request_id")
        batch_op.drop_column("ton_tx_hash")
        batch_op.drop_column("payment_comment")
        batch_op.drop_column("customer_payment_type")
        batch_op.drop_column("ton_amount")

    sa.Enum(name="customerpaymenttype").drop(op.get_bind(), checkfirst=True)
