"""Command line: `serve` (default, used by Claude), `login`, `status`, `logout`."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from telethon import utils

from telegram_mcp_connector import __version__
from telegram_mcp_connector.config import Config, ensure_private_dir, write_credentials
from telegram_mcp_connector.telegram import make_client


def _err(*args: object) -> None:
    print(*args, file=sys.stderr)


def cmd_serve(_args: argparse.Namespace) -> int:
    from telegram_mcp_connector.server import build_server

    config = Config.load()
    # stdout is the MCP channel: anything informational goes to stderr.
    if not config.session_file.is_file():
        _err(f"telegram-mcp-connector: not logged in yet (no session in {config.data_dir}).")
    build_server(config).run(show_banner=False)
    return 0


def _ask_credentials(config: Config) -> Config:
    print("Telegram API credentials are needed once.")
    print("Get them at https://my.telegram.org -> API development tools.\n")
    while True:
        raw = input("api_id: ").strip()
        if raw.isdigit():
            api_id = int(raw)
            break
        print("api_id is a number, try again.")
    api_hash = getpass.getpass("api_hash (hidden): ").strip()
    if not api_hash:
        raise SystemExit("api_hash is required.")
    write_credentials(config, api_id, api_hash)
    print(f"Saved to {config.env_path}\n")
    return Config.load(config.data_dir)


_CODE_DELIVERY = {
    "SentCodeTypeApp": (
        "in the Telegram app: open the chat named 'Telegram' (blue check mark) "
        "on your phone or desktop — it does NOT come by SMS"
    ),
    "SentCodeTypeSms": "by SMS",
    "SentCodeTypeSmsWord": "by SMS (a word)",
    "SentCodeTypeSmsPhrase": "by SMS (a phrase)",
    "SentCodeTypeFirebaseSms": "by SMS",
    "SentCodeTypeFragmentSms": "via Fragment (fragment.com) for an anonymous number",
    "SentCodeTypeCall": "by a phone call (a voice will read it)",
    "SentCodeTypeFlashCall": "as a missed call: the code is the calling number",
    "SentCodeTypeMissedCall": "as a missed call: the code is the last digits of the calling number",
    "SentCodeTypeEmailCode": "to your login email",
}


def _describe_delivery(sent: object) -> str:
    name = type(getattr(sent, "type", None)).__name__
    if name == "SentCodeTypeSetUpEmailRequired":
        return (
            "nowhere yet: Telegram requires a login email for this account. "
            "Use QR login instead: `uv run telegram-mcp-connector login`"
        )
    return _CODE_DELIVERY.get(name, name)


async def _finish_with_password(client) -> None:
    from telethon.errors import PasswordHashInvalidError

    while True:
        try:
            await client.sign_in(password=getpass.getpass("2FA password (hidden): "))
            return
        except PasswordHashInvalidError:
            print("Wrong password, try again.")


async def _login_qr(client) -> None:
    import qrcode
    from telethon.errors import SessionPasswordNeededError

    print("Log in by QR code:")
    print("  on your phone open Telegram -> Settings -> Devices -> Link Desktop Device,")
    print("  then scan the code below. It refreshes every ~30 seconds.\n")
    qr_login = await client.qr_login()
    while True:
        qr = qrcode.QRCode(border=1)
        qr.add_data(qr_login.url)
        qr.print_ascii(invert=True)
        try:
            await qr_login.wait(timeout=30)
            return
        except asyncio.TimeoutError:
            print("\nCode expired, here is a new one:\n")
            await qr_login.recreate()
        except SessionPasswordNeededError:
            print("Scanned. This account has two-step verification.")
            await _finish_with_password(client)
            return


async def _login_phone(client) -> None:
    from telethon import functions
    from telethon.errors import (
        PhoneCodeExpiredError,
        PhoneCodeInvalidError,
        SessionPasswordNeededError,
    )

    phone = input("Phone number (international format, e.g. +380...): ").strip()
    sent = await client.send_code_request(phone)
    print(f"Code sent {_describe_delivery(sent)}.")
    while True:
        code = input("Login code (or 'r' to resend another way): ").strip()
        if code.lower() == "r":
            try:
                sent = await client(functions.auth.ResendCodeRequest(phone, sent.phone_code_hash))
                print(f"Code re-sent {_describe_delivery(sent)}.")
            except Exception as exc:  # noqa: BLE001 - show Telegram's reason as is
                print(f"Could not resend: {exc}")
            continue
        try:
            await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
            return
        except PhoneCodeInvalidError:
            print("Wrong code, try again.")
        except PhoneCodeExpiredError:
            print("Code expired. Type 'r' to get a new one.")
        except SessionPasswordNeededError:
            await _finish_with_password(client)
            return


async def _login(config: Config, use_phone: bool) -> int:
    client = make_client(config)
    try:
        await client.connect()
        if await client.is_user_authorized():
            print("Already logged in.")
        elif use_phone:
            await _login_phone(client)
        else:
            await _login_qr(client)
        me = await client.get_me()
        print(f"\nLogged in as {utils.get_display_name(me)} (id {me.id}).")
    finally:
        await client.disconnect()
    if config.session_file.is_file():
        os.chmod(config.session_file, 0o600)
    print(f"Session stored in {config.session_file} — treat it like a password.")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    config = Config.load()
    ensure_private_dir(config.data_dir)
    if not config.has_credentials:
        config = _ask_credentials(config)
    return asyncio.run(_login(config, use_phone=args.phone))


async def _status(config: Config) -> int:
    print(f"Data dir:    {config.data_dir}")
    print(f"Write mode:  {'ON' if config.allow_write else 'off'}")
    if config.allow_write:
        print(f"Allowlist:   {', '.join(sorted(config.send_allowlist)) or '(empty — sending blocked)'}")
    if not config.has_credentials or not config.session_file.is_file():
        print("Logged in:   no  -> run `uv run telegram-mcp-connector login`")
        return 1
    client = make_client(config)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("Logged in:   no (login not finished, or session revoked) -> run login again")
            return 1
        me = await client.get_me()
        print(f"Logged in:   yes, as {utils.get_display_name(me)} (id {me.id})")
        return 0
    finally:
        await client.disconnect()


def cmd_status(_args: argparse.Namespace) -> int:
    return asyncio.run(_status(Config.load()))


async def _logout(config: Config) -> int:
    if config.has_credentials and config.session_file.is_file():
        client = make_client(config)
        try:
            await client.connect()
            if await client.is_user_authorized():
                await client.log_out()  # also revokes the session on Telegram's side
                print("Session revoked on Telegram.")
        finally:
            await client.disconnect()
    for path in (config.session_file, config.session_file.with_name(config.session_file.name + "-journal")):
        if path.exists():
            path.unlink()
    print("Local session removed. (API credentials in .env are kept.)")
    return 0


def cmd_logout(_args: argparse.Namespace) -> int:
    return asyncio.run(_logout(Config.load()))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="telegram-mcp-connector",
        description="MCP server giving Claude access to your Telegram account.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server over stdio (default)").set_defaults(func=cmd_serve)
    login = sub.add_parser("login", help="log in to Telegram (QR code by default)")
    login.add_argument("--phone", action="store_true", help="log in with phone number + code instead of QR")
    login.set_defaults(func=cmd_login)
    sub.add_parser("status", help="show login state and settings").set_defaults(func=cmd_status)
    sub.add_parser("logout", help="revoke and delete the session").set_defaults(func=cmd_logout)
    args = parser.parse_args(argv)
    func = getattr(args, "func", cmd_serve)
    try:
        sys.exit(func(args))
    except KeyboardInterrupt:
        sys.exit(130)
