from alembic import command
from alembic.config import Config

from app.config import get_settings


def test_postgres_migrations_create_each_enum_once(monkeypatch, capsys) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://migration:password@postgres:5432/terstars",
    )
    get_settings.cache_clear()
    try:
        command.upgrade(Config("alembic.ini"), "head", sql=True)
        generated_sql = capsys.readouterr().out
    finally:
        get_settings.cache_clear()

    for enum_name in (
        "orderkind",
        "orderstatus",
        "paymentstatus",
        "customerpaymenttype",
        "purchaseattemptstatus",
        "adminrole",
    ):
        assert generated_sql.count(f"CREATE TYPE {enum_name} AS ENUM") == 1
    assert "UPDATE alembic_version SET version_num='0004_admin_panel'" in generated_sql
