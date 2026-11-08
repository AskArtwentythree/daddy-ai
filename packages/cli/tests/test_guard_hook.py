import io
import json

from loom_cli import connect, hooks
from loom_common.guard import Decision


def _run_guard(monkeypatch, tmp_path, event: dict) -> dict:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path / ".loom"))  # not connected: decided on the laptop
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert hooks.guard_check() == 0
    return json.loads(out.getvalue())


def test_cursor_events_become_guard_actions():
    assert hooks._action({"hook_event_name": "beforeShellExecution", "command": "ls"}) == (
        "shell", "ls", "", "cursor-hook")
    read = {"hook_event_name": "beforeReadFile", "file_path": "/p/a.py", "content": "x = 1"}
    assert hooks._action(read) == ("read", "/p/a.py", "x = 1", "cursor-hook")
    tool = {"hook_event_name": "beforeMCPExecution", "mcp_server_name": "linear", "tool_name": "delete_issue",
            "tool_input": "{}"}
    assert hooks._action(tool) == ("mcp", "linear.delete_issue", "{}", "cursor-hook")
    assert hooks._action({**tool, "mcp_server_name": "loom"}) is None
    assert hooks._action({"hook_event_name": "stop"}) is None


def test_claude_code_events_become_guard_actions(tmp_path):
    secret_file = tmp_path / "notes.md"
    secret_file.write_text("hello")
    bash = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git status"}}
    assert hooks._action(bash) == ("shell", "git status", "", "claude-hook")
    read = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": str(secret_file)}}
    assert hooks._action(read) == ("read", str(secret_file), "hello", "claude-hook")
    tool = {"hook_event_name": "PreToolUse", "tool_name": "mcp__github__delete_repo", "tool_input": {"r": 1}}
    assert hooks._action(tool) == ("mcp", "github.delete_repo", '{"r": 1}', "claude-hook")
    assert hooks._action({**tool, "tool_name": "mcp__loom__ask_project"}) is None


def test_answers_match_each_agents_format():
    cursor_shell = {"hook_event_name": "beforeShellExecution"}
    assert hooks._answer(cursor_shell, "shell", Decision("allow")) == {"permission": "allow"}
    denied = hooks._answer(cursor_shell, "shell", Decision("deny", "Overwrites a disk"))
    assert denied["permission"] == "deny" and "Overwrites a disk" in denied["agent_message"]
    assert hooks._answer(cursor_shell, "shell", Decision("ask", "Runs as admin"))["permission"] == "ask"
    assert hooks._answer({"hook_event_name": "beforeReadFile"}, "read", Decision("ask", "x"))["permission"] == "deny"

    claude = {"hook_event_name": "PreToolUse"}
    assert hooks._answer(claude, "shell", Decision("allow")) == {}
    out = hooks._answer(claude, "shell", Decision("deny", "Overwrites a disk"))["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and out["hookEventName"] == "PreToolUse"


def test_guard_works_offline_with_the_built_in_rules(monkeypatch, tmp_path):
    wipe = _run_guard(monkeypatch, tmp_path, {"hook_event_name": "beforeShellExecution", "command": "rm -rf ~"})
    assert wipe["permission"] == "deny"
    fine = _run_guard(monkeypatch, tmp_path, {"hook_event_name": "beforeShellExecution", "command": "pytest -q"})
    assert fine == {"permission": "allow"}
    env_file = _run_guard(monkeypatch, tmp_path, {"hook_event_name": "beforeReadFile", "file_path": "/p/.env"})
    assert env_file["permission"] == "deny"
    claude = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "cat .env"}}
    assert _run_guard(monkeypatch, tmp_path, claude)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert _run_guard(monkeypatch, tmp_path, {"not": "an event"}) == {"permission": "allow"}


def test_hooks_are_installed_next_to_the_users_own(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOOM_HOME", str(tmp_path / ".loom"))
    monkeypatch.setattr(connect, "executable", lambda: "/opt/loom")
    cursor = tmp_path / "hooks.json"
    cursor.write_text(json.dumps({"version": 1, "hooks": {"beforeShellExecution": [{"command": "mine"}]}}))
    connect.install_cursor_hooks(cursor)
    connect.install_cursor_hooks(cursor)
    shell = json.loads(cursor.read_text())["hooks"]["beforeShellExecution"]
    assert shell == [{"command": "mine"}, {"command": "/opt/loom hook guard", "timeout": 10}]

    claude = tmp_path / "settings.json"
    mine = {"matcher": "Write", "hooks": [{"type": "command", "command": "my-linter"}]}
    claude.write_text(json.dumps({"model": "opus", "hooks": {"PreToolUse": [mine]}}))
    connect.install_claude_code_hooks(claude)
    connect.install_claude_code_hooks(claude)
    settings = json.loads(claude.read_text())
    assert settings["model"] == "opus"
    assert settings["hooks"]["PreToolUse"] == [
        mine,
        {"matcher": "Bash|Read|mcp__.*", "hooks": [{"type": "command", "command": "/opt/loom hook guard",
                                                     "timeout": 10}]},
    ]
    assert len(settings["hooks"]["UserPromptSubmit"]) == 1
