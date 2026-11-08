"""The whole family flow against a throwaway database (skipped when Postgres is not running)."""

import os

import psycopg
import pytest

ADMIN_URL = os.environ.get("LOOM_TEST_ADMIN_URL", "postgresql://daddy:daddy@localhost:5433/daddy")
TEST_DB = "loom_test"


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    try:
        with psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=2) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
            conn.execute(f"CREATE DATABASE {TEST_DB}")
    except psycopg.OperationalError:
        pytest.skip("Postgres is not running (docker compose up -d)")

    os.environ["LOOM_DATABASE_URL"] = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}"
    os.environ["LOOM_BLOB_DIR"] = str(tmp_path_factory.mktemp("blobs"))
    from fastapi.testclient import TestClient

    from loom_server import db
    from loom_server.api import app
    from loom_server.config import get_settings

    get_settings.cache_clear()
    db._pool = None
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Loom-Client": "pytest"}


def test_family_flow(api):
    alice = api.post("/api/bootstrap", json={
        "family_name": "Test Family", "email": "alice@example.com", "display_name": "Alice",
        "device_name": "alice-laptop",
    }).json()["token"]
    assert api.post("/api/bootstrap", json={
        "family_name": "Second", "email": "eve@example.com", "display_name": "Eve", "device_name": "x",
    }).status_code == 403

    for slug in ("app", "private"):
        assert api.post("/api/projects", json={"name": slug, "slug": slug}, headers=auth(alice)).status_code == 200
    upload = api.post("/api/projects/app/documents", headers=auth(alice), json={"files": [
        {"path": "docs/deploy.md", "content": "# Deploy\n\nWe deploy the gateway to EC2 with Docker Compose."},
        {"path": ".env", "content": "DB_PASSWORD=hunter2hunter2"},
        {"path": "config.py", "content": "token = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789'"},
    ]}).json()
    assert upload["indexed"] == 1
    assert {s["path"] for s in upload["skipped_files"]} == {".env", "config.py"}
    api.post("/api/projects/private/notes", headers=auth(alice),
             json={"title": "Salaries", "content": "The salary budget for the deploy team is 10k."})

    code = api.post("/api/invites", headers=auth(alice), json={
        "email": "bob@example.com", "display_name": "Bob", "project": "app",
    }).json()["code"]
    bob = api.post("/api/join", json={"code": code, "device_name": "bob-laptop"}).json()["token"]
    assert api.post("/api/join", json={"code": code, "device_name": "again"}).status_code == 403

    answer = api.post("/api/ask", headers=auth(bob), json={"query": "How do we deploy the gateway?"}).json()
    assert answer["grounded"] and answer["citations"][0]["path"] == "docs/deploy.md"
    salary = api.post("/api/ask", headers=auth(bob), json={"query": "What is the salary budget?"}).json()
    assert salary["answer"] == "Not in project knowledge."
    assert api.post("/api/ask", headers=auth(bob), json={"query": "salary", "project": "private"}).status_code == 403

    assert api.post("/api/projects", json={"name": "Bob's side project"}, headers=auth(bob)).status_code == 200
    bob_roles = {p["slug"]: p["role"] for p in api.get("/api/projects", headers=auth(bob)).json()}
    assert bob_roles == {"app": "member", "bob-s-side-project": "owner"}

    sent = api.post("/api/send", headers=auth(alice), json={
        "project": "app", "to": "Bob", "filename": "deploy/SKILL.md", "content": "# Deploy skill", "kind": "skill",
    }).json()
    assert sent["delivery"]["install_path"] == "skills/deploy/SKILL.md"
    blocked = api.post("/api/send", headers=auth(alice), json={
        "project": "app", "to": "Bob", "filename": "notes.txt", "content": "key AKIAZ3MHQ7XK2PLW4N8R",
    })
    assert blocked.status_code == 403 and "secrets" in blocked.json()["reason"]

    inbox = api.get("/api/inbox", headers=auth(bob)).json()
    assert [i["filename"] for i in inbox] == ["deploy"]
    accepted = api.post(f"/api/inbox/{inbox[0]['id']}/accept", headers=auth(bob)).json()
    assert accepted["content"] == "# Deploy skill"

    audit = api.get("/api/audit", headers=auth(alice)).json()
    decisions = {(e["action"], e["decision"]) for e in audit}
    assert {("delivery.send", "deny"), ("knowledge.ask", "deny"), ("delivery.accept", "allow")} <= decisions
    assert api.get("/api/audit", headers=auth(bob)).status_code == 403

    device = next(d for d in api.get("/api/devices", headers=auth(alice)).json() if d["user"] == "Bob")
    api.post(f"/api/devices/{device['id']}/revoke", headers=auth(alice))
    assert api.get("/api/me", headers=auth(bob)).status_code == 401


def _new_family(api, monkeypatch, family: str, name: str) -> str:
    from loom_server.config import get_settings

    monkeypatch.setenv("LOOM_OPEN_SIGNUP", "true")
    get_settings.cache_clear()
    body = {"family_name": family, "email": f"{name.lower()}@example.com", "display_name": name, "device_name": "d"}
    token = api.post("/api/bootstrap", json=body).json()["token"]
    monkeypatch.delenv("LOOM_OPEN_SIGNUP")
    get_settings.cache_clear()
    return token


def _join_team_link(api, admin: str, body: dict) -> str:
    """Ask with a team link, have the admin approve, and collect the new member's token."""
    request = api.post("/api/join", json=body).json()
    api.post(f"/api/join/requests/{request['request']}/approve", headers=auth(admin))
    poll = {"request": request["request"], "secret": request["secret"]}
    return api.post("/api/join/status", json=poll).json()["token"]


def test_open_signup_families_are_isolated(api, monkeypatch):
    zed = _new_family(api, monkeypatch, "Other Team", "Zed")

    assert api.get("/api/projects", headers=auth(zed)).json() == []
    answer = api.post("/api/ask", headers=auth(zed), json={"query": "How do we deploy the gateway?"}).json()
    assert answer["answer"] == "Not in project knowledge." and answer["citations"] == []
    assert api.post("/api/ask", headers=auth(zed), json={"query": "deploy", "project": "app"}).status_code == 404
    assert api.post("/api/projects", json={"name": "app", "slug": "app"}, headers=auth(zed)).status_code == 200
    note = {"project": "app", "to": "Alice", "filename": "hi.txt", "content": "hi"}
    sent = api.post("/api/send", headers=auth(zed), json=note)
    assert sent.status_code in (400, 404)
    family = api.get("/api/family", headers=auth(zed)).json()
    assert [m["name"] for m in family["members"]] == ["Zed"]
    assert {e["user"] for e in api.get("/api/audit", headers=auth(zed)).json()} <= {"Zed", None}


def test_team_link_people_wait_for_approval(api, monkeypatch):
    kim = _new_family(api, monkeypatch, "Link Team", "Kim")
    api.post("/api/projects", json={"name": "Demo"}, headers=auth(kim))
    assert api.post("/api/invites", headers=auth(kim), json={"project": "demo", "role": "owner"}).status_code == 400
    link = api.post("/api/invites", headers=auth(kim), json={"project": "demo", "max_uses": 2}).json()
    assert (link["max_uses"], link["expires_in_days"], link["needs_approval"]) == (2, 2, True)

    def ask(email, name):
        body = {"code": link["code"], "device_name": "laptop", "email": email, "display_name": name}
        return api.post("/api/join", json=body)

    def status(request, poll_with=None):
        body = {"request": request["request"], "secret": poll_with or request["secret"]}
        return api.post("/api/join/status", json=body)

    assert api.post("/api/join", json={"code": link["code"], "device_name": "d"}).status_code == 400
    assert ask("kim@example.com", "Not Kim").status_code == 403
    old_cli = api.post("/api/join", headers={"User-Agent": "loom-cli/0.4.0"}, json={
        "code": link["code"], "device_name": "d", "email": "old@example.com", "display_name": "Old"})
    assert old_cli.status_code == 400 and "too old" in old_cli.json()["reason"]
    lee = ask("lee@example.com", "Lee")
    assert lee.status_code == 202 and "token" not in lee.json() and lee.json()["approvers"] == ["Kim"]
    lee = lee.json()
    assert status(lee).json() == {"status": "pending"}
    assert status(lee, poll_with=lee["secret"][::-1]).status_code == 404
    stranger = ask("eve@example.com", "Eve").json()
    assert ask("ned@example.com", "Ned").status_code == 403  # two places, two people already waiting

    waiting = [(r["name"], r["project"], r["device"]) for r in api.get("/api/join/requests", headers=auth(kim)).json()]
    assert waiting == [("Lee", "demo", "laptop"), ("Eve", "demo", "laptop")]
    assert api.post(f"/api/join/requests/{lee['request']}/approve", headers=auth(kim)).json()["status"] == "approved"
    assert api.post(f"/api/join/requests/{stranger['request']}/deny", headers=auth(kim)).status_code == 200
    assert "declined" in status(stranger).json()["reason"]

    joined = status(lee).json()
    assert joined["status"] == "joined" and joined["user"] == "Lee"
    assert status(lee).status_code == 403  # the token is handed out once
    lee_token = joined["token"]
    assert [p["slug"] for p in api.get("/api/projects", headers=auth(lee_token)).json()] == ["demo"]
    assert api.get("/api/join/requests", headers=auth(lee_token)).json() == []
    max_ = ask("max@example.com", "Max").json()
    assert api.post(f"/api/join/requests/{max_['request']}/approve", headers=auth(lee_token)).status_code == 403
    actions = {e["action"] for e in api.get("/api/audit", headers=auth(kim)).json()}
    assert {"family.join_request", "join.approve", "join.deny", "family.join"} <= actions

    again = api.post("/api/invites", headers=auth(kim), json={"project": "demo"}).json()
    assert again["max_uses"] == 10
    assert api.post("/api/invites/cancel", headers=auth(lee_token), json={"code": again["code"]}).status_code == 404
    assert api.post("/api/invites/cancel", headers=auth(kim), json={"code": again["code"]}).json()["cancelled"]
    body = {"code": again["code"], "device_name": "d", "email": "ola@example.com", "display_name": "Ola"}
    assert api.post("/api/join", json=body).status_code == 403

    personal = api.post(
        "/api/invites", headers=auth(kim), json={"email": "pat@example.com", "display_name": "Pat"}
    ).json()
    assert (personal["max_uses"], personal["expires_in_days"], personal["needs_approval"]) == (1, 7, False)
    assert api.post("/api/join", json={"code": personal["code"], "device_name": "d"}).json()["user"] == "Pat"
    assert api.post("/api/join", json={"code": personal["code"], "device_name": "d"}).status_code == 403


def test_rename(api, monkeypatch):
    ana = _new_family(api, monkeypatch, "Rename Team", "Ana")
    api.post("/api/projects", json={"name": "Club"}, headers=auth(ana))
    link = api.post("/api/invites", headers=auth(ana), json={"project": "club", "max_uses": 1}).json()["code"]
    body = {"code": link, "device_name": "d", "email": "ben@example.com", "display_name": "Ben"}
    ben = _join_team_link(api, ana, body)

    def rename(token, name):
        return api.post("/api/me/name", headers=auth(token), json={"display_name": name})

    assert rename(ben, "  ana ").status_code == 400
    assert rename(ben, "   ").status_code == 400
    assert rename(ben, "Benjamin  Stone").json() == {"user": "Benjamin Stone", "previous": "Ben"}
    assert api.get("/api/me", headers=auth(ben)).json()["user"] == "Benjamin Stone"
    note = {"project": "club", "to": "Benjamin Stone", "filename": "m.txt", "content": "hi", "kind": "message"}
    assert api.post("/api/send", headers=auth(ana), json=note).json()["recipient"] == "Benjamin Stone"
    assert api.post("/api/send", headers=auth(ana), json={**note, "to": "Ben"}).status_code == 404
    renames = [e for e in api.get("/api/audit", headers=auth(ana)).json() if e["action"] == "user.rename"]
    assert [e["detail"] for e in renames if e["decision"] == "allow"] == [{"old": "Ben", "new": "Benjamin Stone"}]
    assert sum(e["decision"] != "allow" for e in renames) == 2


def test_photos_and_pdfs_travel_byte_for_byte(api, monkeypatch):
    import base64

    from loom_server.config import get_settings

    pia = _new_family(api, monkeypatch, "Photo Team", "Pia")
    api.post("/api/projects", json={"name": "Pics"}, headers=auth(pia))
    link = api.post("/api/invites", headers=auth(pia), json={"project": "pics", "max_uses": 1}).json()["code"]
    body = {"code": link, "device_name": "d", "email": "quinn@example.com", "display_name": "Quinn"}
    quinn = _join_team_link(api, pia, body)

    photo = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 400
    encoded = base64.b64encode(photo).decode()

    def send(**fields):
        return api.post("/api/send", headers=auth(pia), json={"project": "pics", "to": "Quinn", **fields})

    sent = send(filename="cat.png", content_base64=encoded).json()["delivery"]
    assert sent["size_bytes"] == len(photo)
    assert send(filename="cat.png", content_base64=encoded, kind="skill").status_code == 400
    assert send(filename="cat.png", content_base64="not base64!").status_code == 400
    assert send(filename="cat.png", content="x", content_base64=encoded).status_code == 400
    assert send(filename="cert.p12", content_base64=encoded).status_code == 403

    monkeypatch.setenv("LOOM_MAX_ARTIFACT_BYTES", "1000")
    get_settings.cache_clear()
    too_big = send(filename="cat.png", content_base64=encoded)
    monkeypatch.delenv("LOOM_MAX_ARTIFACT_BYTES")
    get_settings.cache_clear()
    assert too_big.status_code == 400 and "the limit is 1 KB" in too_big.json()["reason"]

    old_cli = api.post(f"/api/inbox/{sent['id']}/accept", headers={**auth(quinn), "User-Agent": "loom-cli/0.1"})
    assert old_cli.status_code == 400 and "loom update" in old_cli.json()["reason"]
    assert old_cli.headers["X-Loom-Latest-Cli"]
    accepted = api.post(f"/api/inbox/{sent['id']}/accept", headers=auth(quinn)).json()
    assert "content" not in accepted and base64.b64decode(accepted["content_base64"]) == photo

def test_guard_decides_audits_and_follows_family_rules(api, monkeypatch):
    gus = _new_family(api, monkeypatch, "Guard Team", "Gus")
    other = _new_family(api, monkeypatch, "Unrelated Team", "Una")
    link = api.post("/api/invites", headers=auth(gus), json={"max_uses": 1}).json()["code"]
    body = {"code": link, "device_name": "d", "email": "mo@example.com", "display_name": "Mo"}
    mo = _join_team_link(api, gus, body)

    def check(token, kind, target):
        return api.post("/api/guard", headers=auth(token), json={"kind": kind, "target": target}).json()

    assert check(mo, "shell", "npm test")["verdict"] == "allow"
    assert check(mo, "shell", "git push --force")["verdict"] == "ask"
    assert check(mo, "shell", "cat .env")["verdict"] == "deny"
    leaked = check(mo, "shell", "export TOKEN=ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8")
    assert leaked["verdict"] == "deny" and leaked["rule"] == "secret"

    rule = {"kind": "shell", "pattern": r"\bterraform\s+apply\b", "verdict": "deny", "reason": "Only CI applies"}
    assert api.post("/api/guard/rules", headers=auth(mo), json=rule).status_code == 403
    assert api.post("/api/guard/rules", headers=auth(gus), json={**rule, "pattern": "("}).status_code == 400
    rule_id = api.post("/api/guard/rules", headers=auth(gus), json=rule).json()["id"]
    assert check(mo, "shell", "terraform apply")["rule"] == rule_id
    assert check(other, "shell", "terraform apply")["verdict"] == "allow"
    assert api.get("/api/guard/rules", headers=auth(other)).json()["family"] == []
    assert len(api.get("/api/guard/rules", headers=auth(mo)).json()["family"]) == 1

    events = [e for e in api.get("/api/audit", headers=auth(gus)).json() if e["action"] == "agent.shell"]
    assert {(e["detail"]["target"], e["decision"]) for e in events} >= {
        ("npm test", "allow"), ("cat .env", "deny"), ("terraform apply", "deny"),
        ("(hidden: contains a secret)", "deny"),
    }
    assert api.delete(f"/api/guard/rules/{rule_id}", headers=auth(other)).status_code == 404
    assert api.delete(f"/api/guard/rules/{rule_id}", headers=auth(gus)).json()["removed"] == rule["pattern"]
    assert check(mo, "shell", "terraform apply")["verdict"] == "allow"


def test_prune_forgets_only_files_gone_from_that_folder(api, monkeypatch):
    pat = _new_family(api, monkeypatch, "Prune Team", "Pat")
    api.post("/api/projects", json={"name": "web", "slug": "web"}, headers=auth(pat))
    files = [{"path": p, "content": f"# {p}\n\nSome text about {p}."} for p in ("a.md", "old/b.md", "c.md")]
    api.post("/api/projects/web/documents", headers=auth(pat), json={"files": files, "source": "local:web"})
    api.post("/api/projects/web/notes", headers=auth(pat), json={"title": "Keep me", "content": "A team decision."})

    pruned = api.post("/api/projects/web/documents/prune", headers=auth(pat),
                      json={"source": "local:web", "keep": ["a.md", "c.md"]})
    assert pruned.json()["removed"] == ["old/b.md"]
    paths = {d["path"] for d in api.get("/api/projects/web/files", headers=auth(pat)).json()["files"]}
    assert "old/b.md" not in paths and {"a.md", "c.md"} <= paths
    assert any(p.startswith("notes/") for p in paths)
    assert api.post("/api/projects/web/documents/prune", headers=auth(pat),
                    json={"source": "note", "keep": ["a.md"]}).status_code == 422
    assert api.post("/api/projects/web/documents/prune", headers=auth(pat),
                    json={"source": "local:web", "keep": []}).status_code == 422


def test_dashboard_runs_the_whole_invitation_flow(api, monkeypatch):
    import re

    ida = _new_family(api, monkeypatch, "Dashboard Team", "Ida")
    login = api.post("/api/dashboard/link", headers=auth(ida)).json()["path"].split("login=")[1]
    board = api.post("/api/dashboard/session", json={"code": login}).json()["token"]

    assert api.post("/api/projects", json={"name": "Trip Photos"}, headers=auth(board)).json()["slug"] == "trip-photos"
    personal = api.post("/api/invites", headers=auth(board), json={
        "email": "jo@example.com", "display_name": "Jo", "project": "trip-photos", "role": "viewer"}).json()
    assert re.fullmatch(r"inv[A-Za-z0-9]{40}", personal["code"])  # nothing a chat app can turn into italics
    link = api.post("/api/invites", headers=auth(board), json={"project": "trip-photos", "max_uses": 3}).json()
    api.post("/api/join", json={"code": link["code"], "device_name": "d", "email": "max@example.com",
                                "display_name": "Max"})

    listed = api.get("/api/invites", headers=auth(board)).json()
    assert [(i["team_link"], i["name"], i["project"], i["role"], i["waiting"]) for i in listed] == [
        (True, None, "trip-photos", "member", 1), (False, "Jo", "trip-photos", "viewer", 0)]
    assert "code" not in listed[0]

    jo = api.post("/api/join", json={"code": personal["code"], "device_name": "d"}).json()["token"]
    assert [i["team_link"] for i in api.get("/api/invites", headers=auth(board)).json()] == [True]
    assert api.get("/api/invites", headers=auth(jo)).json() == []
    assert api.post("/api/invites/cancel", headers=auth(jo), json={"id": listed[0]["id"]}).status_code == 404
    assert api.post("/api/invites/cancel", headers=auth(board), json={"id": listed[0]["id"]}).json()["cancelled"]
    assert api.get("/api/invites", headers=auth(board)).json() == []
    assert api.post("/api/invites/cancel", headers=auth(board), json={}).status_code == 400

    member = {"email": "jo@example.com", "role": "member"}
    assert api.post("/api/projects/trip-photos/members", headers=auth(board), json=member).json()["role"] == "member"
    assert api.delete("/api/projects/trip-photos/members/jo@example.com", headers=auth(board)).status_code == 200
    assert api.get("/api/projects", headers=auth(jo)).json() == []


def test_people_already_in_the_team_join_a_project_with_its_link_at_once(api, monkeypatch):
    uma = _new_family(api, monkeypatch, "Project Team", "Uma")
    other = _new_family(api, monkeypatch, "Elsewhere", "Oz")
    for name in ("Base", "Trip"):
        api.post("/api/projects", json={"name": name}, headers=auth(uma))
    base = api.post("/api/invites", headers=auth(uma), json={"project": "base"}).json()["code"]
    vic = _join_team_link(api, uma, {"code": base, "device_name": "d", "email": "vic@example.com",
                                     "display_name": "Vic"})
    trip = api.post("/api/invites", headers=auth(uma), json={"project": "trip", "role": "viewer"}).json()

    added = api.post("/api/join/project", headers=auth(vic), json={"code": trip["code"]}).json()
    assert (added["project"], added["role"], added["already"]) == ("trip", "viewer", False)
    assert {p["slug"] for p in api.get("/api/projects", headers=auth(vic)).json()} == {"base", "trip"}
    again = api.post("/api/join/project", headers=auth(vic), json={"code": trip["code"]}).json()
    assert again["already"] and again["role"] == "viewer"
    assert api.post("/api/join/project", headers=auth(uma), json={"code": trip["code"]}).json()["role"] == "owner"
    assert api.get("/api/join/requests", headers=auth(uma)).json() == []
    assert [i["uses"] for i in api.get("/api/invites", headers=auth(uma)).json() if i["project"] == "trip"] == [0]

    elsewhere = api.post("/api/join/project", headers=auth(other), json={"code": trip["code"]})
    assert elsewhere.status_code == 409 and "another team, 'Project Team'" in elsewhere.json()["reason"]
    assert api.post("/api/join/project", headers=auth(vic), json={"code": "invNope"}).status_code == 404
    personal = api.post("/api/invites", headers=auth(uma), json={
        "email": "wes@example.com", "display_name": "Wes", "project": "trip"}).json()["code"]
    assert api.post("/api/join/project", headers=auth(vic), json={"code": personal}).status_code == 403
    api.post("/api/invites/cancel", headers=auth(uma), json={"code": trip["code"]})
    assert api.post("/api/join/project", headers=auth(vic), json={"code": trip["code"]}).status_code == 403
    actions = [(e["action"], e["decision"]) for e in api.get("/api/audit", headers=auth(uma)).json()]
    assert ("project.join", "allow") in actions and ("project.join", "deny") in actions
