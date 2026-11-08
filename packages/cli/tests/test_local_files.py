import os
import time

from loom_cli import local_files


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in local_files.FOLDERS:
        (tmp_path / name).mkdir()
    return tmp_path


def _file(path, data=b"\x89PNG\r\n\x1a\n", age=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    then = time.time() - age
    os.utime(path, (then, then))
    return path


def test_a_bare_file_name_is_found_in_the_usual_folders(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    photo = _file(home / "Downloads" / "Beach Day.JPG")
    workspace = tmp_path / "nowhere"

    assert local_files.resolve("beach day.jpg", workspace) == (photo, "")
    assert local_files.resolve("Beach Day.JPG", workspace) == (photo, "")
    assert local_files.resolve(str(photo), workspace) == (photo, "")
    assert local_files.resolve("~/Downloads/Beach Day.JPG", workspace) == (photo, "")
    missing, why = local_files.resolve("nope.png", workspace)
    assert missing is None and "File not found" in why and "~/Downloads" in why


def test_same_name_in_two_folders_asks_which(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _file(home / "Desktop" / "a" / "scan.pdf")
    _file(home / "Documents" / "scan.pdf")

    found, why = local_files.resolve("scan.pdf", tmp_path)
    assert found is None and "Several files" in why and why.count("scan.pdf") >= 3


def test_find_lists_newest_photos_first_and_skips_hidden_files(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _file(home / "Pictures" / "old.png", age=86400)
    _file(home / "Desktop" / "Screenshot 2026-11-07.png", age=60)
    _file(home / "Desktop" / ".secret.png")
    _file(home / "Downloads" / "notes.txt", b"hi")

    found, blocked = local_files.find(kind="image")
    assert [f.path.name for f in found] == ["Screenshot 2026-11-07.png", "old.png"]
    assert blocked == []
    assert [f.path.name for f in local_files.find("screenshot")[0]] == ["Screenshot 2026-11-07.png"]


def test_a_folder_the_os_refuses_is_explained(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / "Desktop").chmod(0)
    try:
        found, why = local_files.resolve("photo.png", tmp_path)
        assert found is None and "~/Desktop" in why and "read" in why
    finally:
        (home / "Desktop").chmod(0o755)
