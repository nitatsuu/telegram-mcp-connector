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


async def _login(config: Config) -> int:
    client = make_client(config)
    try:
        await client.start(
            phone=lambda: input("Phone number (international format, e.g. +380...): ").strip(),
            code_callback=lambda: input("Login code from Telegram: ").strip(),
            password=lambda: getpass.getpass("2FA password (hidden): "),
        )
        me = await client.get_me()
        print(f"\nLogged in as {utils.get_display_name(me)} (id {me.id}).")
    finally:
        await client.disconnect()
    if config.session_file.is_file():
        os.chmod(config.session_file, 0o600)
    print(f"Session stored in {config.session_file} — treat it like a password.")
    return 0


def cmd_login(_args: argparse.Namespace) -> int:
    config = Config.load()
    ensure_private_dir(config.data_dir)
    if not config.has_credentials:
        config = _ask_credentials(config)
    return asyncio.run(_login(config))


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
            print("Logged in:   no (session expired or revoked) -> run login again")
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
    sub.add_parser("login", help="log in to Telegram interactively").set_defaults(func=cmd_login)
    sub.add_parser("status", help="show login state and settings").set_defaults(func=cmd_status)
    sub.add_parser("logout", help="revoke and delete the session").set_defaults(func=cmd_logout)
    args = parser.parse_args(argv)
    func = getattr(args, "func", cmd_serve)
    try:
        sys.exit(func(args))
    except KeyboardInterrupt:
        sys.exit(130)
