import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from telethon.tl import types

from telegram_mcp_connector.config import Config, parse_allowlist, parse_env_file, write_credentials
from telegram_mcp_connector.formatting import entity_kind, format_entity, format_message
from telegram_mcp_connector.server import build_server
from telegram_mcp_connector.telegram import (
    TelegramService,
    is_send_allowed,
    parse_chat_ref,
    parse_date,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def user(id=1, first="Ann", username=None, is_self=False, bot=False):
    return types.User(id=id, first_name=first, username=username, is_self=is_self, bot=bot)


def channel(id=5, title="News", megagroup=False, username=None):
    return types.Channel(
        id=id, title=title, photo=types.ChatPhotoEmpty(), date=NOW,
        megagroup=megagroup, username=username, access_hash=1,
    )


def group(id=7, title="Family"):
    return types.Chat(
        id=id, title=title, photo=types.ChatPhotoEmpty(), participants_count=3,
        date=NOW, version=1,
    )


# ---- config ---------------------------------------------------------------

def test_env_file_and_env_override(tmp_path: Path, monkeypatch):
    (tmp_path / ".env").write_text(
        '# comment\nTG_API_ID=123\nTG_API_HASH="abc"\nTG_ALLOW_WRITE=yes\n'
        "TG_SEND_ALLOWLIST=@Alice, 42 ,me\n"
    )
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.setenv("TG_API_HASH", "fromenv")
    cfg = Config.load(tmp_path)
    assert cfg.api_id == 123
    assert cfg.api_hash == "fromenv"
    assert cfg.allow_write is True
    assert cfg.send_allowlist == {"alice", "42", "me"}


def test_defaults_are_safe(tmp_path: Path, monkeypatch):
    for k in ("TG_API_ID", "TG_API_HASH", "TG_ALLOW_WRITE", "TG_SEND_ALLOWLIST"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config.load(tmp_path)
    assert not cfg.has_credentials
    assert cfg.allow_write is False
    assert cfg.send_allowlist == frozenset()


def test_write_credentials_creates_private_file(tmp_path: Path):
    cfg = Config(data_dir=tmp_path / "d")
    write_credentials(cfg, 99, "hash")
    values = parse_env_file(cfg.env_path)
    assert values["TG_API_ID"] == "99" and values["TG_API_HASH"] == "hash"
    assert values["TG_ALLOW_WRITE"] == "false"
    assert oct(cfg.env_path.stat().st_mode)[-3:] == "600"
    write_credentials(cfg, 100, "new")  # update in place, no duplicates
    text = cfg.env_path.read_text()
    assert text.count("TG_API_ID=") == 1 and "TG_API_ID=100" in text


def test_parse_allowlist():
    assert parse_allowlist(None) == frozenset()
    assert parse_allowlist(" @Bob,,-1001 ") == {"bob", "-1001"}


# ---- parsing ----------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [(42, 42), ("-1001234", -1001234), (" me ", "me"), ("Saved Messages", "me"),
     ("@durov", "@durov"), ("Family chat", "Family chat")],
)
def test_parse_chat_ref(raw, expected):
    assert parse_chat_ref(raw) == expected


def test_parse_date():
    assert parse_date(None, "x") is None
    assert parse_date("2026-11-09T10:00+02:00", "x") == datetime(2026, 11, 9, 8, tzinfo=timezone.utc)
    assert parse_date("2026-11-09", "x").tzinfo is not None
    with pytest.raises(ToolError):
        parse_date("yesterday", "since")


# ---- formatting -------------------------------------------------------------

def test_entity_kinds_and_ids():
    assert entity_kind(user()) == "user"
    assert entity_kind(user(bot=True)) == "bot"
    assert entity_kind(group()) == "group"
    assert entity_kind(channel()) == "channel"
    assert entity_kind(channel(megagroup=True)) == "group"
    assert format_entity(channel(id=5))["id"] == -1000000000005
    assert format_entity(group(id=7))["id"] == -7
    assert format_entity(user(is_self=True))["title"] == "Saved Messages"


def test_format_message_basic():
    msg = SimpleNamespace(
        id=10, date=NOW, sender=user(username="ann"), out=False, action=None,
        message="hello", media=None, reply_to=SimpleNamespace(reply_to_msg_id=9),
        fwd_from=None, edit_date=None,
    )
    out = format_message(msg)
    assert out["id"] == 10 and out["text"] == "hello" and out["reply_to_id"] == 9
    assert out["sender"] == {"id": 1, "name": "Ann", "username": "ann"}
    assert out["date"].startswith("2026-11-09")


def test_own_messages_show_real_name_not_saved_messages():
    msg = SimpleNamespace(
        id=1, date=NOW, sender=user(id=1, first="Ivan", is_self=True), out=True,
        action=None, message="hi", media=None, reply_to=None, fwd_from=None, edit_date=None,
    )
    out = format_message(msg)
    assert out["sender"] == {"id": 1, "name": "Ivan", "is_me": True}
    assert out["outgoing"] is True


# ---- send allowlist -----------------------------------------------------------

def test_send_allowlist():
    alice = user(id=3, username="Alice")
    assert not is_send_allowed(frozenset(), alice)
    assert is_send_allowed(frozenset({"alice"}), alice)
    assert is_send_allowed(frozenset({"3"}), alice)
    assert not is_send_allowed(frozenset({"me"}), alice)
    assert is_send_allowed(frozenset({"me"}), user(id=1, is_self=True))
    assert is_send_allowed(frozenset({"-1000000000005"}), channel(id=5))
    assert is_send_allowed(frozenset({"*"}), alice)


# ---- resolution with a fake client ------------------------------------------------

class FakeClient:
    def __init__(self, dialogs):
        self.dialogs = dialogs
        self.connected = False

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def is_user_authorized(self):
        return True

    async def get_me(self):
        return user(id=1, is_self=True)

    async def iter_dialogs_gen(self):
        for d in self.dialogs:
            yield d

    def iter_dialogs(self, **_):
        return self.iter_dialogs_gen()

    async def get_entity(self, ref):
        raise ValueError("not cached")


def make_service(tmp_path, dialogs, **cfg):
    (tmp_path / "telegram.session").write_bytes(b"")
    config = Config(data_dir=tmp_path, api_id=1, api_hash="h", **cfg)
    return TelegramService(config, client_factory=lambda _c: FakeClient(dialogs))


def dialog(entity, name):
    return SimpleNamespace(entity=entity, name=name, id=format_entity(entity)["id"])


def test_resolve_by_title(tmp_path):
    fam, work1, work2 = group(7, "Family"), group(8, "Work A"), group(9, "Work B")
    svc = make_service(tmp_path, [dialog(fam, "Family"), dialog(work1, "Work A"), dialog(work2, "Work B")])

    async def run():
        assert await svc.resolve("family") is fam  # exact, case-insensitive
        assert await svc.resolve("work a") is work1
        assert (await svc.resolve("me")).is_self
        with pytest.raises(ToolError, match="several chats"):
            await svc.resolve("work")
        with pytest.raises(ToolError, match="No chat matches"):
            await svc.resolve("nonexistent")

    asyncio.run(run())


def test_not_logged_in(tmp_path):
    svc = TelegramService(Config(data_dir=tmp_path))
    with pytest.raises(ToolError, match="not logged in"):
        asyncio.run(svc.client())


def test_send_blocked_when_write_disabled(tmp_path):
    svc = make_service(tmp_path, [])
    with pytest.raises(ToolError, match="disabled"):
        asyncio.run(svc.send_message("me", "hi", None))


# ---- MCP surface ----------------------------------------------------------------

def tool_names(config):
    async def run():
        async with Client(build_server(config)) as c:
            return {t.name for t in await c.list_tools()}

    return asyncio.run(run())


READ_TOOLS = {"whoami", "list_chats", "get_messages", "get_message_context",
              "search_messages", "get_unread", "get_chat_info"}


def test_read_only_by_default(tmp_path):
    assert tool_names(Config(data_dir=tmp_path)) == READ_TOOLS


def test_write_tools_when_enabled(tmp_path):
    names = tool_names(Config(data_dir=tmp_path, allow_write=True))
    assert names == READ_TOOLS | {"send_message", "mark_read"}


def test_tool_reports_login_needed(tmp_path):
    async def run():
        async with Client(build_server(Config(data_dir=tmp_path))) as c:
            with pytest.raises(ToolError, match="not logged in"):
                await c.call_tool("list_chats", {})

    asyncio.run(run())
