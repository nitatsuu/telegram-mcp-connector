"""Configuration: environment variables, overlaid on <data dir>/.env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_FILE_NAME = ".env"
SESSION_NAME = "telegram"  # Telethon appends ".session"

_TRUE = {"1", "true", "yes", "on"}


def default_data_dir() -> Path:
    """<project>/.data when running from a source checkout, else ~/.config/...

    Keeping the data next to the code means deleting the project folder
    removes the session too.
    """
    override = os.environ.get("TG_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    project_root = Path(__file__).resolve().parents[2]
    if (project_root / "pyproject.toml").is_file():
        return project_root / ".data"
    return Path.home() / ".config" / "telegram-mcp-connector"


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser (comments, blank lines, optional quotes)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def parse_allowlist(raw: str | None) -> frozenset[str]:
    items = set()
    for part in (raw or "").split(","):
        part = part.strip().lower().removeprefix("@")
        if part:
            items.add(part)
    return frozenset(items)


@dataclass(frozen=True)
class Config:
    data_dir: Path
    api_id: int | None = None
    api_hash: str | None = None
    allow_write: bool = False
    send_allowlist: frozenset[str] = field(default_factory=frozenset)

    @property
    def env_path(self) -> Path:
        return self.data_dir / ENV_FILE_NAME

    @property
    def session_path(self) -> Path:
        """Path without the .session suffix, as Telethon expects."""
        return self.data_dir / SESSION_NAME

    @property
    def session_file(self) -> Path:
        return self.data_dir / f"{SESSION_NAME}.session"

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_id and self.api_hash)

    @classmethod
    def load(cls, data_dir: Path | None = None) -> Config:
        data_dir = data_dir or default_data_dir()
        values = parse_env_file(data_dir / ENV_FILE_NAME)
        # Real environment variables win over the file.
        values.update({k: v for k, v in os.environ.items() if k.startswith("TG_")})

        api_id_raw = values.get("TG_API_ID", "").strip()
        try:
            api_id = int(api_id_raw) if api_id_raw else None
        except ValueError as exc:
            raise ValueError(f"TG_API_ID must be a number, got {api_id_raw!r}") from exc

        return cls(
            data_dir=data_dir,
            api_id=api_id,
            api_hash=values.get("TG_API_HASH", "").strip() or None,
            allow_write=values.get("TG_ALLOW_WRITE", "").strip().lower() in _TRUE,
            send_allowlist=parse_allowlist(values.get("TG_SEND_ALLOWLIST")),
        )


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def write_credentials(config: Config, api_id: int, api_hash: str) -> None:
    """Create or update TG_API_ID / TG_API_HASH in <data dir>/.env (mode 0600)."""
    ensure_private_dir(config.data_dir)
    path = config.env_path
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    wanted = {"TG_API_ID": str(api_id), "TG_API_HASH": api_hash}
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in wanted:
            out.append(f"{key}={wanted.pop(key)}")
        else:
            out.append(line)
    if not lines:
        out.append("# Written by `telegram-mcp-connector login`. Keep this file secret.")
    out.extend(f"{k}={v}" for k, v in wanted.items())
    if not any(line.startswith("TG_ALLOW_WRITE") for line in out):
        out += ["TG_ALLOW_WRITE=false", "TG_SEND_ALLOWLIST=me"]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
