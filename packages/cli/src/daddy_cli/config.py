"""Per-computer settings in ``~/.daddy/config.json`` (``DADDY_HOME`` gives a second identity)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def home_dir() -> Path:
    return Path(os.environ.get("DADDY_HOME", Path.home() / ".daddy")).expanduser()


def config_path() -> Path:
    return home_dir() / "config.json"


@dataclass
class Config:
    server_url: str = "http://127.0.0.1:8787"
    token: str | None = None
    user: str | None = None
    family: str | None = None
    default_project: str | None = None
    auto_install_skills: bool = True
    auto_accept: bool = True  # `daddy watch` opens messages and saves files to inbox_dir as they arrive
    skill_dirs: list[str] = field(default_factory=lambda: ["~/.cursor/skills", "~/.claude/skills"])
    inbox_dir: str = "~/Daddy/inbox"

    @property
    def logged_in(self) -> bool:
        return bool(self.token)


def load_config() -> Config:
    path = config_path()
    config = Config()
    if path.exists():
        data = json.loads(path.read_text())
        known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
        config = Config(**known)
    config.server_url = os.environ.get("DADDY_SERVER_URL", config.server_url).rstrip("/")
    config.token = os.environ.get("DADDY_TOKEN", config.token)
    return config


def save_config(config: Config) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(config), indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)
    return path
