"""Keeping `loom` current: update notices and the `loom update` command."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

from loom_common.version import CLI_VERSION

from .config import home_dir

SOURCE = "git+https://github.com/AskArtwentythree/loom-ai#subdirectory=packages/cli"
NOTICE_EVERY_SECONDS = 12 * 3600


def notice(latest: str) -> str:
    return f"A new Loom ({latest}) is available, you have {CLI_VERSION}. Run: loom update"


def due(latest: str, channel: str) -> bool:
    """True at most once every 12 hours per channel ("cli", "agent") and version, so notices don't nag."""
    path = home_dir() / "update-notice.json"
    try:
        seen = json.loads(path.read_text())
    except (OSError, ValueError):
        seen = {}
    last = seen.get(channel, {})
    if last.get("version") == latest and time.time() - last.get("at", 0) < NOTICE_EVERY_SECONDS:
        return False
    seen[channel] = {"version": latest, "at": time.time()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen))
    return True


def installed_version() -> str | None:
    """The version currently installed on disk, which differs from the running one right after an update."""
    try:
        return metadata.version("loom-cli")
    except metadata.PackageNotFoundError:
        return None


def find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / ("uv.exe" if sys.platform == "win32" else "uv")
    return str(candidate) if candidate.exists() else None


def installed_as_uv_tool(uv: str) -> bool:
    result = subprocess.run([uv, "tool", "dir"], capture_output=True, text=True, check=False)
    tools = Path(result.stdout.strip()).resolve() if result.returncode == 0 else None
    return tools is not None and Path(sys.prefix).resolve().is_relative_to(tools)


def legacy_uninstall_command(uv: str) -> list[str]:
    """Removes the tool LoomAI was installed as before the rename (the new one brings `daddy` back)."""
    return [uv, "tool", "uninstall", "daddy-cli"]


def install_command(uv: str) -> list[str]:
    return [uv, "tool", "install", "--force", "--reinstall", "--quiet", os.environ.get("LOOM_SOURCE", SOURCE)]
