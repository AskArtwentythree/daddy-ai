"""The whole family flow against a throwaway database (skipped when Postgres is not running)."""

import os

import psycopg
import pytest

ADMIN_URL = os.environ.get("DADDY_TEST_ADMIN_URL", "postgresql://daddy:daddy@localhost:5433/daddy")
TEST_DB = "daddy_test"


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    try:
        with psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=2) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
            conn.execute(f"CREATE DATABASE {TEST_DB}")
    except psycopg.OperationalError:
        pytest.skip("Postgres is not running (docker compose up -d)")

    os.environ["DADDY_DATABASE_URL"] = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}"
    os.environ["DADDY_BLOB_DIR"] = str(tmp_path_factory.mktemp("blobs"))
    from fastapi.testclient import TestClient

    from daddy_server import db
    from daddy_server.api import app
    from daddy_server.config import get_settings

    get_settings.cache_clear()
    db._pool = None
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Daddy-Client": "pytest"}


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
    from daddy_server.config import get_settings

    monkeypatch.setenv("DADDY_OPEN_SIGNUP", "true")
    get_settings.cache_clear()
    body = {"family_name": family, "email": f"{name.lower()}@example.com", "display_name": name, "device_name": "d"}
    token = api.post("/api/bootstrap", json=body).json()["token"]
    monkeypatch.delenv("DADDY_OPEN_SIGNUP")
    get_settings.cache_clear()
    return token


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


def test_team_link_lets_several_people_join(api, monkeypatch):
    kim = _new_family(api, monkeypatch, "Link Team", "Kim")
    api.post("/api/projects", json={"name": "Demo"}, headers=auth(kim))
    assert api.post("/api/invites", headers=auth(kim), json={"project": "demo", "role": "owner"}).status_code == 400
    link = api.post("/api/invites", headers=auth(kim), json={"project": "demo", "max_uses": 2}).json()
    assert link["max_uses"] == 2

    def join(email, name):
        body = {"code": link["code"], "device_name": "d", "email": email, "display_name": name}
        return api.post("/api/join", json=body)

    assert api.post("/api/join", json={"code": link["code"], "device_name": "d"}).status_code == 400
    assert join("kim@example.com", "Not Kim").status_code == 403
    lee = join("lee@example.com", "Lee").json()["token"]
    assert join("max@example.com", "Max").status_code == 200
    assert join("ned@example.com", "Ned").status_code == 403
    assert [p["slug"] for p in api.get("/api/projects", headers=auth(lee)).json()] == ["demo"]

    again = api.post("/api/invites", headers=auth(kim), json={"project": "demo"}).json()["code"]
    assert api.post("/api/invites/cancel", headers=auth(lee), json={"code": again}).status_code == 404
    assert api.post("/api/invites/cancel", headers=auth(kim), json={"code": again}).json()["cancelled"]
    body = {"code": again, "device_name": "d", "email": "ola@example.com", "display_name": "Ola"}
    assert api.post("/api/join", json=body).status_code == 403

    personal = api.post(
        "/api/invites", headers=auth(kim), json={"email": "pat@example.com", "display_name": "Pat"}
    ).json()
    assert personal["max_uses"] == 1
    assert api.post("/api/join", json={"code": personal["code"], "device_name": "d"}).json()["user"] == "Pat"
    assert api.post("/api/join", json={"code": personal["code"], "device_name": "d"}).status_code == 403


def test_rename(api, monkeypatch):
    ana = _new_family(api, monkeypatch, "Rename Team", "Ana")
    api.post("/api/projects", json={"name": "Club"}, headers=auth(ana))
    link = api.post("/api/invites", headers=auth(ana), json={"project": "club", "max_uses": 1}).json()["code"]
    body = {"code": link, "device_name": "d", "email": "ben@example.com", "display_name": "Ben"}
    ben = api.post("/api/join", json=body).json()["token"]

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

    from daddy_server.config import get_settings

    pia = _new_family(api, monkeypatch, "Photo Team", "Pia")
    api.post("/api/projects", json={"name": "Pics"}, headers=auth(pia))
    link = api.post("/api/invites", headers=auth(pia), json={"project": "pics", "max_uses": 1}).json()["code"]
    body = {"code": link, "device_name": "d", "email": "quinn@example.com", "display_name": "Quinn"}
    quinn = api.post("/api/join", json=body).json()["token"]

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

    monkeypatch.setenv("DADDY_MAX_ARTIFACT_BYTES", "1000")
    get_settings.cache_clear()
    too_big = send(filename="cat.png", content_base64=encoded)
    monkeypatch.delenv("DADDY_MAX_ARTIFACT_BYTES")
    get_settings.cache_clear()
    assert too_big.status_code == 400 and "the limit is 1 KB" in too_big.json()["reason"]

    accepted = api.post(f"/api/inbox/{sent['id']}/accept", headers=auth(quinn)).json()
    assert "content" not in accepted and base64.b64decode(accepted["content_base64"]) == photo
