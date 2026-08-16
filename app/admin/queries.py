import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, String, case, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import (
    AdminUser,
    AuditEvent,
    BlockedIp,
    BlockedTelegramUser,
    Order,
    Payment,
    PurchaseAttempt,
    RuntimeSetting,
    User,
)
from app.domain import OrderStatus, PaymentStatus


class AdminQueries:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def dashboard(self) -> dict[str, Any]:
        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        async with self.session_factory() as session:
            rows = await session.execute(
                select(Order.status, func.count(Order.id)).group_by(Order.status)
            )
            statuses = {status.value: int(count) for status, count in rows}
            today_orders = int(
                await session.scalar(select(func.count(Order.id)).where(Order.created_at >= since))
                or 0
            )
            today = await session.execute(
                select(
                    func.coalesce(func.sum(Order.rub_amount), 0),
                    func.coalesce(func.sum(Order.stars), 0),
                    func.coalesce(func.avg(Order.rub_amount), 0),
                ).where(
                    Order.created_at >= since,
                    Order.payment_status == PaymentStatus.SUCCEEDED,
                )
            )
            revenue, stars, average = today.one()
            totals = await session.execute(
                select(
                    func.coalesce(func.sum(Order.rub_amount), 0),
                    func.coalesce(
                        func.sum(
                            case((Order.status == OrderStatus.COMPLETED, Order.stars), else_=0)
                        ),
                        0,
                    ),
                ).where(Order.payment_status == PaymentStatus.SUCCEEDED)
            )
            total_revenue, total_stars = totals.one()
            trend_start = since - timedelta(days=13)
            trend_rows = await session.execute(
                select(Order.created_at, Order.rub_amount, Order.stars).where(
                    Order.created_at >= trend_start,
                    Order.payment_status == PaymentStatus.SUCCEEDED,
                )
            )
            trend_by_day: dict[Any, dict[str, Any]] = {}
            for created_at, rub_amount, stars_amount in trend_rows:
                day = created_at.date() if hasattr(created_at, "date") else created_at
                bucket = trend_by_day.setdefault(day, {"revenue": Decimal("0"), "orders": 0, "stars": 0})
                bucket["revenue"] += Decimal(rub_amount or 0)
                bucket["orders"] += 1
                bucket["stars"] += int(stars_amount or 0)
            trend = [
                {
                    "label": (trend_start + timedelta(days=index)).strftime("%d.%m"),
                    "revenue": trend_by_day.get(
                        trend_start.date() + timedelta(days=index),
                        {"revenue": Decimal("0"), "orders": 0, "stars": 0},
                    )["revenue"],
                    "orders": trend_by_day.get(
                        trend_start.date() + timedelta(days=index),
                        {"revenue": Decimal("0"), "orders": 0, "stars": 0},
                    )["orders"],
                    "stars": trend_by_day.get(
                        trend_start.date() + timedelta(days=index),
                        {"revenue": Decimal("0"), "orders": 0, "stars": 0},
                    )["stars"],
                }
                for index in range(14)
            ]
            recent = list(
                await session.scalars(select(Order).order_by(Order.created_at.desc()).limit(8))
            )
            payment_errors = int(
                await session.scalar(
                    select(func.count(Order.id)).where(
                        Order.error_code.is_not(None),
                        Order.updated_at >= datetime.now(UTC) - timedelta(hours=24),
                    )
                )
                or 0
            )
            return {
                "statuses": statuses,
                "today_orders": today_orders,
                "today_revenue": Decimal(revenue or 0),
                "today_stars": int(stars or 0),
                "average_check": Decimal(average or 0),
                "total_revenue": Decimal(total_revenue or 0),
                "total_stars": int(total_stars or 0),
                "trend": trend,
                "provider_errors": payment_errors,
                "recent": recent,
            }

    async def orders(
        self,
        *,
        query: str = "",
        status: str = "",
        paid: str = "",
        min_stars: str = "",
        max_stars: str = "",
        min_amount: str = "",
        max_amount: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        statement = (
            select(Order, Payment, PurchaseAttempt)
            .outerjoin(Payment, Payment.order_id == Order.id)
            .outerjoin(PurchaseAttempt, PurchaseAttempt.order_id == Order.id)
            .order_by(Order.created_at.desc())
            .limit(min(max(limit, 1), 200))
            .offset(max(offset, 0))
        )
        clean_query = query.strip()
        if clean_query:
            pattern = f"%{clean_query}%"
            statement = statement.where(
                or_(
                    Order.order_number.ilike(pattern),
                    cast(Order.buyer_telegram_id, String).ilike(pattern),
                    Order.buyer_username.ilike(pattern),
                    Order.recipient_username.ilike(pattern),
                    Payment.provider_reference.ilike(pattern),
                    Payment.transaction_hash.ilike(pattern),
                    PurchaseAttempt.provider_request_id.ilike(pattern),
                    PurchaseAttempt.transaction_id.ilike(pattern),
                )
            )
        if status:
            try:
                statement = statement.where(Order.status == OrderStatus(status))
            except ValueError:
                pass
        if paid == "yes":
            statement = statement.where(Order.payment_status == PaymentStatus.SUCCEEDED)
        elif paid == "no":
            statement = statement.where(
                or_(Order.payment_status.is_(None), Order.payment_status != PaymentStatus.SUCCEEDED)
            )
        numeric_filters = (
            (min_stars, Order.stars, ">="),
            (max_stars, Order.stars, "<="),
            (min_amount, Order.rub_amount, ">="),
            (max_amount, Order.rub_amount, "<="),
        )
        for raw, column, operator in numeric_filters:
            try:
                value = Decimal(raw) if raw.strip() else None
            except ArithmeticError:
                value = None
            if value is not None and (not value.is_finite() or value < 0):
                value = None
            if value is not None:
                statement = statement.where(
                    column >= value if operator == ">=" else column <= value
                )
        for raw, operator in ((date_from, ">="), (date_to, "<")):
            try:
                parsed = datetime.fromisoformat(raw).replace(tzinfo=UTC) if raw else None
            except ValueError:
                parsed = None
            if parsed is not None:
                if operator == "<":
                    statement = statement.where(Order.created_at < parsed + timedelta(days=1))
                else:
                    statement = statement.where(Order.created_at >= parsed)
        async with self.session_factory() as session:
            rows = (await session.execute(statement)).all()
            return [
                {"order": order, "payment": payment, "attempt": attempt}
                for order, payment, attempt in rows
            ]

    async def order_detail(self, order_number: str) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(Order, Payment, PurchaseAttempt)
                    .outerjoin(Payment, Payment.order_id == Order.id)
                    .outerjoin(PurchaseAttempt, PurchaseAttempt.order_id == Order.id)
                    .where(Order.order_number == order_number.upper())
                )
            ).first()
            if row is None:
                return None
            order, payment, attempt = row
            events = list(
                await session.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.order_id == order.id)
                    .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
                )
            )
            return {"order": order, "payment": payment, "attempt": attempt, "events": events}

    async def users(self, *, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        successful = case((Order.status == OrderStatus.COMPLETED, 1), else_=0)
        failed = case(
            (
                Order.status.in_(
                    {
                        OrderStatus.PAYMENT_FAILED,
                        OrderStatus.PURCHASE_FAILED,
                        OrderStatus.REFUND_REQUIRED,
                        OrderStatus.MANUAL_REVIEW,
                    }
                ),
                1,
            ),
            else_=0,
        )
        statement: Select[Any] = (
            select(
                User,
                func.count(Order.id),
                func.coalesce(func.sum(Order.rub_amount), 0),
                func.coalesce(func.sum(Order.stars), 0),
                func.coalesce(func.sum(successful), 0),
                func.coalesce(func.sum(failed), 0),
                BlockedTelegramUser.telegram_user_id,
            )
            .outerjoin(Order, Order.buyer_telegram_id == User.telegram_id)
            .outerjoin(
                BlockedTelegramUser,
                BlockedTelegramUser.telegram_user_id == User.telegram_id,
            )
            .group_by(User.telegram_id, BlockedTelegramUser.telegram_user_id)
            .order_by(User.updated_at.desc())
            .limit(min(max(limit, 1), 200))
        )
        clean = query.strip()
        if clean:
            statement = statement.where(
                or_(
                    cast(User.telegram_id, String).ilike(f"%{clean}%"),
                    User.username.ilike(f"%{clean}%"),
                )
            )
        async with self.session_factory() as session:
            rows = (await session.execute(statement)).all()
            return [
                {
                    "user": user,
                    "orders": int(order_count),
                    "spent": Decimal(spent),
                    "stars": int(stars),
                    "successful": int(ok),
                    "failed": int(fail),
                    "blocked": blocked_id is not None,
                }
                for user, order_count, spent, stars, ok, fail, blocked_id in rows
            ]

    async def user_detail(self, telegram_id: int) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            user = await session.get(User, telegram_id)
            if user is None:
                return None
            orders = list(
                await session.scalars(
                    select(Order)
                    .where(Order.buyer_telegram_id == telegram_id)
                    .order_by(Order.created_at.desc())
                )
            )
            block = await session.get(BlockedTelegramUser, telegram_id)
            return {"user": user, "orders": orders, "block": block}

    async def update_user(
        self,
        *,
        telegram_id: int,
        action: str,
        reason: str,
        note: str,
        admin_id: int,
        ip_address: str,
    ) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                user = await session.get(User, telegram_id)
                if user is None:
                    raise ValueError("Пользователь не найден")
                event = "user_note_updated"
                if action == "block":
                    if not reason.strip():
                        raise ValueError("Укажите причину блокировки")
                    block = await session.get(BlockedTelegramUser, telegram_id)
                    if block is None:
                        session.add(
                            BlockedTelegramUser(
                                telegram_user_id=telegram_id,
                                reason=reason.strip()[:500],
                                created_by_admin_id=admin_id,
                            )
                        )
                    else:
                        block.reason = reason.strip()[:500]
                    event = "user_block"
                elif action == "unblock":
                    await session.execute(
                        delete(BlockedTelegramUser).where(
                            BlockedTelegramUser.telegram_user_id == telegram_id
                        )
                    )
                    event = "user_unblock"
                elif action == "note":
                    user.admin_note = note.strip()[:2000] or None
                else:
                    raise ValueError("Неизвестное действие с пользователем")
                session.add(
                    AuditEvent(
                        actor_admin_id=admin_id,
                        event=event,
                        entity_type="telegram_user",
                        entity_id=str(telegram_id),
                        ip_address=ip_address,
                        correlation_id=str(uuid.uuid4()),
                        details={"reason": reason[:500]} if reason else {},
                    )
                )

    async def audit_events(self, limit: int = 200) -> list[tuple[AuditEvent, str | None]]:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(AuditEvent, AdminUser.username)
                .outerjoin(AdminUser, AdminUser.id == AuditEvent.actor_admin_id)
                .order_by(AuditEvent.created_at.desc())
                .limit(min(max(limit, 1), 500))
            )
            return [(event, username) for event, username in rows.all()]

    async def blocklists(self) -> tuple[list[BlockedTelegramUser], list[BlockedIp]]:
        async with self.session_factory() as session:
            users = list(
                await session.scalars(
                    select(BlockedTelegramUser).order_by(BlockedTelegramUser.created_at.desc())
                )
            )
            ips = list(
                await session.scalars(select(BlockedIp).order_by(BlockedIp.created_at.desc()))
            )
            return users, ips

    async def update_ip_block(
        self,
        *,
        cidr: str,
        reason: str,
        action: str,
        admin_id: int,
        ip_address: str,
    ) -> None:
        normalized = cidr.strip()
        async with self.session_factory() as session:
            async with session.begin():
                if action == "add":
                    if not normalized or not reason.strip():
                        raise ValueError("Укажите сеть и причину блокировки")
                    block = await session.get(BlockedIp, normalized)
                    if block is None:
                        session.add(
                            BlockedIp(
                                cidr=normalized,
                                reason=reason.strip()[:500],
                                created_by_admin_id=admin_id,
                            )
                        )
                    else:
                        block.reason = reason.strip()[:500]
                    event = "ip_block"
                elif action == "remove":
                    await session.execute(delete(BlockedIp).where(BlockedIp.cidr == normalized))
                    event = "ip_unblock"
                else:
                    raise ValueError("Неизвестное действие с IP-блокировкой")
                session.add(
                    AuditEvent(
                        actor_admin_id=admin_id,
                        event=event,
                        entity_type="web_ip",
                        entity_id=normalized,
                        ip_address=ip_address,
                        correlation_id=str(uuid.uuid4()),
                        details={"reason": reason[:500]} if reason else {},
                    )
                )

    async def settings(self) -> dict[str, str]:
        async with self.session_factory() as session:
            rows = await session.execute(select(RuntimeSetting.key, RuntimeSetting.value))
            return {key: value for key, value in rows.all()}

    async def web_audit(
        self,
        *,
        event: str,
        admin_id: int,
        ip_address: str,
        order_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                session.add(
                    AuditEvent(
                        order_id=order_id,
                        actor_admin_id=admin_id,
                        event=event[:64],
                        entity_type="order" if order_id else "system",
                        entity_id=str(order_id) if order_id else None,
                        ip_address=ip_address,
                        correlation_id=str(uuid.uuid4()),
                        details=details or {},
                    )
                )
