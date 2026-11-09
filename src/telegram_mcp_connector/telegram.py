"""Telegram access layer: one lazily connected Telethon client + operations."""

from __future__ import annotations

import asyncio
import platform
import re
from datetime import datetime, timezone
from typing import Any, Callable

from fastmcp.exceptions import ToolError
from telethon import TelegramClient, functions, utils
from telethon.errors import RPCError
from telethon.tl import types

from telegram_mcp_connector import __version__
from telegram_mcp_connector.config import Config
from telegram_mcp_connector.formatting import (
    entity_kind,
    format_dialog,
    format_entity,
    format_message,
    is_muted,
)

_NUMERIC = re.compile(r"^-?\d+$")
_LINK = re.compile(r"^(?:https?://)?(?:t|telegram)\.me/", re.IGNORECASE)
_SELF_ALIASES = {"me", "self", "saved", "saved messages", "избранное"}

NOT_LOGGED_IN = (
    "Telegram is not logged in. Ask the user to run "
    "`uv run telegram-mcp-connector login` in the project folder."
)


def make_client(config: Config) -> TelegramClient:
    return TelegramClient(
        str(config.session_path),
        config.api_id,
        config.api_hash,
        device_model="telegram-mcp-connector",
        system_version=f"{platform.system()} {platform.release()}",
        app_version=__version__,
    )


def parse_chat_ref(chat: str | int) -> str | int:
    if isinstance(chat, int):
        return chat
    ref = chat.strip()
    if _NUMERIC.match(ref):
        return int(ref)
    if ref.casefold() in _SELF_ALIASES:
        return "me"
    return ref


def parse_date(value: str | None, name: str) -> datetime | None:
    """ISO date or datetime; naive values are taken as local time."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ToolError(f"{name} must be ISO 8601 (e.g. 2026-11-09 or 2026-11-09T18:00), got {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.astimezone()  # interpret as local time
    return dt.astimezone(timezone.utc)


def is_send_allowed(allowlist: frozenset[str], entity: Any) -> bool:
    if "*" in allowlist:
        return True
    if isinstance(entity, types.User) and entity.is_self and "me" in allowlist:
        return True
    peer_id = str(utils.get_peer_id(entity))
    if peer_id in allowlist:
        return True
    usernames = set()
    if getattr(entity, "username", None):
        usernames.add(entity.username.lower())
    for extra in getattr(entity, "usernames", None) or []:
        usernames.add(extra.username.lower())
    return bool(usernames & allowlist)


class TelegramService:
    def __init__(
        self,
        config: Config,
        client_factory: Callable[[Config], Any] = make_client,
    ) -> None:
        self.config = config
        self._factory = client_factory
        self._client: Any = None
        self._lock = asyncio.Lock()

    # ---- connection -------------------------------------------------------

    async def client(self) -> Any:
        async with self._lock:
            if self._client is not None and self._client.is_connected():
                return self._client
            if not self.config.has_credentials or not self.config.session_file.is_file():
                raise ToolError(NOT_LOGGED_IN)
            client = self._factory(self.config)
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                raise ToolError(NOT_LOGGED_IN)
            self._client = client
            return client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    # ---- chat resolution --------------------------------------------------

    async def resolve(self, chat: str | int) -> Any:
        client = await self.client()
        ref = parse_chat_ref(chat)

        if ref == "me":
            return await client.get_me()

        if isinstance(ref, int):
            try:
                return await client.get_entity(ref)
            except (ValueError, RPCError):
                # Not in the session cache yet: loading dialogs fills it.
                async for _ in client.iter_dialogs():
                    pass
                try:
                    return await client.get_entity(ref)
                except (ValueError, RPCError) as exc:
                    raise ToolError(f"Chat with id {ref} not found.") from exc

        if ref.startswith("@") or _LINK.match(ref):
            try:
                return await client.get_entity(ref)
            except (ValueError, RPCError) as exc:
                raise ToolError(f"Chat {ref!r} not found.") from exc

        # Otherwise: match against dialog titles.
        needle = ref.casefold()
        exact, partial = [], []
        async for dialog in client.iter_dialogs():
            name = (dialog.name or "").casefold()
            if name == needle:
                exact.append(dialog)
            elif needle in name:
                partial.append(dialog)
        for group in (exact, partial):
            if len(group) == 1:
                return group[0].entity
            if len(group) > 1:
                options = ", ".join(f"{d.name!r} (id {d.id})" for d in group[:10])
                raise ToolError(f"{chat!r} matches several chats: {options}. Use the id.")

        # Last try: a bare username without '@'.
        try:
            return await client.get_entity(ref)
        except (ValueError, RPCError) as exc:
            raise ToolError(
                f"No chat matches {chat!r}. Use list_chats to find the id."
            ) from exc

    # ---- read operations --------------------------------------------------

    async def whoami(self) -> dict[str, Any]:
        client = await self.client()
        me = await client.get_me()
        out = format_entity(me)
        out["title"] = utils.get_display_name(me)
        if me.phone:
            out["phone"] = f"+{me.phone[:-4]}****" if len(me.phone) > 4 else "****"
        return out

    async def list_chats(
        self, limit: int, unread_only: bool, archived: bool, kind: str
    ) -> list[dict[str, Any]]:
        client = await self.client()
        out: list[dict[str, Any]] = []
        async for dialog in client.iter_dialogs(archived=archived):
            if kind != "all" and entity_kind(dialog.entity) != kind:
                continue
            if unread_only and not (
                dialog.unread_count
                or dialog.unread_mentions_count
                or getattr(dialog.dialog, "unread_mark", False)
            ):
                continue
            out.append(format_dialog(dialog))
            if len(out) >= limit:
                break
        return out

    async def get_messages(
        self,
        chat: str | int,
        limit: int,
        before_id: int | None,
        since: str | None,
        until: str | None,
        from_user: str | int | None,
    ) -> dict[str, Any]:
        client = await self.client()
        entity = await self.resolve(chat)
        since_dt = parse_date(since, "since")
        kwargs: dict[str, Any] = {"limit": limit}
        if before_id:
            kwargs["offset_id"] = before_id
        if until_dt := parse_date(until, "until"):
            kwargs["offset_date"] = until_dt
        if from_user is not None:
            kwargs["from_user"] = await self.resolve(from_user)

        messages = []
        reached_since = False
        async for msg in client.iter_messages(entity, **kwargs):
            if since_dt and msg.date < since_dt:
                reached_since = True
                break
            messages.append(msg)
        messages.reverse()  # chronological

        result: dict[str, Any] = {
            "chat": format_entity(entity),
            "messages": [format_message(m) for m in messages],
        }
        if messages and len(messages) >= limit and not reached_since:
            result["older_messages_before_id"] = messages[0].id
        return result

    async def get_message_context(
        self, chat: str | int, message_id: int, radius: int
    ) -> dict[str, Any]:
        client = await self.client()
        entity = await self.resolve(chat)
        older = [m async for m in client.iter_messages(entity, limit=radius, offset_id=message_id)]
        target = await client.get_messages(entity, ids=message_id)
        if target is None:
            raise ToolError(f"Message {message_id} not found in this chat.")
        newer = [
            m
            async for m in client.iter_messages(entity, limit=radius, min_id=message_id, reverse=True)
        ]
        messages = [*reversed(older), target, *newer]
        return {
            "chat": format_entity(entity),
            "target_id": message_id,
            "messages": [format_message(m) for m in messages],
        }

    async def search_messages(
        self, query: str, chat: str | int | None, limit: int
    ) -> dict[str, Any]:
        client = await self.client()
        if chat is None:
            found = [
                format_message(m, include_chat=True)
                async for m in client.iter_messages(None, search=query, limit=limit)
            ]
            return {"scope": "all chats", "messages": found}
        entity = await self.resolve(chat)
        found = [
            format_message(m) async for m in client.iter_messages(entity, search=query, limit=limit)
        ]
        return {"scope": format_entity(entity), "messages": found}

    async def get_unread(
        self, max_chats: int, per_chat: int, include_muted: bool
    ) -> list[dict[str, Any]]:
        client = await self.client()
        out: list[dict[str, Any]] = []
        async for dialog in client.iter_dialogs(archived=False):
            if dialog.unread_count <= 0:
                continue
            if is_muted(dialog) and not include_muted:
                continue
            take = min(dialog.unread_count, per_chat)
            msgs = [m async for m in client.iter_messages(dialog.entity, limit=take)]
            msgs.reverse()
            entry = format_dialog(dialog)
            entry.pop("last_message", None)
            entry["messages"] = [format_message(m) for m in msgs]
            if dialog.unread_count > take:
                entry["truncated"] = True
            out.append(entry)
            if len(out) >= max_chats:
                break
        return out

    async def get_chat_info(self, chat: str | int) -> dict[str, Any]:
        client = await self.client()
        entity = await self.resolve(chat)
        out = format_entity(entity)
        try:
            if isinstance(entity, types.User):
                full = await client(functions.users.GetFullUserRequest(entity))
                fu = full.full_user
                out.update(
                    about=fu.about,
                    common_chats_count=fu.common_chats_count,
                    phone=entity.phone and f"+{entity.phone}",
                    is_contact=bool(entity.contact),
                    is_bot=bool(entity.bot),
                )
            elif isinstance(entity, types.Channel):
                full = await client(functions.channels.GetFullChannelRequest(entity))
                out.update(
                    about=full.full_chat.about,
                    participants_count=full.full_chat.participants_count,
                )
            elif isinstance(entity, types.Chat):
                full = await client(functions.messages.GetFullChatRequest(entity.id))
                out.update(
                    about=full.full_chat.about,
                    participants_count=entity.participants_count,
                )
        except RPCError as exc:
            out["details_error"] = str(exc)
        return {k: v for k, v in out.items() if v not in (None, "")}

    # ---- write operations (only exposed when TG_ALLOW_WRITE=true) ----------

    async def send_message(
        self, chat: str | int, text: str, reply_to_id: int | None
    ) -> dict[str, Any]:
        if not self.config.allow_write:
            raise ToolError("Writing is disabled (TG_ALLOW_WRITE=false).")
        client = await self.client()
        entity = await self.resolve(chat)
        if not is_send_allowed(self.config.send_allowlist, entity):
            target = format_entity(entity)
            raise ToolError(
                f"Sending to {target['title']!r} (id {target['id']}) is not allowed. "
                "Ask the user to add this id or @username to TG_SEND_ALLOWLIST."
            )
        sent = await client.send_message(entity, text, reply_to=reply_to_id)
        return {"chat": format_entity(entity), "sent": format_message(sent)}

    async def mark_read(self, chat: str | int) -> dict[str, Any]:
        if not self.config.allow_write:
            raise ToolError("Writing is disabled (TG_ALLOW_WRITE=false).")
        client = await self.client()
        entity = await self.resolve(chat)
        await client.send_read_acknowledge(entity)
        return {"chat": format_entity(entity), "marked_read": True}
