import json

import pytest

from daddy_cli import connect, watch
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


def test_connect_claude_desktop_keeps_other_servers(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DADDY_HOME", str(tmp_path / ".daddy"))
    monkeypatch.setattr(connect, "executable", lambda: "/opt/daddy")
    path = connect.claude_desktop_config()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))

    assert "claude-desktop" in connect.detect_agents()
    connect.connect_claude_desktop()

    config = json.loads(path.read_text())
    assert config["theme"] == "dark"
    assert config["mcpServers"] == {"other": {"command": "x"}, "daddy": {"command": "/opt/daddy", "args": ["mcp"]}}


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


class _FakeInbox:
    def __init__(self, items: dict[str, dict]):
        self.items = items
        self.accepted: list[str] = []

    def get(self, path, **params):
        assert path == "/api/inbox"
        return [i for i in reversed(self.items.values()) if i["id"] not in self.accepted]

    def post(self, path):
        delivery_id = path.split("/")[3]
        self.accepted.append(delivery_id)
        return self.items[delivery_id]


def test_watch_opens_messages_and_files_without_accept(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "notify", lambda *a: None)
    item = {"project": "app", "from": "Patrick", "note": "", "install_path": None}
    inbox = _FakeInbox(
        {
            "1": item | {"id": "1", "kind": "message", "filename": "message.txt", "content": "Hello world"},
            "2": item | {"id": "2", "kind": "file", "filename": "plan.md", "content": "# Plan"},
        }
    )
    lines: list[str] = []
    watcher = watch.Watcher(_config(tmp_path), lines.append)
    watcher.client = inbox

    watcher.handle({"type": "hello", "user": "Sri", "device": "mac"})
    watcher.handle({"type": "hello", "user": "Sri", "device": "mac"})

    assert inbox.accepted == ["1", "2"]
    assert "💬 Patrick [app]: Hello world" in lines
    assert (tmp_path / "inbox" / "Patrick" / "plan.md").read_text() == "# Plan"
