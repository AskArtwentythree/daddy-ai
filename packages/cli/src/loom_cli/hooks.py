"""Hook handlers for Cursor and Claude Code: the prompt guard, the agent guard and an activity reporter.

Each reads the agent's JSON event on stdin. The prompt guard blocks a prompt that contains a
credential before it reaches any model. The agent guard asks the gateway whether a shell command or
tool call may run (reads are decided on the laptop) and falls back to the same rules offline. The
reporter never blocks the agent. Every handler always prints a valid answer, because Cursor blocks
the action on malformed output.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
import time
from pathlib import Path

from loom_common import guard
from loom_common.secrets import scan_text

from .client import LoomClient, LoomError
from .config import Config, home_dir, load_config

RULES_TTL_SECONDS = 300
READ_LIMIT_BYTES = 1_000_000

OWN_TOOLS = frozenset(
    {
        "whoami",
        "ask_project",
        "search_project",
        "project_brief",
        "get_project_file",
        "send_to_teammate",
        "check_inbox",
        "accept_delivery",
        "decline_delivery",
        "save_note",
        "change_my_name",
    }
)


def _read_event() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {}


def _report(event: str, decision: str, reason: str | None, detail: dict, client_name: str = "cursor-hook") -> None:
    config = load_config()
    if not config.token:
        return
    with contextlib.suppress(LoomError):
        LoomClient(config, client_name=client_name, timeout=3).post(
            "/api/agent-events",
            {"event": event, "decision": decision, "reason": reason, "detail": detail},
        )


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower().replace("m_c_p", "mcp")


def prompt_check() -> int:
    event = _read_event()
    claude = event.get("hook_event_name") == "UserPromptSubmit"
    findings = scan_text(event.get("prompt", ""))
    if not findings:
        if not claude:
            print(json.dumps({"continue": True}))
        return 0
    reason = "; ".join(f.describe() for f in findings[:3])
    message = f"LoomAI blocked this prompt because it contains a secret ({reason}). Remove it and retry."
    _report(
        "prompt_blocked",
        "deny",
        reason,
        {"conversation": event.get("conversation_id"), "roots": event.get("workspace_roots")},
    )
    if claude:
        print(json.dumps({"decision": "block", "reason": message}))
    else:
        print(json.dumps({"continue": False, "user_message": message}))
    return 0  # Cursor only reads the JSON verdict when the hook exits 0


# ── Agent guard ───────────────────────────────────────────────────────────────


def _family_rules(config: Config) -> tuple[guard.Rule, ...]:
    """The family's own rules, cached for a few minutes; a stale copy is used when the gateway is unreachable."""
    cache = home_dir() / "guard-rules.json"
    try:
        cached = json.loads(cache.read_text())
    except (OSError, ValueError):
        cached = {}
    if config.token and time.time() - cached.get("at", 0) > RULES_TTL_SECONDS:
        with contextlib.suppress(LoomError):
            family = LoomClient(config, client_name="guard", timeout=3).get("/api/guard/rules")["family"]
            cached = {"at": time.time(), "family": family}
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(cached))
    return guard.rules_from(cached.get("family", []))


def _action(event: dict) -> tuple[guard.Kind, str, str, str] | None:
    """(kind, target, content, agent) for an event the guard decides on, None for everything else."""
    name = event.get("hook_event_name")
    if name == "beforeShellExecution":
        return "shell", event.get("command") or "", "", "cursor-hook"
    if name == "beforeReadFile":
        return "read", event.get("file_path") or "", event.get("content") or "", "cursor-hook"
    if name == "beforeMCPExecution":
        server = event.get("mcp_server_name") or "unknown"
        if server in ("loom", "daddy") or server.endswith(("-loom", "-daddy")):
            return None  # Loom's own tools are decided and audited by the gateway itself
        return "mcp", f"{server}.{event.get('tool_name') or ''}", event.get("tool_input") or "", "cursor-hook"
    if name == "PreToolUse":
        tool, args = event.get("tool_name") or "", event.get("tool_input") or {}
        if tool == "Bash":
            return "shell", args.get("command") or "", "", "claude-hook"
        if tool == "Read":
            path = Path(args.get("file_path") or "")
            content = ""
            with contextlib.suppress(OSError), path.open("rb") as handle:
                content = handle.read(READ_LIMIT_BYTES).decode(errors="ignore")
            return "read", str(path), content, "claude-hook"
        if tool.startswith("mcp__"):
            _, server, tool_name = (tool.split("__", 2) + ["", ""])[:3]
            if server in ("loom", "daddy"):
                return None
            return "mcp", f"{server}.{tool_name}", json.dumps(args), "claude-hook"
    return None


def _decide(kind: guard.Kind, target: str, content: str, cwd: str | None, agent: str) -> guard.Decision:
    config = load_config()
    shown = target[:500]
    if hard := guard.hard_check(kind, target, content):
        if hard.rule == "secret":
            shown = "(hidden: contains a secret)"
        _report("guard_blocked", "deny", hard.reason, {"kind": kind, "target": shown}, agent)
        return hard
    if kind == "read":  # reads are frequent and local, so they are decided here and only denials reported
        decision = guard.evaluate(kind, target, family_rules=_family_rules(config))
        if decision.verdict != "allow":
            _report("guard_blocked", "deny", decision.reason, {"kind": kind, "target": shown}, agent)
        return decision
    if config.token:
        with contextlib.suppress(LoomError, KeyError, ValueError):
            answer = LoomClient(config, client_name=agent, timeout=3).post(
                "/api/guard", {"kind": kind, "target": target[:4000], "cwd": cwd}
            )
            return guard.Decision(answer["verdict"], answer.get("reason"), answer.get("rule"))
    return guard.evaluate(kind, target, family_rules=_family_rules(config))  # offline: decided on the laptop


def _answer(event: dict, kind: guard.Kind, decision: guard.Decision) -> dict:
    verdict = decision.verdict
    if event.get("hook_event_name") == "PreToolUse":
        if verdict == "allow":
            return {}  # leave Claude Code's own permission prompts in charge
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": verdict,
                "permissionDecisionReason": f"LoomAI: {decision.reason}",
            }
        }
    if kind == "read" and verdict == "ask":
        verdict = "deny"  # Cursor can't ask before a read
    if verdict == "allow":
        return {"permission": "allow"}
    what = {"shell": "command", "read": "file read", "mcp": "tool call"}[kind]
    if verdict == "ask":
        return {
            "permission": "ask",
            "user_message": f"LoomAI wants your OK: {decision.reason}.",
            "agent_message": f"LoomAI's guard needs the user's approval for this {what}: {decision.reason}.",
        }
    return {
        "permission": "deny",
        "user_message": f"LoomAI blocked this {what}: {decision.reason}.",
        "agent_message": (
            f"LoomAI's guard blocked this {what}: {decision.reason}. This is the team's policy; don't try to work "
            "around it. If it's really needed, tell the user why and let them decide."
        ),
    }


def guard_check() -> int:
    """beforeShellExecution, beforeReadFile and beforeMCPExecution (Cursor), PreToolUse (Claude Code)."""
    event = _read_event()
    try:
        action = _action(event)
        if action is None:
            answer = {} if event.get("hook_event_name") == "PreToolUse" else {"permission": "allow"}
        else:
            kind, target, content, agent = action
            answer = _answer(event, kind, _decide(kind, target, content, event.get("cwd"), agent))
    except Exception:  # a bug here must not freeze the user's agent; the gateway still guards Loom's tools
        answer = {} if event.get("hook_event_name") == "PreToolUse" else {"permission": "allow"}
    print(json.dumps(answer))
    return 0


def audit() -> int:
    try:
        event = _read_event()
        name = event.get("hook_event_name")
        if not name:
            print("{}")
            return 0
        tool = event.get("tool_name") or ""
        server = event.get("mcp_server_name") or ""
        ours = any(own in server.lower() for own in ("loom", "daddy"))
        own_call = ours or (not server and any(tool.endswith(t) for t in OWN_TOOLS))
        if not own_call:  # Loom's own calls are audited server-side
            keys = ("mcp_server_name", "tool_name", "duration", "status", "loop_count")
            detail = {k: event[k] for k in keys if k in event}
            detail["conversation"] = event.get("conversation_id")
            _report(f"cursor.{_snake(name)}"[:40], "allow", None, detail)
    except Exception:  # never break the user's agent
        pass
    print("{}")
    return 0
