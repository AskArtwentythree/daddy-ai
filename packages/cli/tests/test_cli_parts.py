import json

import pytest

from loom_cli import connect, watch
from loom_cli.config import Config
from loom_cli.hooks import _snake
from loom_cli.installer import install
from loom_common.version import CLI_VERSION

STAMP = {"LOOM_CLI_VERSION": CLI_VERSION}


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


class _FakeInboxClient:
    def __init__(self, waiting, contents):
        self.waiting, self.contents, self.accepted = waiting, contents, []

    def get(self, path, **params):
        assert path in ("/api/inbox", "/api/join/requests")
        return self.waiting if path == "/api/inbox" else []

    def post(self, path):
        delivery_id = path.split("/")[3]
        self.accepted.append(delivery_id)
        item = next(i for i in self.waiting if i["id"] == delivery_id)
        return item | {"content": self.contents[delivery_id]}


def test_watch_opens_waiting_messages_and_files_without_accept(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "notify", lambda *args: None)
    base = {"note": "", "project": "app", "from": "Alice"}
    waiting = [  # newest first, as /api/inbox returns them
        base | {"id": "f1", "kind": "file", "filename": "plan.md", "install_path": "plan.md"},
        base | {"id": "m1", "kind": "message", "filename": "message.txt", "install_path": None},
    ]
    lines = []
    watcher = watch.Watcher(_config(tmp_path), lines.append)
    watcher.client = _FakeInboxClient(waiting, {"m1": "Hello world", "f1": "# Plan"})

    assert watcher.handle({"type": "hello", "user": "Bob", "device": "laptop"})

    assert watcher.client.accepted == ["m1", "f1"]
    assert "   Hello world" in lines
    assert (tmp_path / "inbox" / "Alice" / "plan.md").read_text() == "# Plan"


def test_hook_event_names_become_snake_case():
    assert _snake("afterMCPExecution") == "after_mcp_execution"
    assert _snake("beforeSubmitPrompt") == "before_submit_prompt"


def test_connect_claude_desktop_keeps_other_servers(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOOM_HOME", str(tmp_path / ".loom"))
    monkeypatch.setattr(connect, "executable", lambda: "/opt/loom")
    path = connect.claude_desktop_config()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))

    assert "claude-desktop" in connect.detect_agents()
    assert connect.connected_agents() == []
    assert connect.connect_claude_desktop()[1] is True
    assert connect.connect_claude_desktop()[1] is False

    config = json.loads(path.read_text())
    assert config["theme"] == "dark"
    loom = {"command": "/opt/loom", "args": ["mcp"], "env": STAMP}
    assert config["mcpServers"] == {"other": {"command": "x"}, "loom": loom}
    assert connect.connected_agents() == ["the Claude app"]
    monkeypatch.setenv("LOOM_HOME", str(tmp_path / ".loom-other"))
    assert connect.connected_agents() == []


def test_connect_cursor_merges_existing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOOM_HOME", str(tmp_path / ".loom"))
    monkeypatch.setattr(connect, "executable", lambda: "/opt/loom")
    cursor = tmp_path / ".cursor"
    cursor.mkdir()
    (cursor / "mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    (cursor / "hooks.json").write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": "mine"}]}}))

    connect.connect_cursor(None, hooks=True)
    connect.connect_cursor(None, hooks=True)

    servers = json.loads((cursor / "mcp.json").read_text())["mcpServers"]
    assert servers["other"] == {"command": "x"}
    assert servers["loom"] == {"command": "/opt/loom", "args": ["mcp"], "env": STAMP}
    hooks = json.loads((cursor / "hooks.json").read_text())["hooks"]
    assert [h["command"] for h in hooks["stop"]] == ["mine", "/opt/loom hook audit"]
    assert hooks["beforeSubmitPrompt"] == [{"command": "/opt/loom hook prompt-check"}]


def test_join_never_silently_replaces_a_working_team(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from loom_cli import main
    from loom_cli.client import LoomError
    from loom_cli.config import load_config, save_config

    monkeypatch.setenv("LOOM_HOME", str(tmp_path / ".loom"))
    save_config(Config(token="dad_old", user="Askar", family="acmhack", default_project="acmhack"))
    alive = {"ok": True}

    def get(self, path):
        if not alive["ok"]:
            raise LoomError(401, "revoked")
        return {"family": "acmhack", "user": "Askar"}

    monkeypatch.setattr(main.LoomClient, "get", get)
    monkeypatch.setattr(main.LoomClient, "post", lambda self, path, body: {
        "token": "dad_new", "user": "Askar", "family": "Test"})
    runner = CliRunner()

    refused = runner.invoke(main.app, ["join", "inv_x"])
    assert refused.exit_code == 1 and "connected to the 'acmhack' family" in refused.output
    assert "`loom join inv_x --switch`" in refused.output
    assert load_config().token == "dad_old"

    switched = runner.invoke(main.app, ["join", "inv_x", "--switch"])
    assert switched.exit_code == 0, switched.output
    config = load_config()
    assert (config.token, config.family, config.default_project) == ("dad_new", "Test", None)
    backups = list((tmp_path / ".loom").glob("config.backup-*.json"))
    assert len(backups) == 1 and json.loads(backups[0].read_text())["token"] == "dad_old"

    alive["ok"] = False
    assert runner.invoke(main.app, ["join", "inv_y"]).exit_code == 0


def test_join_in_a_terminal_asks_before_replacing_the_team(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from loom_cli import main
    from loom_cli.config import load_config, save_config

    monkeypatch.setenv("LOOM_HOME", str(tmp_path / ".loom"))
    save_config(Config(token="dad_old", user="Name", family="Test"))
    monkeypatch.setattr(main, "_can_ask", lambda: True)
    monkeypatch.setattr(main.LoomClient, "get", lambda self, path: {"family": "Test", "user": "Name"})
    monkeypatch.setattr(main.LoomClient, "post", lambda self, path, body: {
        "token": "dad_new", "user": "Will", "family": "acmhack"})
    runner = CliRunner()

    declined = runner.invoke(main.app, ["join", "inv_x"], input="n\n")
    assert declined.exit_code == 1 and "Kept 'Test'" in declined.output
    assert load_config().token == "dad_old"

    agreed = runner.invoke(main.app, ["join", "inv_x"], input="y\n")
    assert agreed.exit_code == 0 and "Welcome to the 'acmhack' family, Will!" in agreed.output
    assert load_config().token == "dad_new"
    assert len(list((tmp_path / ".loom").glob("config.backup-*.json"))) == 1


def test_join_with_a_team_link_waits_for_approval_and_can_resume(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from loom_cli import main
    from loom_cli.client import LoomError
    from loom_cli.config import load_config

    monkeypatch.setenv("LOOM_HOME", str(tmp_path / ".loom"))
    monkeypatch.setattr(main, "JOIN_POLL_SECONDS", 0)
    calls = []
    answers = iter([{"status": "pending"}, KeyboardInterrupt(), {"status": "pending"},
                    {"status": "joined", "token": "loom_new", "user": "Lee", "family": "Team"}])

    def post(self, path, body):
        calls.append(path)
        if path == "/api/join":
            return {"pending": True, "request": "r1", "secret": "s1", "family": "Team", "user": "Lee",
                    "approvers": ["Kim"]}
        answer = next(answers)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(main.LoomClient, "post", post)
    runner = CliRunner()
    first = runner.invoke(main.app, ["join", "inv_x", "--name", "Lee", "--email", "lee@example.com"])
    assert first.exit_code == 1 and "Waiting for Kim" in first.output and "loom join inv_x" in first.output
    assert load_config().token is None

    second = runner.invoke(main.app, ["join", "inv_x"])
    assert second.exit_code == 0, second.output
    assert calls.count("/api/join") == 1  # the second run kept waiting on the same request
    assert load_config().token == "loom_new"
    assert not (tmp_path / ".loom" / "join-request.json").exists()

    def denied(self, path, body):
        if path == "/api/join":
            return {"pending": True, "request": "r2", "secret": "s2", "family": "Team", "user": "Eve"}
        raise LoomError(403, "Kim declined your request to join", "denied")

    monkeypatch.setattr(main.LoomClient, "post", denied)
    monkeypatch.setattr(main.LoomClient, "get", lambda self, path: {"family": "Team", "user": "Lee"})
    refused = runner.invoke(main.app, ["join", "inv_y", "--switch", "--name", "Eve", "--email", "eve@x.com"])
    assert refused.exit_code == 1 and "declined" in refused.output
    assert not (tmp_path / ".loom" / "join-request.json").exists()


def test_photos_are_saved_byte_for_byte(tmp_path):
    photo = b"\x89PNG\r\n\x1a\n\x00\xff" * 100
    item = {"kind": "file", "filename": "cat.png", "from": "Pia"}
    [written] = install(item, photo, _config(tmp_path))
    assert written.read_bytes() == photo
    [again] = install(item, photo[:-1], _config(tmp_path))
    assert again.read_bytes() == photo[:-1]
    assert len(list(written.parent.glob("cat.png.bak-*"))) == 1


def test_version_numbers_agree():
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    assert tomllib.loads(pyproject.read_text())["project"]["version"] == CLI_VERSION


def test_update_notices_do_not_nag(tmp_path, monkeypatch):
    from loom_cli import updates

    monkeypatch.setenv("LOOM_HOME", str(tmp_path / ".loom"))
    assert updates.due("9.0.0", "cli") is True
    assert updates.due("9.0.0", "cli") is False
    assert updates.due("9.0.0", "agent") is True
    assert updates.due("9.1.0", "cli") is True
    assert "loom update" in updates.notice("9.1.0")


def test_already_connected_ignores_the_version_stamp(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOOM_HOME", str(tmp_path / ".loom"))
    monkeypatch.setattr(connect, "executable", lambda: "/opt/loom")
    cursor = tmp_path / ".cursor" / "mcp.json"
    cursor.parent.mkdir()
    old = {"command": "/opt/loom", "args": ["mcp"], "env": {"LOOM_CLI_VERSION": "0.0.1"}}
    cursor.write_text(json.dumps({"mcpServers": {"loom": old}}))
    assert connect.connected_agents() == ["Cursor"]
