import json

import pytest

from daddy_cli import connect
from daddy_cli.config import Config
from daddy_cli.hooks import _snake
from daddy_cli.installer import install


def _config(tmp_path) -> Config:
    return Config(
        skill_dirs=[str(tmp_path / "cursor"), str(tmp_path / "claude")], inbox_dir=str(tmp_path / "inbox")
    )


def test_skill_installs_into_every_agent(tmp_path):
    item = {"kind": "skill", "filename": "demo", "install_path": "skills/demo/SKILL.md", "from": "Alice"}
    written = install(item, "# skill", _config(tmp_path))
    assert sorted(p.parent.parent.name for p in written) == ["claude", "cursor"]
    assert all(p.read_text() == "# skill" for p in written)


def test_reinstall_keeps_a_backup(tmp_path):
    item = {"kind": "file", "filename": "notes.md", "install_path": "notes.md", "from": "Bob"}
    install(item, "v1", _config(tmp_path))
    install(item, "v2", _config(tmp_path))
    folder = tmp_path / "inbox" / "Bob"
    assert (folder / "notes.md").read_text() == "v2"
    assert [p.read_text() for p in folder.glob("notes.md.bak-*")] == ["v1"]


def test_install_never_escapes_target(tmp_path):
    item = {"kind": "file", "filename": "x", "install_path": "../../outside.txt", "from": "Eve"}
    with pytest.raises(ValueError):
        install(item, "boom", _config(tmp_path), project_dir=tmp_path / "project")
    assert not (tmp_path / "outside.txt").exists()


def test_hook_event_names_become_snake_case():
    assert _snake("afterMCPExecution") == "after_mcp_execution"
    assert _snake("beforeSubmitPrompt") == "before_submit_prompt"


def test_connect_cursor_merges_existing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DADDY_HOME", str(tmp_path / ".daddy"))
    monkeypatch.setattr(connect, "executable", lambda: "/opt/daddy")
    cursor = tmp_path / ".cursor"
    cursor.mkdir()
    (cursor / "mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    (cursor / "hooks.json").write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": "mine"}]}}))

    connect.connect_cursor(None, hooks=True)
    connect.connect_cursor(None, hooks=True)

    servers = json.loads((cursor / "mcp.json").read_text())["mcpServers"]
    assert servers["other"] == {"command": "x"}
    assert servers["daddy"] == {"command": "/opt/daddy", "args": ["mcp"]}
    hooks = json.loads((cursor / "hooks.json").read_text())["hooks"]
    assert [h["command"] for h in hooks["stop"]] == ["mine", "/opt/daddy hook audit"]
    assert hooks["beforeSubmitPrompt"] == [{"command": "/opt/daddy hook prompt-check"}]
