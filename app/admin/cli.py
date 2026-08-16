import argparse
import asyncio
import getpass

from app.admin.auth import AdminAuth, totp_uri
from app.config import get_settings
from app.database.session import (
    create_local_schema,
    create_session_factory,
    dispose_session_factory,
)


async def create_owner(username: str, *, without_2fa: bool) -> None:
    password = getpass.getpass("New OWNER password: ")
    confirmation = getpass.getpass("Repeat password: ")
    if password != confirmation:
        raise RuntimeError("Passwords do not match")
    settings = get_settings()
    await create_local_schema(settings.database_url)
    session_factory = create_session_factory(settings.database_url)
    try:
        user, secret = await AdminAuth(settings, session_factory).create_owner(
            username=username,
            password=password,
            with_totp=not without_2fa,
        )
    finally:
        await dispose_session_factory(session_factory)
    print(f"OWNER created: {user.username}")
    if secret:
        print("Add this secret to an authenticator now. It will not be shown in the admin UI.")
        print(f"TOTP secret: {secret}")
        print(f"TOTP URI: {totp_uri(user.username, secret)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="TerStars admin management")
    subparsers = parser.add_subparsers(dest="command", required=True)
    owner = subparsers.add_parser("create-owner", help="Create the first OWNER")
    owner.add_argument("--username", required=True)
    owner.add_argument(
        "--without-2fa",
        action="store_true",
        help="Disable TOTP for local development only",
    )
    args = parser.parse_args()
    if args.command == "create-owner":
        asyncio.run(create_owner(args.username, without_2fa=args.without_2fa))


if __name__ == "__main__":
    main()
