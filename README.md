# telegram-mcp-connector

An [MCP](https://modelcontextprotocol.io) server that lets Claude (Claude Code,
Claude Desktop) read and search **your own Telegram account**, and, if you
turn it on, send messages from it.

It logs in as you over MTProto (via [Telethon](https://github.com/LonamiWebs/Telethon)),
not as a bot, so Claude sees the same chats you do.

> **Security.** The session file gives full access to your Telegram account.
> It stays on your machine, in `.data/` inside this folder, with mode `0600`,
> and is never committed. Write access is **off** by default.

## Tools

| Tool | What it does |
|---|---|
| `whoami` | Which account is connected |
| `list_chats` | Chats with unread counts and a last-message preview; can filter by type or unread, or list the Archive |
| `get_messages` | A chat's history, filterable by date range or sender, with pagination |
| `get_message_context` | A message plus its neighbours (handy after a search hit) |
| `search_messages` | Server-side full-text search in one chat or in all chats |
| `get_unread` | Unread messages grouped by chat (does **not** mark them read) |
| `get_chat_info` | Bio/description, member count, etc. |
| `send_message` ✋ | Send text as you. Only to chats on the allowlist |
| `mark_read` ✋ | Mark a chat as read |

✋ These tools only exist when `TG_ALLOW_WRITE=true`.

A `chat` argument accepts a numeric id, `@username`, a `t.me` link, `me`
(Saved Messages) or a chat title.

## Install

Everything installs **inside this folder**: the virtualenv in `.venv/`, the uv
cache in `.uv-cache/` (see `uv.toml`), and the session and config in `.data/`.
Deleting the folder removes all of it. Requires [uv](https://docs.astral.sh/uv/)
and Python ≥ 3.11.

```bash
git clone https://github.com/nitatsuu/telegram-mcp-connector.git
cd telegram-mcp-connector
uv sync
```

### 1. Get API credentials

Go to <https://my.telegram.org>, open **API development tools**, and create an
app (any name). You get an `api_id` and an `api_hash`.

### 2. Log in (once)

```bash
uv run telegram-mcp-connector login
```

It asks for `api_id`/`api_hash` (saved to `.data/.env`), then prints a **QR
code**. Scan it from your phone: Telegram → Settings → Devices → Link Desktop
Device. If you use 2FA, it then asks for your password. The device shows up as
**telegram-mcp-connector** under Settings → Devices.

To log in with a phone number and code instead, run
`uv run telegram-mcp-connector login --phone`. If you're already logged in on
another device, the code arrives **in the Telegram app** (in the official
"Telegram" chat), not by SMS. Type `r` at the prompt to resend it another way.

Check the result:

```bash
uv run telegram-mcp-connector status
```

### 3. Connect to Claude

**Claude Code:**

```bash
claude mcp add telegram -s user -- uv run --directory "$(pwd)" telegram-mcp-connector
```

**Claude Desktop:** add this to `claude_desktop_config.json`, using absolute paths:

```json
{
  "mcpServers": {
    "telegram": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["run", "--directory", "/path/to/telegram-mcp-connector", "telegram-mcp-connector"]
    }
  }
}
```

Then ask Claude something like *"what did I miss in Telegram today?"* or
*"find where Alex sent the contract"*.

## Enabling writes

Edit `.data/.env`:

```ini
TG_ALLOW_WRITE=true
# ids and/or @usernames, comma-separated. "me" = Saved Messages, "*" = any chat.
TG_SEND_ALLOWLIST=me, @alice, -1001234567890
```

Restart Claude (or the MCP server) for the change to take effect. Get chat ids
from `list_chats`.

**Why the allowlist:** messages Claude reads are written by other people. A
message could contain instructions aimed at an AI. The server tells Claude to
ignore such instructions, Claude asks before calling write tools, and the
allowlist adds a hard limit on top of both.

## Configuration

Values are read from environment variables, then from `<data dir>/.env`.
Environment variables take priority.

| Variable | Default | |
|---|---|---|
| `TG_API_ID`, `TG_API_HASH` | — | From my.telegram.org |
| `TG_ALLOW_WRITE` | `false` | Expose `send_message` / `mark_read` |
| `TG_SEND_ALLOWLIST` | empty | Where `send_message` may post |
| `TG_DATA_DIR` | `<project>/.data` | Session and config location |

## Uninstall

```bash
uv run telegram-mcp-connector logout   # revokes the session on Telegram's side
claude mcp remove telegram -s user     # if added to Claude Code
cd .. && rm -rf telegram-mcp-connector
```

You can also revoke the session from Telegram → Settings → Devices.

## Notes

- Only one process can use the session at a time (it is an SQLite file). If
  `login` or `status` reports *database is locked*, close Claude first.
- Telegram rate limits: short flood waits are slept through automatically.
  This tool is meant for personal use, not bulk messaging.

## Development

```bash
uv run pytest
```

## License

MIT
