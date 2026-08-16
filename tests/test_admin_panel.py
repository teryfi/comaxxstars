import re
import time

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from app.admin.auth import AdminAuth, _totp, hash_password, verify_password
from app.admin.server import AdminServer
from app.database.models import User
from app.database.session import create_local_schema, create_session_factory
from app.services.container import build_container
from tests.factories import make_settings


def test_admin_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("a-secure-password")
    second = hash_password("a-secure-password")
    assert first != second
    assert verify_password("a-secure-password", first)
    assert not verify_password("wrong-password", first)
    assert "a-secure-password" not in first


async def test_admin_login_session_csrf_and_dashboard(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'admin.db').as_posix()}"
    settings = make_settings(
        DATABASE_URL=database_url,
        PROCESS_ROLE="admin",
        ADMIN_COOKIE_SECURE=False,
    )
    await create_local_schema(database_url)
    session_factory = create_session_factory(database_url)
    auth = AdminAuth(settings, session_factory)
    user, secret = await auth.create_owner(
        username="owner",
        password="a-secure-password",
        with_totp=True,
    )
    assert user.username == "owner"
    assert secret
    async with session_factory() as session:
        async with session.begin():
            session.add(User(telegram_id=42, username="admin_test_user"))

    app = AdminServer(
        settings,
        build_container(settings, session_factory),
        session_factory,
    ).application()
    client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
    await client.start_server()
    try:
        anonymous = await client.get("/admin", allow_redirects=False)
        assert anonymous.status == 302
        assert anonymous.headers["Location"] == "/admin/login"

        failed = await client.post(
            "/admin/login",
            data={"username": "owner", "password": "wrong", "totp": "000000"},
        )
        assert failed.status == 401

        code = _totp(secret, int(time.time()))
        logged_in = await client.post(
            "/admin/login",
            data={"username": "owner", "password": "a-secure-password", "totp": code},
            allow_redirects=False,
        )
        assert logged_in.status == 302
        dashboard = await client.get("/admin")
        html = await dashboard.text()
        assert dashboard.status == 200
        assert "TerStars" in html
        assert "Заказы сегодня" in html
        assert dashboard.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in dashboard.headers["Content-Security-Policy"]

        rejected = await client.post(
            "/admin/system/control",
            data={"key": "maintenance_mode", "value": "on", "confirmation": "CONFIRM"},
        )
        assert rejected.status == 403
        cookie = next(
            cookie for cookie in client.session.cookie_jar if cookie.key == "terstars_admin"
        )
        csrf = cookie.value.split(".", 1)[1]
        accepted = await client.post(
            "/admin/system/control",
            data={
                "csrf": csrf,
                "key": "maintenance_mode",
                "value": "on",
                "confirmation": "CONFIRM",
            },
            allow_redirects=False,
        )
        assert accepted.status == 302

        invalid_block = await client.post(
            "/admin/users/42/action",
            data={"csrf": csrf, "action": "block", "reason": ""},
            allow_redirects=False,
        )
        assert invalid_block.status == 302
        error_page = await client.get(invalid_block.headers["Location"])
        assert error_page.status == 200
        assert "Укажите причину блокировки" in await error_page.text()
    finally:
        await client.close()


async def test_public_order_numbers_are_unique_and_not_sequential(tmp_path) -> None:
    from tests.test_order_lifecycle import CountingProvider, _build, _create_paid

    service, _ = await _build(tmp_path, CountingProvider())
    first = await _create_paid(service, token="public-one", buyer=10)
    second = await _create_paid(service, token="public-two", buyer=11)
    first_number = (await service.get_order_summary(first)).order_number
    second_number = (await service.get_order_summary(second)).order_number
    assert first_number != second_number
    assert re.fullmatch(r"TS-\d{8}-[A-HJ-NP-Z2-9]{8}", first_number)
