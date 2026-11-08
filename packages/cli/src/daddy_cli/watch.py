"""``daddy watch``: live pushes from Daddy AI, desktop notifications, automatic skill installs."""

from __future__ import annotations

import asyncio
import json
import platform
import subprocess
from collections.abc import Callable

import websockets

from .client import DaddyClient, DaddyError
from .config import Config
from .installer import install


def notify(title: str, message: str) -> None:
    if platform.system() != "Darwin":
        return
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


AUDIT_DETAIL_KEYS = (
    "to", "filename", "question", "query", "path", "title", "repo", "email", "mcp_server_name", "tool_name",
)


def describe_audit(event: dict) -> str:
    """One line per audit event, from either a live push or a row of ``/api/audit``."""
    who = event.get("actor")
    if who is None:
        who = event.get("user") or "system"
        if event.get("device"):
            who += f" ({event['device']}" + (f" via {event['client']})" if event.get("client") else ")")
    mark = {"allow": "✓", "deny": "✗", "error": "!"}.get(event["decision"], "?")
    line = f"{str(event.get('at', ''))[11:19]} {mark} {event['action']:<22} {who}"
    if event.get("project"):
        line += f" [{event['project']}]"
    detail = event.get("detail") or {}
    shown = [f"{k}={str(detail[k])[:60]}" for k in AUDIT_DETAIL_KEYS if detail.get(k)]
    if shown:
        line += " " + " ".join(shown[:3])
    if event.get("reason"):
        line += f": {event['reason']}"
    return line


class Watcher:
    def __init__(self, config: Config, echo: Callable[[str], None], feed: bool = False):
        self.config = config
        self.echo = echo
        self.feed = feed
        self.client = DaddyClient(config, client_name="daddy-watch")

    def _auto_install(self, item: dict) -> None:
        try:
            accepted = self.client.post(f"/api/inbox/{item['id']}/accept")
        except DaddyError as error:
            self.echo(f"  could not auto-accept: {error.reason}")
            return
        for path in install(accepted, accepted["content"], self.config):
            self.echo(f"  installed → {path}")
        notify("Daddy AI", f"Installed skill '{accepted['filename']}' from {accepted['from']}")

    def handle(self, event: dict) -> bool:
        """Process one pushed event; returns False when watching should stop."""
        kind = event.get("type")
        if kind == "hello":
            self.echo(f"Watching as {event['user']} on {event['device']}. Ctrl+C to stop.")
        elif kind == "delivery.new":
            item = event["delivery"]
            self.echo(
                f"📬 {item['from']} sent {item['kind']} '{item['filename']}' "
                f"(project {item['project']}) id={item['id']}" + (f"\n   note: {item['note']}" if item["note"] else "")
            )
            if item["kind"] == "skill" and self.config.auto_install_skills:
                self._auto_install(item)
            else:
                notify("Daddy AI", f"{item['from']} sent you {item['filename']}")
                self.echo(f"   accept with: daddy accept {item['id']}")
        elif kind in ("delivery.accepted", "delivery.declined"):
            verb = kind.split(".")[1]
            self.echo(f"↩ {event['by']} {verb} '{event['delivery']['filename']}'")
            notify("Daddy AI", f"{event['by']} {verb} {event['delivery']['filename']}")
        elif kind == "ingest.done":
            report = event["report"]
            self.echo(
                f"📚 {event['repo']} indexed into {event['project']}: "
                f"{report['indexed']} new/changed, {report['unchanged']} unchanged, "
                f"{report['chunks']} chunks, {report['skipped']} skipped"
            )
            notify("Daddy AI", f"{event['repo']} is ready in {event['project']}")
        elif kind == "ingest.failed":
            self.echo(f"⚠ {event['repo']} import failed: {event['reason']}")
        elif kind == "device.revoked":
            self.echo(f"⛔ This device ({event['device']}) was revoked by an admin.")
            notify("Daddy AI", "This device was disconnected by an admin")
            return False
        elif kind == "audit" and self.feed:
            self.echo(describe_audit(event["event"]))
        return True

    async def run(self) -> None:
        delay = 1
        while True:
            try:
                async with websockets.connect(self.client.websocket_url(), ping_interval=None) as ws:
                    delay = 1
                    async for raw in ws:
                        event = json.loads(raw)
                        keep_going = await asyncio.to_thread(self.handle, event)
                        if not keep_going:
                            return
            except websockets.InvalidStatus as error:
                self.echo(f"Daddy AI rejected the connection ({error.response.status_code}).")
                return
            except websockets.ConnectionClosedError as error:
                if error.rcvd and error.rcvd.code == 4401:
                    self.echo("This device's token is no longer valid. Run `daddy join` again.")
                    return
                self.echo(f"Connection lost, retrying in {delay}s…")
            except OSError:
                self.echo(f"Daddy AI unreachable at {self.config.server_url}, retrying in {delay}s…")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
