import argparse
import asyncio
import getpass
import sys
from pathlib import Path

import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a Telegram user session")
    parser.add_argument(
        "--qr",
        action="store_true",
        help="log in by scanning a QR code instead of receiving a login code",
    )
    return parser.parse_args()


def print_qr(url: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


async def authorize_with_qr(client: TelegramClient) -> None:
    await client.connect()
    if await client.is_user_authorized():
        return

    qr_login = await client.qr_login()
    print("Scan this QR code in Telegram: Settings -> Devices -> Link Desktop Device")
    print_qr(qr_login.url)
    print("Waiting for the QR code to be scanned...")
    try:
        await qr_login.wait()
    except SessionPasswordNeededError:
        try:
            password = getpass.getpass("Enter your Telegram two-step verification password: ")
        except EOFError as error:
            raise RuntimeError(
                "Telegram two-step verification is enabled, but the terminal cannot read the password"
            ) from error
        if not password:
            raise RuntimeError("Telegram two-step verification password cannot be empty")
        await client.sign_in(password=password)


async def main() -> None:
    args = parse_args()
    settings = get_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")
    settings.telegram_session_dir.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(settings.telegram_session_path),
        settings.telegram_api_id,
        settings.secret_value(settings.telegram_api_hash),
    )
    if args.qr:
        await authorize_with_qr(client)
    else:
        await client.start(phone=settings.telegram_user_phone)
    await client.get_me()
    print("Telegram user session authorized successfully.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
