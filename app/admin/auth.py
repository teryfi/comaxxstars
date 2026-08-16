import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.database.models import AdminSession, AdminUser, AuditEvent
from app.domain import AdminRole

COOKIE_NAME = "terstars_admin"


@dataclass(frozen=True)
class AdminPrincipal:
    id: int
    username: str
    role: AdminRole
    csrf_token: str

    def can_manage(self) -> bool:
        return self.role in {AdminRole.OWNER, AdminRole.ADMIN}

    def is_owner(self) -> bool:
        return self.role == AdminRole.OWNER


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return (
        "scrypt$16384$8$1$"
        + base64.urlsafe_b64encode(salt).decode()
        + "$"
        + base64.urlsafe_b64encode(derived).decode()
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_value, digest_value = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_value)
        expected = base64.urlsafe_b64decode(digest_value)
        actual = hashlib.scrypt(
            password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def verify_totp(secret: str, code: str, *, now: int | None = None) -> bool:
    normalized = code.strip()
    if len(normalized) != 6 or not normalized.isdecimal():
        return False
    timestamp = int(time.time() if now is None else now)
    for offset in (-1, 0, 1):
        if hmac.compare_digest(_totp(secret, timestamp + offset * 30), normalized):
            return True
    return False


def totp_uri(username: str, secret: str) -> str:
    label = quote(f"TerStars:{username}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer=TerStars&algorithm=SHA1&digits=6&period=30"
    )


def _totp(secret: str, timestamp: int) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    counter = struct.pack(">Q", timestamp // 30)
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{number:06d}"


class AdminAuth:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    async def create_owner(
        self,
        *,
        username: str,
        password: str,
        with_totp: bool = True,
    ) -> tuple[AdminUser, str | None]:
        normalized = username.strip().lower()
        if not normalized or len(normalized) > 64:
            raise ValueError("Invalid admin username")
        secret = new_totp_secret() if with_totp else None
        async with self.session_factory() as session:
            async with session.begin():
                if await session.scalar(select(AdminUser.id).limit(1)) is not None:
                    raise RuntimeError("An admin user already exists")
                user = AdminUser(
                    username=normalized,
                    password_hash=hash_password(password),
                    role=AdminRole.OWNER,
                    totp_secret=secret,
                )
                session.add(user)
                await session.flush()
                session.add(
                    AuditEvent(
                        actor_admin_id=user.id,
                        event="admin_owner_created",
                        correlation_id=str(uuid.uuid4()),
                        entity_type="admin_user",
                        entity_id=str(user.id),
                        details={"username": normalized, "totp_enabled": bool(secret)},
                    )
                )
                return user, secret

    async def login(
        self,
        *,
        username: str,
        password: str,
        totp_code: str,
        ip_address: str,
        user_agent: str,
    ) -> tuple[str, AdminPrincipal] | None:
        normalized = username.strip().lower()
        key = f"{ip_address}:{normalized}"
        if not self._allow_attempt(key):
            await self._audit_login("admin_login_rate_limited", normalized, ip_address)
            return None
        async with self.session_factory() as session:
            async with session.begin():
                user = await session.scalar(
                    select(AdminUser).where(AdminUser.username == normalized)
                )
                valid = bool(
                    user
                    and user.is_active
                    and verify_password(password, user.password_hash)
                    and (not user.totp_secret or verify_totp(user.totp_secret, totp_code))
                )
                if not valid or user is None:
                    session.add(
                        AuditEvent(
                            event="admin_login_failed",
                            correlation_id=str(uuid.uuid4()),
                            ip_address=ip_address,
                            details={"username": normalized},
                        )
                    )
                    return None
                raw_token = secrets.token_urlsafe(32)
                csrf_token = secrets.token_urlsafe(32)
                session.add(
                    AdminSession(
                        token_hash=_hash(raw_token),
                        admin_user_id=user.id,
                        csrf_token_hash=_hash(csrf_token),
                        ip_address=ip_address,
                        user_agent=user_agent[:256],
                        expires_at=datetime.now(UTC)
                        + timedelta(hours=self.settings.admin_session_hours),
                    )
                )
                session.add(
                    AuditEvent(
                        actor_admin_id=user.id,
                        event="admin_login",
                        correlation_id=str(uuid.uuid4()),
                        ip_address=ip_address,
                        entity_type="admin_user",
                        entity_id=str(user.id),
                        details={},
                    )
                )
                self._attempts.pop(key, None)
                cookie_value = f"{raw_token}.{csrf_token}"
                return cookie_value, AdminPrincipal(user.id, user.username, user.role, csrf_token)

    async def principal(
        self, cookie_value: str | None, *, ip_address: str
    ) -> AdminPrincipal | None:
        parsed = _parse_cookie(cookie_value)
        if parsed is None:
            return None
        raw_token, csrf_token = parsed
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            row = await session.execute(
                select(AdminSession, AdminUser)
                .join(AdminUser, AdminUser.id == AdminSession.admin_user_id)
                .where(
                    AdminSession.token_hash == _hash(raw_token),
                    AdminSession.expires_at > now,
                    AdminUser.is_active.is_(True),
                )
            )
            record = row.first()
            if record is None:
                return None
            admin_session, user = record
            if not hmac.compare_digest(admin_session.csrf_token_hash, _hash(csrf_token)):
                return None
            if admin_session.ip_address and admin_session.ip_address != ip_address:
                return None
            return AdminPrincipal(user.id, user.username, user.role, csrf_token)

    async def logout(
        self, cookie_value: str | None, principal: AdminPrincipal, ip_address: str
    ) -> None:
        parsed = _parse_cookie(cookie_value)
        async with self.session_factory() as session:
            async with session.begin():
                if parsed:
                    await session.execute(
                        delete(AdminSession).where(AdminSession.token_hash == _hash(parsed[0]))
                    )
                session.add(
                    AuditEvent(
                        actor_admin_id=principal.id,
                        event="admin_logout",
                        correlation_id=str(uuid.uuid4()),
                        ip_address=ip_address,
                        details={},
                    )
                )

    def _allow_attempt(self, key: str) -> bool:
        now = time.monotonic()
        attempts = self._attempts[key]
        cutoff = now - self.settings.admin_login_window_seconds
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        if len(attempts) >= self.settings.admin_login_attempts:
            return False
        attempts.append(now)
        return True

    async def _audit_login(self, event: str, username: str, ip_address: str) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                session.add(
                    AuditEvent(
                        event=event,
                        ip_address=ip_address,
                        correlation_id=str(uuid.uuid4()),
                        details={"username": username},
                    )
                )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _parse_cookie(value: str | None) -> tuple[str, str] | None:
    if not value or value.count(".") != 1:
        return None
    token, csrf = value.split(".", 1)
    if len(token) < 32 or len(csrf) < 32:
        return None
    return token, csrf
