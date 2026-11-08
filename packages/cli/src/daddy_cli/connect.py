"""``daddy connect``: register the local MCP server with Cursor, Claude Code and the Claude desktop app."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .config import home_dir

RULE = """---
description: Daddy AI, the team's shared project brain
alwaysApply: true
---
This project is connected to Daddy AI (MCP server "daddy").
- For questions about this project's code, decisions, docs or teammates, call `ask_project`
  and cite its sources. If it says "Not in project knowledge.", say so instead of guessing.
- To share a file, skill or message with a teammate, call `send_to_teammate`.
- At the start of a conversation, call `check_inbox` and mention new items.
- When the user asks to remember a decision for the team, call `save_note`.
- Never send credentials, `.env` files or keys to teammates.
"""


def executable() -> str:
    """Absolute path of this ``daddy`` command, so agents can launch it without a PATH."""
    argv0 = Path(sys.argv[0])
    if argv0.name == "daddy" and argv0.exists():
        return str(argv0.absolute())
    found = shutil.which("daddy")
    if found is None:
        raise RuntimeError("Cannot find the `daddy` executable; install the CLI first")
    return found


def _custom_home() -> str | None:
    default = Path.home() / ".daddy"
    return None if home_dir() == default else str(home_dir())


def _load_json(path: Path) -> dict:
    if not path.exists() or not path.read_text().strip():
        return {}
    return json.loads(path.read_text())


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def mcp_entry(project_dir: Path | None = None) -> dict:
    env = {}
    if home := _custom_home():
        env["DADDY_HOME"] = home
    if project_dir is not None:
        env["DADDY_WORKSPACE"] = str(project_dir.resolve())
    entry: dict = {"command": executable(), "args": ["mcp"]}
    if env:
        entry["env"] = env
    return entry


def connect_cursor(project_dir: Path | None, hooks: bool) -> list[str]:
    """Add Daddy AI to Cursor; returns a description of each change made."""
    changes = []
    base = project_dir.resolve() / ".cursor" if project_dir else Path.home() / ".cursor"
    mcp_path = base / "mcp.json"
    config = _load_json(mcp_path)
    config.setdefault("mcpServers", {})["daddy"] = mcp_entry(project_dir)
    _write_json(mcp_path, config)
    changes.append(f"MCP server 'daddy' → {mcp_path}")

    if project_dir is not None:
        rule_path = base / "rules" / "daddy-ai.mdc"
        rule_path.parent.mkdir(parents=True, exist_ok=True)
        rule_path.write_text(RULE)
        changes.append(f"Agent rule → {rule_path}")

    if hooks:
        changes.append(install_cursor_hooks(base / "hooks.json"))
    return changes


def _hook_command(name: str) -> str:
    command = f"{shlex.quote(executable())} hook {name}"
    if home := _custom_home():
        command = f"DADDY_HOME={shlex.quote(home)} {command}"
    return command


def install_cursor_hooks(path: Path) -> str:
    """Merge Daddy AI's prompt guard and audit hooks into a Cursor ``hooks.json``."""
    config = _load_json(path)
    config.setdefault("version", 1)
    all_hooks = config.setdefault("hooks", {})
    wanted = {
        "beforeSubmitPrompt": _hook_command("prompt-check"),
        "afterMCPExecution": _hook_command("audit"),
        "stop": _hook_command("audit"),
    }
    for event, command in wanted.items():
        entries = all_hooks.setdefault(event, [])
        entries[:] = [
            e
            for e in entries
            if " hook prompt-check" not in e.get("command", "") and " hook audit" not in e.get("command", "")
        ]
        entries.append({"command": command})
    _write_json(path, config)
    return f"Prompt guard and audit hooks → {path}"


def claude_command() -> list[str]:
    command = ["claude", "mcp", "add", "--scope", "user"]
    if home := _custom_home():
        command += ["-e", f"DADDY_HOME={home}"]
    return [*command, "daddy", "--", executable(), "mcp"]


def _claude_code() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for candidate in (Path.home() / ".local" / "bin" / "claude", Path.home() / ".claude" / "local" / "claude"):
        if candidate.exists():
            return str(candidate)
    return None


def connect_claude() -> tuple[bool, str]:
    """Register with Claude Code; returns (done, message)."""
    claude = _claude_code()
    command = claude_command()
    if claude is None:
        return False, "Claude Code is not installed here. Once it is, run:\n  " + shlex.join(command)
    subprocess.run(
        [claude, "mcp", "remove", "--scope", "user", "daddy"],
        check=False,
        capture_output=True,
        env=os.environ,
    )
    result = subprocess.run([claude, *command[1:]], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return False, f"`claude mcp add` failed: {result.stderr.strip() or result.stdout.strip()}"
    return True, "MCP server 'daddy' added to Claude Code (user scope)"


def claude_desktop_config() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def connect_claude_desktop() -> str:
    """Add Daddy AI to the Claude desktop app's config; it picks it up after a full restart."""
    path = claude_desktop_config()
    config = _load_json(path)
    config.setdefault("mcpServers", {})["daddy"] = mcp_entry()
    _write_json(path, config)
    return f"MCP server 'daddy' → {path}"


def detect_agents() -> list[str]:
    """Agents installed on this computer, in the names `daddy connect` accepts."""
    found = []
    if (Path.home() / ".cursor").is_dir() or Path("/Applications/Cursor.app").exists() or shutil.which("cursor"):
        found.append("cursor")
    if _claude_code():
        found.append("claude")
    if claude_desktop_config().parent.is_dir() or Path("/Applications/Claude.app").exists():
        found.append("claude-desktop")
    return found
