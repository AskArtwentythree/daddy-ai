"""Installs from before the Daddy AI -> LoomAI rename keep working and move over by themselves."""

import json

from loom_cli import connect
from loom_cli.config import home_dir, load_config
from loom_common import guard
from loom_common.version import cli_version_of


def test_old_settings_folder_moves_and_leaves_a_link(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LOOM_HOME", raising=False)
    legacy = tmp_path / ".daddy"
    legacy.mkdir()
    (legacy / "config.json").write_text(json.dumps({"token": "dad_x", "user": "Askar"}))

    assert home_dir() == tmp_path / ".loom"
    assert load_config().token == "dad_x"
    assert legacy.is_symlink() and (legacy / "config.json").exists()
    assert home_dir() == tmp_path / ".loom"


def test_old_environment_names_still_work(tmp_path):
    import os
    import subprocess
    import sys

    env = {**os.environ, "DADDY_HOME": str(tmp_path / "team"), "HOME": str(tmp_path)}
    env.pop("LOOM_HOME", None)
    code = "from loom_cli.config import home_dir; print(home_dir())"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == str(tmp_path / "team")


def test_connect_replaces_old_daddy_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOOM_HOME", str(tmp_path / ".loom"))
    monkeypatch.setattr(connect, "executable", lambda: "/opt/loom")
    project = tmp_path / "app"
    rules = project / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "daddy-ai.mdc").write_text("old rule")
    (project / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {"daddy": {}, "other": {}}}))
    (project / ".cursor" / "hooks.json").write_text(json.dumps(
        {"version": 1, "hooks": {"stop": [{"command": "/old/bin/daddy hook audit"}]}}))
    desktop = connect.claude_desktop_config()
    desktop.parent.mkdir(parents=True)
    desktop.write_text(json.dumps({"mcpServers": {"daddy": {"command": "/old/bin/daddy"}}}))

    connect.connect_cursor(project, hooks=True)
    assert connect.connect_claude_desktop()[1] is True

    assert set(json.loads((project / ".cursor" / "mcp.json").read_text())["mcpServers"]) == {"loom", "other"}
    assert not (rules / "daddy-ai.mdc").exists() and (rules / "loom-ai.mdc").exists()
    stop = json.loads((project / ".cursor" / "hooks.json").read_text())["hooks"]["stop"]
    assert [h["command"] for h in stop] == ["/opt/loom hook audit"]
    assert set(json.loads(desktop.read_text())["mcpServers"]) == {"loom"}


def test_guard_protects_the_old_folder_and_old_clients_are_recognised():
    assert guard.evaluate("shell", "cat ~/.daddy/config.json").verdict == "deny"
    assert guard.evaluate("read", "/Users/a/.daddy/config.json").verdict == "deny"
    assert guard.evaluate("read", "/Users/a/.loom/config.json").verdict == "deny"
    assert cli_version_of("daddy-cli/0.3.0") == "0.3.0"
    assert cli_version_of("loom-cli/0.4.0") == "0.4.0"
