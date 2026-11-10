"""Turn Telethon objects into small JSON-friendly dicts for the model."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from telethon import utils
from telethon.tl import types


def local_iso(dt: datetime | None) -> str | None:
    """Telegram dates are UTC; show them in the machine's local time zone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().isoformat(timespec="seconds")


def entity_kind(entity: Any) -> str:
    if isinstance(entity, types.User):
        return "bot" if entity.bot else "user"
    if isinstance(entity, (types.Chat, types.ChatForbidden)):
        return "group"
    if isinstance(entity, (types.Channel, types.ChannelForbidden)):
        if getattr(entity, "megagroup", False) or getattr(entity, "gigagroup", False):
            return "group"
        return "channel"
    return "unknown"


def entity_title(entity: Any) -> str:
    if isinstance(entity, types.User) and entity.is_self:
        return "Saved Messages"
    name = utils.get_display_name(entity)
    if name:
        return name
    if isinstance(entity, types.User) and entity.deleted:
        return "Deleted Account"
    return str(getattr(entity, "id", "?"))


def format_entity(entity: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": utils.get_peer_id(entity),
        "title": entity_title(entity),
        "type": entity_kind(entity),
    }
    username = getattr(entity, "username", None)
    if username:
        out["username"] = username
    return out


def format_sender(msg: Any) -> dict[str, Any] | None:
    sender = getattr(msg, "sender", None)
    if sender is not None:
        # "Saved Messages" is a chat name; as a sender, show the real name.
        name = utils.get_display_name(sender) or entity_title(sender)
        out: dict[str, Any] = {"id": utils.get_peer_id(sender), "name": name}
        if getattr(sender, "username", None):
            out["username"] = sender.username
        if isinstance(sender, types.User) and sender.is_self:
            out["is_me"] = True
        return out
    sender_id = getattr(msg, "sender_id", None)
    if sender_id is not None:
        return {"id": sender_id}
    return None


def media_kind(msg: Any) -> str | None:
    if not getattr(msg, "media", None):
        return None
    for attr, name in (
        ("photo", "photo"),
        ("sticker", "sticker"),
        ("gif", "gif"),
        ("video_note", "video_note"),
        ("video", "video"),
        ("voice", "voice"),
        ("audio", "audio"),
        ("poll", "poll"),
        ("contact", "contact"),
        ("venue", "venue"),
        ("geo", "location"),
        ("dice", "dice"),
        ("web_preview", "link_preview"),
        ("document", "document"),
    ):
        if getattr(msg, attr, None):
            return name
    return type(msg.media).__name__.removeprefix("MessageMedia").lower() or "media"


def _media_details(msg: Any, kind: str) -> dict[str, Any]:
    details: dict[str, Any] = {"type": kind}
    file = getattr(msg, "file", None)
    if kind in {"document", "audio", "video"} and file is not None:
        if file.name:
            details["file_name"] = file.name
        if file.mime_type:
            details["mime_type"] = file.mime_type
        if file.size:
            details["size_bytes"] = file.size
    if kind in {"voice", "audio", "video", "video_note"} and file is not None and file.duration:
        details["duration_s"] = int(file.duration)
    if kind == "poll":
        poll = msg.poll.poll
        question = getattr(poll.question, "text", poll.question)
        details["question"] = question
        details["options"] = [getattr(a.text, "text", a.text) for a in poll.answers]
    if kind == "link_preview":
        page = msg.web_preview
        if getattr(page, "url", None):
            details["url"] = page.url
        if getattr(page, "title", None):
            details["title"] = page.title
    if kind == "sticker" and file is not None and file.emoji:
        details["emoji"] = file.emoji
    return details


def format_message(msg: Any, *, include_chat: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": msg.id,
        "date": local_iso(msg.date),
        "sender": format_sender(msg),
    }
    if getattr(msg, "out", False):
        out["outgoing"] = True

    action = getattr(msg, "action", None)
    if action is not None:
        out["service_action"] = type(action).__name__.removeprefix("MessageAction")

    text = getattr(msg, "message", None) or ""
    if text:
        out["text"] = text

    kind = media_kind(msg)
    if kind:
        out["media"] = _media_details(msg, kind)

    reply = getattr(msg, "reply_to", None)
    reply_id = getattr(reply, "reply_to_msg_id", None) if reply else None
    if reply_id:
        out["reply_to_id"] = reply_id

    fwd = getattr(msg, "fwd_from", None)
    if fwd is not None:
        fwd_info: dict[str, Any] = {}
        if getattr(fwd, "from_name", None):
            fwd_info["name"] = fwd.from_name
        if getattr(fwd, "from_id", None) is not None:
            fwd_info["id"] = utils.get_peer_id(fwd.from_id)
        out["forwarded_from"] = fwd_info or True

    if getattr(msg, "edit_date", None):
        out["edited"] = local_iso(msg.edit_date)

    if include_chat:
        chat = getattr(msg, "chat", None)
        if chat is not None:
            out["chat"] = {"id": utils.get_peer_id(chat), "title": entity_title(chat)}
        elif getattr(msg, "peer_id", None) is not None:
            out["chat"] = {"id": utils.get_peer_id(msg.peer_id)}
    return out


def is_muted(dialog: Any) -> bool:
    settings = getattr(getattr(dialog, "dialog", None), "notify_settings", None)
    until = getattr(settings, "mute_until", None)
    if until is None:
        return False
    if isinstance(until, datetime):
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return until > datetime.now(timezone.utc)
    return bool(until)


def format_dialog(dialog: Any) -> dict[str, Any]:
    out = format_entity(dialog.entity)
    out["unread_count"] = dialog.unread_count
    if dialog.unread_mentions_count:
        out["unread_mentions"] = dialog.unread_mentions_count
    if getattr(dialog.dialog, "unread_mark", False):
        out["marked_unread"] = True
    if dialog.pinned:
        out["pinned"] = True
    if is_muted(dialog):
        out["muted"] = True
    if dialog.archived:
        out["archived"] = True
    last = dialog.message
    if last is not None:
        preview = (getattr(last, "message", None) or "").replace("\n", " ")
        if not preview:
            kind = media_kind(last)
            preview = f"[{kind}]" if kind else ""
        out["last_message"] = {
            "date": local_iso(last.date),
            "text": preview[:120] + ("…" if len(preview) > 120 else ""),
        }
    return out
