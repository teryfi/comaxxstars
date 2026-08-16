from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain import (
    AdminRole,
    CustomerPaymentType,
    OrderKind,
    OrderStatus,
    PaymentStatus,
    PurchaseAttemptStatus,
)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    admin_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
        UniqueConstraint("order_number", name="uq_orders_order_number"),
        UniqueConstraint("payment_comment", name="uq_orders_payment_comment"),
        UniqueConstraint("ton_tx_hash", name="uq_orders_ton_tx_hash"),
        UniqueConstraint("fragment_request_id", name="uq_orders_fragment_request_id"),
        UniqueConstraint("telegram_transaction_id", name="uq_orders_telegram_transaction_id"),
        CheckConstraint("stars > 0", name="ck_orders_stars_positive"),
        CheckConstraint("rub_amount >= 0", name="ck_orders_rub_amount_nonnegative"),
        Index("ix_orders_status_created_at", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    buyer_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    buyer_username: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[OrderKind] = mapped_column(Enum(OrderKind), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), nullable=False, default=OrderStatus.CREATED, index=True
    )

    recipient_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    recipient_username: Mapped[str | None] = mapped_column(String(64))
    stars: Mapped[int] = mapped_column(Integer, nullable=False)
    telegram_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    telegram_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    usd_rub_rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    markup_percent: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    rub_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    quote_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    quote_currency: Mapped[str | None] = mapped_column(String(16))
    quote_commission_percent: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    quote_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ton_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    customer_payment_type: Mapped[CustomerPaymentType | None] = mapped_column(
        Enum(CustomerPaymentType)
    )
    payment_comment: Mapped[str | None] = mapped_column(String(64))
    payment_destination: Mapped[str | None] = mapped_column(String(128))
    payment_network: Mapped[str | None] = mapped_column(String(16))
    ton_tx_hash: Mapped[str | None] = mapped_column(String(128))
    fragment_request_id: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    payment_id: Mapped[str | None] = mapped_column(String(128))
    payment_status: Mapped[PaymentStatus | None] = mapped_column(Enum(PaymentStatus))
    telegram_transaction_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    customer_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    customer_message_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_payments_order_id"),
        UniqueConstraint("provider_reference", name="uq_payments_provider_reference"),
        UniqueConstraint("transaction_hash", name="uq_payments_transaction_hash"),
        UniqueConstraint("idempotency_key", name="uq_payments_idempotency_key"),
        UniqueConstraint("refund_id", name="uq_payments_refund_id"),
        CheckConstraint("expected_amount >= 0", name="ck_payments_expected_nonnegative"),
        Index("ix_payments_provider_status_compound", "provider", "provider_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    transaction_hash: Mapped[str | None] = mapped_column(String(128))
    destination: Mapped[str | None] = mapped_column(String(128))
    expected_amount: Mapped[Decimal] = mapped_column(Numeric(24, 9), nullable=False)
    received_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    network: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmation_url: Mapped[str | None] = mapped_column(Text)
    provider_status: Mapped[str | None] = mapped_column(String(32), index=True)
    paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    refundable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_provider_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_webhook_event: Mapped[str | None] = mapped_column(String(64))
    last_webhook_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refund_id: Mapped[str | None] = mapped_column(String(128))
    refund_status: Mapped[str | None] = mapped_column(String(32))
    refund_idempotency_key: Mapped[str | None] = mapped_column(String(64))
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), nullable=False, index=True)
    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PurchaseAttempt(Base):
    __tablename__ = "purchase_attempts"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_purchase_attempts_order_id"),
        UniqueConstraint("idempotency_key", name="uq_purchase_attempts_idempotency_key"),
        UniqueConstraint("provider_request_id", name="uq_purchase_attempts_provider_request_id"),
        UniqueConstraint("transaction_id", name="uq_purchase_attempts_transaction_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[PurchaseAttemptStatus] = mapped_column(
        Enum(PurchaseAttemptStatus), nullable=False, index=True
    )
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
    transaction_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_order_created", "order_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    actor_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    actor_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(128))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    previous_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    new_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrderNotification(Base):
    __tablename__ = "order_notifications"
    __table_args__ = (
        CheckConstraint("audience IN ('user', 'admin')", name="ck_order_notifications_audience"),
        CheckConstraint("attempts >= 0", name="ck_order_notifications_attempts_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    audience: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_token: Mapped[str | None] = mapped_column(String(36), index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[AdminRole] = mapped_column(Enum(AdminRole), nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_user_id: Mapped[int] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class BlockedTelegramUser(Base):
    __tablename__ = "blocked_telegram_users"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by_admin_id: Mapped[int] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BlockedIp(Base):
    __tablename__ = "blocked_ips"

    cidr: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by_admin_id: Mapped[int] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
