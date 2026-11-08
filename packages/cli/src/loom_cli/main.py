"""``loom``: the family member's side of LoomAI."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import platform
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import typer

from loom_common import guard
from loom_common.files import MAX_DELIVERY_BYTES, describe_size, from_payload, to_payload
from loom_common.ignore import skip_reason
from loom_common.secrets import is_sensitive_path, scan_text
from loom_common.version import CLI_VERSION, is_older

from . import connect as connectors
from . import render, updates
from .client import LoomClient, LoomError
from .config import HOSTED_SERVER, Config, config_path, load_config, save_config
from .installer import install

app = typer.Typer(help="LoomAI: your team's shared project brain.", no_args_is_help=True)
project_app = typer.Typer(help="Create projects and manage who is in them.", no_args_is_help=True)
hook_app = typer.Typer(help="Handlers called by Cursor and Claude Code hooks (not for direct use).")
guard_app = typer.Typer(help="The agent guard: what agents may run, read and call.", no_args_is_help=True)
app.add_typer(project_app, name="project")
app.add_typer(hook_app, name="hook")
app.add_typer(guard_app, name="guard")

ProjectOpt = Annotated[str | None, typer.Option("--project", "-p", help="Project slug")]
BATCH_FILES = 150
BATCH_BYTES = 3_000_000
INSTALL_SCRIPT_URL = "https://raw.githubusercontent.com/AskArtwentythree/loom-ai/main/install.sh"


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"loom {CLI_VERSION}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool, typer.Option("--version", callback=_show_version, is_eager=True, help="Show the version")
    ] = False,
) -> None:
    pass


def _tell_about_update(latest: str) -> None:
    if updates.due(latest, "cli"):
        typer.secho(updates.notice(latest), fg="yellow", err=True)


def _client(config: Config | None = None, timeout: float = 120) -> LoomClient:
    config = config or load_config()
    if not config.token:
        _fail("Not connected yet. Run `loom init` (first person) or `loom join <code>`.")
    return LoomClient(config, client_name="loom-cli", timeout=timeout, on_update=_tell_about_update)


def _fail(message: str) -> None:
    typer.secho(message, fg="red", err=True)
    raise typer.Exit(1)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LoomError as error:
        _fail(("Denied: " if error.denied else "Error: ") + error.reason)


def _keep_current_connection(config: Config, switch: bool, again: str) -> None:
    """Refuse to overwrite a working connection unless --switch is given; always back the old one up."""
    if not config.token:
        return
    try:
        me = LoomClient(config, timeout=10).get("/api/me")
    except LoomError:
        me = None  # revoked or unreachable: replacing it is the way back in, but keep a copy anyway
    if me and not switch:
        _fail(
            f"This computer is already connected to the '{me['family']}' family as {me['user']}, and joining "
            "replaces that connection.\n"
            f"To be in both, give the new team its own folder:  LOOM_HOME=~/.loom-<team> {again}\n"
            f"To replace it anyway:  {again} --switch   (the current connection is backed up first)"
        )
    backup = config_path().with_name(f"config.backup-{datetime.now():%Y%m%d-%H%M%S}.json")
    backup.write_text(config_path().read_text())
    backup.chmod(0o600)
    typer.echo(f"Backed up the previous connection to {backup}")


def _device_name() -> str:
    return f"{os.environ.get('USER', 'me')}'s {platform.node().split('.')[0] or 'computer'}"


# ── Joining the family ────────────────────────────────────────────────────────


@app.command()
def init(
    family: Annotated[str, typer.Option(prompt="Family (team) name")],
    name: Annotated[str, typer.Option(prompt="Your name")],
    email: Annotated[str, typer.Option(prompt="Your email")],
    server: Annotated[str, typer.Option(help="LoomAI URL (default: the hosted LoomAI)")] = HOSTED_SERVER,
    device: Annotated[str | None, typer.Option(help="Name for this computer")] = None,
    secret: Annotated[str | None, typer.Option(help="Bootstrap secret, for self-hosted servers")] = None,
    switch: Annotated[bool, typer.Option("--switch", help="Replace this computer's current team")] = False,
) -> None:
    """Start a new family (your team) and become its admin."""
    config = load_config()
    _keep_current_connection(config, switch, "loom init")
    config.server_url = server.rstrip("/")
    config.default_project = None
    result = _call(
        LoomClient(config).post,
        "/api/bootstrap",
        {
            "family_name": family,
            "email": email,
            "display_name": name,
            "device_name": device or _device_name(),
            "secret": secret,
        },
    )
    config.token, config.user, config.family = result["token"], result["user"], result["family"]
    save_config(config)
    typer.secho(f"Welcome, {name}! You are the admin of the '{family}' family.", fg="green")
    typer.echo("Next: `loom project create <name>`, `loom ingest .`, then `loom invite -p <project>`.")


@app.command()
def join(
    code: str,
    server: Annotated[str, typer.Option(help="LoomAI URL (default: the hosted LoomAI)")] = HOSTED_SERVER,
    device: Annotated[str | None, typer.Option(help="Name for this computer")] = None,
    name: Annotated[str | None, typer.Option(envvar="LOOM_NAME", help="Your name (team links ask for it)")] = None,
    email: Annotated[str | None, typer.Option(envvar="LOOM_EMAIL", help="Your email (team links ask)")] = None,
    switch: Annotated[bool, typer.Option("--switch", help="Replace this computer's current team")] = False,
) -> None:
    """Join a family with the invite code or team link an admin gave you."""
    config = load_config()
    _keep_current_connection(config, switch, f"loom join {code}")
    config.server_url = server.rstrip("/")
    client = LoomClient(config)
    body = {"code": code, "device_name": device or _device_name(), "display_name": name, "email": email}
    try:
        result = client.post("/api/join", body)
    except LoomError as error:
        if "needs your name and email" not in error.reason:
            _fail(("Denied: " if error.denied else "Error: ") + error.reason)
        typer.echo("This is a team link, so tell the team who you are.")
        body["display_name"] = name or typer.prompt("Your name")
        body["email"] = email or typer.prompt("Your email")
        result = _call(client.post, "/api/join", body)
    if result["family"] != config.family:
        config.default_project = None
    config.token, config.user, config.family = result["token"], result["user"], result["family"]
    save_config(config)
    typer.secho(f"Welcome to the '{result['family']}' family, {result['user']}!", fg="green")
    _next_steps()


def _next_steps() -> None:
    try:
        ready = connectors.connected_agents()
    except RuntimeError:
        ready = []
    if ready:
        typer.echo(f"{' and '.join(ready)} already use Loom on this computer, so no restart is needed.")
        typer.echo("Just ask your agent: “Loom, who am I?”")
    else:
        typer.echo("Next: `loom connect all`, then ask your agent: “Loom, who am I?”")


@app.command()
def status() -> None:
    """Show who you are, your projects, your inbox and which agents are connected."""
    config = load_config()
    client = _client(config)
    typer.echo(render.status(_call(client.get, "/api/me"), config.server_url))
    for scope, folder in (("global", Path.home()), ("this folder", Path.cwd())):
        cursor_mcp = folder / ".cursor" / "mcp.json"
        connected = cursor_mcp.exists() and '"loom"' in cursor_mcp.read_text()
        typer.echo(f"Cursor ({scope}): {'connected' if connected else 'not connected'}")
    typer.echo(f"Config: {config_path()}")
    if client.latest_cli and is_older(CLI_VERSION, client.latest_cli):
        typer.secho(f"loom {CLI_VERSION}, update available ({client.latest_cli}): run `loom update`", fg="yellow")
    else:
        typer.echo(f"loom {CLI_VERSION} (up to date)" if client.latest_cli else f"loom {CLI_VERSION}")


@app.command()
def rename(name: str) -> None:
    """Change the name teammates see and send to."""
    result = _call(_client().post, "/api/me/name", {"display_name": name})
    config = load_config()
    config.user = result["user"]
    save_config(config)
    typer.secho(f"You are now '{result['user']}' (was '{result['previous']}').", fg="green")


@app.command()
def logout() -> None:
    """Forget this computer's token."""
    config = load_config()
    config.token = None
    save_config(config)
    typer.echo("Logged out on this computer.")


@app.command()
def invite(
    email: Annotated[str | None, typer.Argument(help="Leave out to get one team link for a group chat")] = None,
    name: Annotated[str | None, typer.Option("--name", "-n", help="Their display name")] = None,
    project: ProjectOpt = None,
    role: Annotated[str, typer.Option(help="owner, member or viewer")] = "member",
    uses: Annotated[int, typer.Option(help="How many people a team link lets in")] = 20,
) -> None:
    """Invite people: `loom invite -p my-app` gives one team link; `loom invite sri@x.com -n Sri` a personal one."""
    if email and not name:
        name = email.split("@")[0].replace(".", " ").title()
    result = _call(
        _client().post,
        "/api/invites",
        {"email": email, "display_name": name, "project": project, "role": role, "max_uses": uses},
    )
    server = load_config().server_url
    days = result["expires_in_days"]
    target = f"the '{project}' project" if project else "your team"
    if email:
        typer.secho(f"Invite for {name} (valid {days} days). Send them this line privately:", fg="green")
    else:
        typer.secho(
            f"Team link to {target}: up to {result['max_uses']} people, valid {days} days. "
            "Paste it in your team chat; each person types their own name and email when they run it:",
            fg="green",
        )
    where = "" if server == HOSTED_SERVER else f" {server}"
    typer.echo(f"  curl -fsSL {INSTALL_SCRIPT_URL} | sh -s -- {result['code']}{where}")
    typer.echo("If they already have the `loom` command:")
    typer.echo(f"  loom join {result['code']}{' --server' + where if where else ''} && loom connect all")
    if urlparse(server).hostname in ("127.0.0.1", "localhost", "0.0.0.0", "::1"):
        typer.secho(
            f"Warning: you are connected to a LoomAI server on this computer ({server}), so nobody else can use "
            "this invite. To move to the hosted LoomAI, run `loom logout`, then the install line from the README.",
            fg="yellow",
        )
    if not email:
        typer.echo(f"Stop it working early: loom cancel-invite {result['code']}")


@app.command("cancel-invite")
def cancel_invite(code: str) -> None:
    """Stop an invite or team link from working; people who already joined stay."""
    result = _call(_client().post, "/api/invites/cancel", {"code": code})
    typer.echo(f"Cancelled. {result['used_by']} people had joined with it.")


# ── Projects ──────────────────────────────────────────────────────────────────


@project_app.command("create")
def project_create(
    name: str,
    slug: Annotated[str | None, typer.Option()] = None,
    description: Annotated[str, typer.Option()] = "",
) -> None:
    """Create a project; you become its owner."""
    result = _call(_client().post, "/api/projects", {"name": name, "slug": slug, "description": description})
    config = load_config()
    if not config.default_project:
        config.default_project = result["slug"]
        save_config(config)
    typer.secho(f"Created project '{result['slug']}'.", fg="green")


@project_app.command("list")
def project_list() -> None:
    """List the projects you belong to."""
    projects = _call(_client().get, "/api/projects")
    default = load_config().default_project
    for p in projects:
        mark = "*" if p["slug"] == default else " "
        typer.echo(f"{mark} {p['slug']:<20} {p['role']:<7} {p['members']} member(s), {p['documents']} file(s)")
    if not projects:
        typer.echo("No projects yet.")


@project_app.command("use")
def project_use(slug: str) -> None:
    """Make a project the default for commands and agents on this computer."""
    config = load_config()
    config.default_project = slug
    save_config(config)
    typer.echo(f"Default project: {slug}")


@project_app.command("add-member")
def project_add_member(
    slug: str,
    email: str,
    role: Annotated[str, typer.Option(help="owner, member or viewer")] = "member",
) -> None:
    """Give an existing family member access to a project."""
    result = _call(_client().post, f"/api/projects/{slug}/members", {"email": email, "role": role})
    typer.echo(f"{result['member']} is now {result['role']} of {slug}.")


@project_app.command("remove-member")
def project_remove_member(slug: str, email: str) -> None:
    """Remove someone's access to a project; their agents lose it immediately."""
    result = _call(_client().delete, f"/api/projects/{slug}/members/{email}")
    typer.echo(f"Removed {result['removed']} from {slug}.")


# ── Knowledge ─────────────────────────────────────────────────────────────────


def _list_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout.decode()
        return [root / p for p in out.split("\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [p for p in root.rglob("*") if p.is_file()]


@app.command()
def ingest(
    path: Annotated[Path, typer.Argument(help="Folder or file to add")] = Path("."),
    project: ProjectOpt = None,
    prune: Annotated[bool, typer.Option(
        "--prune", help="Also forget files added from this folder before that no longer exist in it")] = False,
) -> None:
    """Add a local folder's code and docs to a project (secrets never leave this computer)."""
    client = _client(timeout=600)
    slug = _call(client.resolve_project, project)
    root = path.resolve()
    candidates = [root] if root.is_file() else _list_files(root)
    base = root.parent if root.is_file() else root

    files, local_skips = [], []
    for file in candidates:
        rel = file.relative_to(base).as_posix()
        if not file.is_file():
            continue
        if reason := skip_reason(rel, file.stat().st_size):
            local_skips.append((rel, reason))
            continue
        try:
            content = file.read_text()
        except UnicodeDecodeError:
            local_skips.append((rel, "not text"))
            continue
        if not content.strip():
            local_skips.append((rel, "empty file"))
            continue
        if findings := scan_text(content):
            local_skips.append((rel, "contains secrets: " + findings[0].describe()))
            continue
        files.append({"path": rel, "content": content})

    batches, batch, size = [], [], 0
    for item in files:
        if batch and (len(batch) >= BATCH_FILES or size + len(item["content"]) > BATCH_BYTES):
            batches.append(batch)
            batch, size = [], 0
        batch.append(item)
        size += len(item["content"])
    if batch:
        batches.append(batch)

    totals = {"indexed": 0, "unchanged": 0, "chunks": 0}
    server_skips = []
    with typer.progressbar(batches, label=f"Uploading to {slug}") as progress:
        for i, chunk in enumerate(progress):
            report = _call(
                client.post,
                f"/api/projects/{slug}/documents",
                {
                    "files": chunk,
                    "source": f"local:{root.name}",
                    "refresh_brief": i == len(batches) - 1,
                },
            )
            for key in totals:
                totals[key] += report[key]
            server_skips += report["skipped_files"]
    typer.secho(
        f"{totals['indexed']} file(s) indexed, {totals['unchanged']} unchanged, "
        f"{totals['chunks']} chunk(s) into '{slug}'.",
        fg="green",
    )
    secret_skips = [s for s in local_skips if s[1].startswith(("contains secrets", "credential"))]
    if secret_skips:
        typer.secho(f"Kept on this computer ({len(secret_skips)}):", fg="yellow")
        for rel, reason in secret_skips:
            typer.echo(f"  {rel}: {reason}")
    for skip in server_skips:
        typer.echo(f"  skipped by server: {skip['path']}: {skip['reason']}")
    typer.echo(f"Skipped locally (empty, binary, lock, ignored): {len(local_skips) - len(secret_skips)}")
    if prune and files:
        keep = [item["path"] for item in files]
        removed = _call(client.post, f"/api/projects/{slug}/documents/prune",
                        {"source": f"local:{root.name}", "keep": keep})["removed"]
        typer.secho(f"Forgot {len(removed)} file(s) no longer in {root.name}.", fg="green")
        for path in removed[:10]:
            typer.echo(f"  {path}")
        if len(removed) > 10:
            typer.echo(f"  … and {len(removed) - 10} more")


@app.command()
def github(
    repo: Annotated[str, typer.Argument(help="owner/name")],
    project: ProjectOpt = None,
    ref: Annotated[str | None, typer.Option(help="Branch, tag or commit")] = None,
) -> None:
    """Import a GitHub repository into a project (runs on the server; `loom watch` shows progress)."""
    client = _client()
    slug = _call(client.resolve_project, project)
    _call(
        client.post,
        f"/api/projects/{slug}/github",
        {"repo": repo, "ref": ref, "token": os.environ.get("GITHUB_TOKEN")},
    )
    typer.echo(f"Importing {repo} into {slug} in the background.")


@app.command()
def ask(
    question: str,
    project: ProjectOpt = None,
    path: Annotated[str | None, typer.Option(help="Restrict to a folder")] = None,
) -> None:
    """Ask LoomAI about your projects; answers cite their sources."""
    result = _call(
        _client().post,
        "/api/ask",
        {"query": question, "project": project, "path_prefix": path, "session_key": "cli"},
    )
    typer.echo(render.answer(result | {"question": question}))


@app.command()
def search(
    query: str,
    project: ProjectOpt = None,
    path: Annotated[str | None, typer.Option(help="Restrict to a folder")] = None,
) -> None:
    """Show the raw passages that match a query."""
    result = _call(_client().post, "/api/search", {"query": query, "project": project, "path_prefix": path})
    typer.echo(render.hits(result))


@app.command()
def brief(project: ProjectOpt = None) -> None:
    """Print a project's onboarding brief."""
    client = _client()
    slug = _call(client.resolve_project, project)
    typer.echo(_call(client.get, f"/api/projects/{slug}/brief")["brief"])


@app.command()
def note(
    title: str,
    text: Annotated[str | None, typer.Argument(help="Note text (or use --file)")] = None,
    file: Annotated[Path | None, typer.Option(help="Read the note from a file")] = None,
    project: ProjectOpt = None,
) -> None:
    """Save a decision or explanation to a project's knowledge."""
    content = file.read_text() if file else text
    if not content:
        _fail("Give the note text or --file.")
    client = _client()
    slug = _call(client.resolve_project, project)
    result = _call(client.post, f"/api/projects/{slug}/notes", {"title": title, "content": content})
    typer.echo(f"Saved as {result['path']} in {slug}.")


# ── Delivery ──────────────────────────────────────────────────────────────────


@app.command()
def send(
    to: Annotated[str, typer.Argument(help="Teammate name or email")],
    path: Annotated[Path | None, typer.Argument(help="File to send")] = None,
    message: Annotated[str | None, typer.Option("--message", "-m", help="Send a text message")] = None,
    kind: Annotated[str | None, typer.Option(help="file, skill or message")] = None,
    note: Annotated[str, typer.Option(help="Note for the recipient")] = "",
    project: ProjectOpt = None,
) -> None:
    """Send a file (any kind: code, photos, PDFs…), an agent skill (SKILL.md) or a message to a teammate."""
    if path is None and message is None:
        _fail("Give a file path or --message.")
    if path is not None:
        if not path.is_file():
            _fail(f"No such file: {path}")
        if (size := path.stat().st_size) > MAX_DELIVERY_BYTES:
            _fail(f"{path.name} is {describe_size(size)}; the limit is {describe_size(MAX_DELIVERY_BYTES)}.")
        payload = to_payload(path.read_bytes())
        kind = kind or ("skill" if path.name == "SKILL.md" else "file")
        if "content_base64" in payload and kind != "file":
            _fail(f"{path.name} is not a text file, so it can only be sent as a file.")
        filename = f"{path.resolve().parent.name}/{path.name}" if kind == "skill" else path.name
    else:
        payload, kind, filename = {"content": message}, "message", "message.txt"
    findings = scan_text(payload.get("content", "")) + scan_text(note)
    if is_sensitive_path(filename) or findings:
        reason = "credential file" if not findings else "; ".join(f.describe() for f in findings[:3])
        with contextlib.suppress(LoomError):
            _client().post(
                "/api/agent-events",
                {
                    "event": "share_blocked",
                    "decision": "deny",
                    "reason": reason,
                    "detail": {"to": to, "filename": filename, "where": "local"},
                },
            )
        _fail(f"Blocked on this computer, nothing was sent: {reason}")
    client = _client()
    result = _call(
        client.post,
        "/api/send",
        {
            "project": _call(client.resolve_project, project),
            "to": to,
            "filename": filename,
            **payload,
            "kind": kind,
            "note": note,
        },
    )
    sent = result["delivery"]
    typer.secho(
        f"Sent {kind} '{sent['filename']}' ({describe_size(sent['size_bytes'])}) to {result['recipient']}.",
        fg="green",
    )


@app.command()
def inbox(
    all: Annotated[bool, typer.Option("--all", help="Include handled items")] = False,
) -> None:
    """List what teammates have sent you."""
    typer.echo(render.inbox(_call(_client().get, "/api/inbox", all=str(all).lower())))


@app.command()
def accept(
    delivery_id: str,
    to: Annotated[Path | None, typer.Option(help="Folder to place a file in")] = None,
) -> None:
    """Accept an inbox item and install it (skills go to Cursor and Claude Code)."""
    config = load_config()
    item = _call(_client(config).post, f"/api/inbox/{delivery_id}/accept")
    if item["kind"] == "message":
        typer.echo(f"Message from {item['from']}:\n{item['content']}")
        return
    for written in install(item, from_payload(item), config, to):
        typer.echo(f"Installed → {written}")


@app.command()
def decline(delivery_id: str) -> None:
    """Decline an inbox item."""
    item = _call(_client().post, f"/api/inbox/{delivery_id}/decline")
    typer.echo(f"Declined '{item['filename']}' from {item['from']}.")


@app.command()
def watch(
    feed: Annotated[bool, typer.Option(help="Also stream the family's audit log (admins)")] = False,
    window: Annotated[bool, typer.Option("--window", help="Run it in a new terminal window")] = False,
) -> None:
    """Stay online: messages, files and skills from teammates show up and open as they arrive."""
    from .watch import Watcher, open_watch_window, watch_running

    config = load_config()
    _client(config)
    if window:
        if watch_running():
            typer.echo("`loom watch` is already running in another window.")
        elif open_watch_window(connectors.executable(), connectors.custom_home()):
            typer.echo("Opened `loom watch` in a new terminal window.")
        else:
            typer.echo("No terminal app found; run `loom watch` in a new terminal.")
        return
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(Watcher(config, typer.echo, feed=feed).run())


# ── Governance ────────────────────────────────────────────────────────────────


@app.command()
def audit(
    limit: Annotated[int, typer.Option(help="How many recent events")] = 30,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Keep streaming new events")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show what every agent in the family did, and what was denied (admins)."""
    from .watch import describe_audit

    events = _call(_client().get, "/api/audit", limit=limit)
    for event in events:
        typer.echo(json.dumps(event, default=str) if as_json else describe_audit(event))
    if follow:
        watch(feed=True)


@app.command()
def dashboard(
    no_open: Annotated[bool, typer.Option("--no-open", help="Only print the link")] = False,
) -> None:
    """Open the family dashboard in your browser (one-time link, valid for two minutes)."""
    config = load_config()
    link = _call(_client(config).post, "/api/dashboard/link")
    url = config.server_url + link["path"]
    if not no_open:
        webbrowser.open(url)
    typer.echo(f"Dashboard: {url}")


@app.command()
def devices() -> None:
    """List connected computers (all of them for admins)."""
    for d in _call(_client().get, "/api/devices"):
        state = "revoked" if d["revoked_at"] else f"last seen {str(d['last_seen_at'])[:16]}"
        client = f" via {d['last_client']}" if d["last_client"] else ""
        typer.echo(f"{d['id']}  {d['user']:<12} {d['name']:<24} {state}{client}")


@app.command()
def revoke(device_id: str) -> None:
    """Disconnect a computer; its agents lose access immediately."""
    result = _call(_client().post, f"/api/devices/{device_id}/revoke")
    typer.echo(f"Revoked {result['revoked']} ({result['user']}).")


# ── Agents ────────────────────────────────────────────────────────────────────


@app.command()
def connect(
    agent: Annotated[str, typer.Argument(help="cursor, claude (Claude Code), claude-desktop, or all")],
    project_dir: Annotated[Path | None, typer.Option(help="Connect only this project folder")] = None,
    hooks: Annotated[bool, typer.Option(help="Cursor: also install the prompt guard and audit hooks")] = False,
) -> None:
    """Connect AI agents on this computer to LoomAI (`all` connects every one it finds)."""
    if not load_config().token:
        _fail("Run `loom init` or `loom join` first.")
    supported = ("cursor", "claude", "claude-desktop")
    if agent == "all":
        agents = connectors.detect_agents()
        hooks = True
        if not agents:
            _fail("Found no Cursor, Claude Code or Claude desktop app on this computer.")
    elif agent in supported:
        agents = [agent]
    else:
        _fail("Supported agents: " + ", ".join(supported) + ", all")
    notes = []
    for name in agents:
        if name == "cursor":
            for change in connectors.connect_cursor(project_dir, hooks):
                typer.secho(f"✓ Cursor: {change}", fg="green")
            notes.append("Cursor: ready now, no restart needed (it reloads Loom by itself).")
        elif name == "claude":
            done, message = connectors.connect_claude()
            typer.secho(("✓ Claude Code: " if done else "") + message, fg="green" if done else "yellow")
            if done:
                notes.append("Claude Code: ready in every new session (`claude --continue` reopens your last chat).")
        else:
            message, changed = connectors.connect_claude_desktop()
            typer.secho(f"✓ Claude app: {message}", fg="green")
            notes.append(_refresh_claude_desktop(changed))
    from .watch import replace_legacy_watches

    if replaced := replace_legacy_watches(connectors.executable()):
        notes.append(f"Reopened {replaced} watch window(s) as `loom watch` (Daddy AI is now LoomAI).")
    for note in notes:
        typer.echo(note)
    typer.echo("Then just ask your agent: “Loom, who am I?”")


@app.command()
def update() -> None:
    """Install the latest loom and switch your AI apps and watch window over to it."""
    uv = updates.find_uv()
    if uv is None:
        _fail(f"Can't find uv. Paste the install line again instead:\n  curl -fsSL {INSTALL_SCRIPT_URL} | sh")
    if not updates.installed_as_uv_tool(uv):
        _fail(f"This loom runs from {sys.prefix}, not from the installer; update that checkout with `git pull`.")
    reconnect = [connectors.executable(), "connect", "all"]
    if sys.platform == "win32":
        # Windows can't replace loom.exe while it runs, so finish in a new window once this one exits.
        script = f"timeout /t 2 >nul & {subprocess.list2cmdline(updates.legacy_uninstall_command(uv))} >nul 2>&1 & " \
                 f"{subprocess.list2cmdline(updates.install_command(uv))} & " \
                 f"{subprocess.list2cmdline(reconnect)} & pause"
        subprocess.Popen(["cmd", "/c", script], creationflags=subprocess.CREATE_NEW_CONSOLE)
        typer.echo("Updating in a new window; follow it there.")
        return
    typer.echo(f"Updating loom {CLI_VERSION}…")
    subprocess.run(updates.legacy_uninstall_command(uv), check=False, capture_output=True)
    if subprocess.run(updates.install_command(uv), check=False).returncode != 0:
        _fail("The update failed; your current loom still works. Try again, or paste the install line again.")
    new = updates.installed_version() or "?"
    typer.secho(f"✓ loom {new} installed", fg="green")
    if subprocess.run(reconnect, check=False).returncode != 0:
        typer.echo("Run `loom connect all` to finish connecting your apps.")
    typer.echo("Your `loom watch` window switches to the new version by itself within a minute.")


def _refresh_claude_desktop(changed: bool) -> str:
    """The Claude app only reads its config at startup, so restart it for the user when it is open."""
    if not changed:
        return "Claude app: already connected."
    if not connectors.claude_desktop_running():
        return "Claude app: ready the next time you open it."
    by_hand = "quit it fully (" + ("Cmd+Q" if sys.platform == "darwin" else "tray icon → Quit") + ") and open it again"
    if sys.platform == "darwin" and sys.stdin.isatty() and typer.confirm(
        "The Claude app is open and needs a quick restart to load Loom (your chats are kept). Restart it now?",
        default=True,
    ):
        if connectors.restart_claude_desktop():
            return "Claude app: restarted, Loom is ready."
        return f"Claude app: couldn't restart it automatically; {by_hand}."
    return f"Claude app: {by_hand} to load Loom."


@app.command()
def mcp() -> None:
    """Run the MCP server agents talk to (launched by Cursor / Claude Code over stdio)."""
    from .mcp_server import run

    run()


@hook_app.command("prompt-check")
def hook_prompt_check() -> None:
    from .hooks import prompt_check

    raise typer.Exit(prompt_check())


@hook_app.command("audit")
def hook_audit() -> None:
    from .hooks import audit as audit_hook

    raise typer.Exit(audit_hook())


@hook_app.command("guard")
def hook_guard() -> None:
    from .hooks import guard_check

    raise typer.Exit(guard_check())


# ── Agent guard rules ─────────────────────────────────────────────────────────

GuardKind = Annotated[str, typer.Argument(help="shell (commands), read (files) or mcp (tool calls, server.tool)")]


def _check_kind(kind: str) -> guard.Kind:
    if kind not in guard.KINDS:
        _fail("The kind must be shell, read or mcp.")
    return kind  # type: ignore[return-value]


@guard_app.command("rules")
def guard_rules() -> None:
    """Show the rules every agent in the family follows: the team's own first, then the built-in ones."""
    rules = _call(_client().get, "/api/guard/rules")
    typer.echo("Team rules (checked first):" if rules["family"] else "No team rules yet (`loom guard add`).")
    for rule in rules["family"]:
        typer.echo(f"  {rule['id']}  {rule['kind']:<5} {rule['verdict']:<5} /{rule['pattern']}/  {rule['reason']}")
    typer.echo("Built-in rules:")
    for rule in rules["builtin"]:
        typer.echo(f"  {rule['kind']:<5} {rule['verdict']:<5} {rule['reason']}")
    typer.echo("Always on: secrets in commands, files or tool calls, and credential files, are blocked.")


@guard_app.command("add")
def guard_add(
    kind: GuardKind,
    pattern: Annotated[str, typer.Argument(help="Regular expression, matched case-insensitively")],
    reason: Annotated[str, typer.Option("--reason", "-r", help="What people see when it applies")],
    verdict: Annotated[str, typer.Option(help="deny, ask (needs the user's OK) or allow")] = "deny",
) -> None:
    """Add a team rule (admins), e.g. `loom guard add shell "npm publish" -r "Releases go through CI"`."""
    if verdict not in ("deny", "ask", "allow"):
        _fail("The verdict must be deny, ask or allow.")
    body = {"kind": _check_kind(kind), "pattern": pattern, "verdict": verdict, "reason": reason}
    rule = _call(_client().post, "/api/guard/rules", body)
    typer.secho(f"Added rule {rule['id']}: {verdict} {kind} matching /{pattern}/", fg="green")
    typer.echo("Every agent in the family follows it within 5 minutes (shell and tool calls right away).")


@guard_app.command("remove")
def guard_remove(rule_id: str) -> None:
    """Remove a team rule (admins)."""
    result = _call(_client().delete, f"/api/guard/rules/{rule_id}")
    typer.echo(f"Removed the rule /{result['removed']}/.")


@guard_app.command("test")
def guard_test(
    kind: GuardKind,
    target: Annotated[str, typer.Argument(help="The command, file path or server.tool to try")],
) -> None:
    """Show what the guard would decide, without running anything or writing to the ledger."""
    family = guard.rules_from(_call(_client().get, "/api/guard/rules")["family"])
    decision = guard.evaluate(_check_kind(kind), target, family_rules=family)
    color = {"allow": "green", "ask": "yellow", "deny": "red"}[decision.verdict]
    typer.secho(f"{decision.verdict}" + (f": {decision.reason}" if decision.reason else ""), fg=color)
