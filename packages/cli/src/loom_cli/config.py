"""Per-computer settings in ``~/.loom/config.json`` (``LOOM_HOME`` gives a second identity)."""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

HOSTED_SERVER = "https://d1a5peogdvb7j1.cloudfront.net"

# LoomAI used to be called Daddy AI: its DADDY_* settings (e.g. DADDY_HOME for a second identity) still work.
for _key, _value in list(os.environ.items()):
    if _key.startswith("DADDY_"):
        os.environ.setdefault("LOOM_" + _key.removeprefix("DADDY_"), _value)


def home_dir() -> Path:
    if configured := os.environ.get("LOOM_HOME"):
        return Path(configured).expanduser()
    home = Path.home() / ".loom"
    _move_legacy_home(home)
    return home


def _move_legacy_home(home: Path) -> None:
    """Daddy AI kept everything in ~/.daddy: move it once, and leave a link so older paths keep working."""
    legacy = home.with_name(".daddy")
    if home.exists() or legacy.is_symlink() or not legacy.is_dir():
        return
    with contextlib.suppress(OSError):
        legacy.rename(home)
        legacy.symlink_to(home, target_is_directory=True)


def config_path() -> Path:
    return home_dir() / "config.json"


@dataclass
class Config:
    server_url: str = HOSTED_SERVER
    token: str | None = None
    user: str | None = None
    family: str | None = None
    default_project: str | None = None
    auto_install_skills: bool = True
    auto_accept: bool = True  # `loom watch` opens messages and saves files to inbox_dir as they arrive
    skill_dirs: list[str] = field(default_factory=lambda: ["~/.cursor/skills", "~/.claude/skills"])
    inbox_dir: str = "~/Loom/inbox"

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
    config.server_url = os.environ.get("LOOM_SERVER_URL", config.server_url).rstrip("/")
    config.token = os.environ.get("LOOM_TOKEN", config.token)
    return config


def save_config(config: Config) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(config), indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)
    return path
