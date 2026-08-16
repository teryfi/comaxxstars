"""web admin, public order numbers, and merchant balance state

Revision ID: 0004_admin_panel
Revises: 0003_production_safety
Create Date: 2026-08-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_admin_panel"
down_revision = "0003_production_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError(
            "Migration 0004 is for production PostgreSQL. Development SQLite is upgraded at startup."
        )
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE orderstatus ADD VALUE IF NOT EXISTS 'WAITING_FOR_MERCHANT_BALANCE'")

    admin_role = postgresql.ENUM("OWNER", "ADMIN", "SUPPORT", name="adminrole", create_type=False)
    admin_role.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", admin_role, nullable=False),
        sa.Column("totp_secret", sa.String(64)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("username", name="uq_admin_users_username"),
    )
    op.create_index("ix_admin_users_username", "admin_users", ["username"])
    op.create_table(
        "admin_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "admin_user_id",
            sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("csrf_token_hash", sa.String(64), nullable=False),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("user_agent", sa.String(256)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_sessions_admin_user_id", "admin_sessions", ["admin_user_id"])
    op.create_index("ix_admin_sessions_expires_at", "admin_sessions", ["expires_at"])
    op.create_table(
        "blocked_telegram_users",
        sa.Column("telegram_user_id", sa.BigInteger(), primary_key=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "created_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "blocked_ips",
        sa.Column("cidr", sa.String(64), primary_key=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "created_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("admin_note", sa.Text()))
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("order_number", sa.String(32)))
        batch_op.add_column(sa.Column("customer_chat_id", sa.BigInteger()))
        batch_op.add_column(sa.Column("customer_message_id", sa.Integer()))
    op.execute(
        "UPDATE orders SET order_number = 'TS-' || to_char(created_at, 'YYYYMMDD') || '-' || "
        "upper(substr(md5(id::text || idempotency_key), 1, 8)) WHERE order_number IS NULL"
    )
    with op.batch_alter_table("orders") as batch_op:
        batch_op.alter_column("order_number", nullable=False)
        batch_op.create_unique_constraint("uq_orders_order_number", ["order_number"])
    op.create_index("ix_orders_order_number", "orders", ["order_number"])

    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.add_column(
            sa.Column("actor_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"))
        )
        batch_op.add_column(sa.Column("entity_type", sa.String(32)))
        batch_op.add_column(sa.Column("entity_id", sa.String(128)))
        batch_op.add_column(sa.Column("ip_address", sa.String(64)))
        batch_op.add_column(sa.Column("correlation_id", sa.String(36)))
        batch_op.add_column(sa.Column("previous_state", sa.JSON()))
        batch_op.add_column(sa.Column("new_state", sa.JSON()))
    op.create_index("ix_audit_events_correlation_id", "audit_events", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_correlation_id", table_name="audit_events")
    with op.batch_alter_table("audit_events") as batch_op:
        for name in (
            "new_state",
            "previous_state",
            "correlation_id",
            "ip_address",
            "entity_id",
            "entity_type",
            "actor_admin_id",
        ):
            batch_op.drop_column(name)
    op.drop_index("ix_orders_order_number", table_name="orders")
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint("uq_orders_order_number", type_="unique")
        batch_op.drop_column("customer_message_id")
        batch_op.drop_column("customer_chat_id")
        batch_op.drop_column("order_number")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("admin_note")
    op.drop_table("blocked_ips")
    op.drop_table("blocked_telegram_users")
    op.drop_index("ix_admin_sessions_expires_at", table_name="admin_sessions")
    op.drop_index("ix_admin_sessions_admin_user_id", table_name="admin_sessions")
    op.drop_table("admin_sessions")
    op.drop_index("ix_admin_users_username", table_name="admin_users")
    op.drop_table("admin_users")
    postgresql.ENUM(name="adminrole").drop(op.get_bind(), checkfirst=True)
