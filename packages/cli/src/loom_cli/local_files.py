"""Finding the photo or file a user means ("the photo I just downloaded") in their usual folders.

Agents in the Claude app have no project folder, and a picture pasted into a chat never reaches the agent
as a file, so sending one needs the saved copy on disk.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

FOLDERS = ("Desktop", "Downloads", "Documents", "Pictures")
MAX_DEPTH = 2
MAX_ENTRIES = 20_000

Kind = Literal["any", "image", "pdf", "document", "video"]
EXTENSIONS: dict[str, frozenset[str]] = {
    "image": frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".webp", ".tif", ".tiff", ".bmp"}),
    "pdf": frozenset({".pdf"}),
    "document": frozenset({".pdf", ".doc", ".docx", ".pages", ".key", ".ppt", ".pptx", ".xls", ".xlsx",
                           ".numbers", ".txt", ".md", ".csv", ".rtf"}),
    "video": frozenset({".mov", ".mp4", ".m4v"}),
}


@dataclass(frozen=True)
class Found:
    path: Path
    size: int
    modified: float


def folders() -> list[Path]:
    return [Path.home() / name for name in FOLDERS]


def blocked_hint(blocked: list[Path]) -> str:
    """What to do when the operating system kept this app out of a folder."""
    if not blocked:
        return ""
    names = ", ".join(f"~/{p.name}" for p in blocked)
    if sys.platform == "darwin":
        return (f" macOS didn't let this app read {names}. Allow it in System Settings → Privacy & Security → "
                "Files and Folders (turn those folders on for Claude or Cursor), then try again.")
    return f" This app isn't allowed to read {names}."


def find(name: str = "", kind: Kind = "any", limit: int = 10, *, exact: bool = False) -> tuple[list[Found], list[Path]]:
    """Newest files in the usual folders whose name contains ``name`` (or equals it); also the folders refused."""
    needle = name.strip().lower()
    wanted = EXTENSIONS.get(kind)
    found: list[Found] = []
    blocked: list[Path] = []
    seen = 0
    for root in folders():
        stack = [(root, 0)]
        while stack and seen < MAX_ENTRIES:
            folder, depth = stack.pop()
            try:
                entries = list(os.scandir(folder))
            except PermissionError:
                if depth == 0:
                    blocked.append(root)
                continue
            except OSError:
                continue
            for entry in entries:
                seen += 1
                if entry.name.startswith("."):
                    continue
                try:
                    info = entry.stat()
                except OSError:
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if depth < MAX_DEPTH and not entry.name.endswith((".app", ".photoslibrary")):
                        stack.append((Path(entry.path), depth + 1))
                    continue
                lowered = entry.name.lower()
                if needle and not _matches(lowered, needle, exact):
                    continue
                if wanted and Path(lowered).suffix not in wanted:
                    continue
                found.append(Found(Path(entry.path), info.st_size, info.st_mtime))
    found.sort(key=lambda f: f.modified, reverse=True)
    return found[:limit], blocked


def _matches(filename: str, needle: str, exact: bool) -> bool:
    if exact:
        return needle in (filename, Path(filename).stem)
    return needle in filename


def resolve(text: str, workspace: Path) -> tuple[Path | None, str]:
    """The file the agent means by ``text``, or None and an explanation the agent can pass on."""
    given = Path(text).expanduser()
    if given.is_absolute():
        candidates = [given]
    elif len(given.parts) == 1:
        candidates = [workspace / given]  # elsewhere a bare name is searched for, so duplicates are noticed
    else:
        candidates = [workspace / given, Path.home() / given, *(root / given for root in folders())]
    blocked: list[Path] = []
    for candidate in candidates:
        try:
            if stat.S_ISREG(candidate.stat().st_mode):
                return candidate, ""
        except PermissionError:
            blocked.append(candidate.parent)
        except OSError:
            continue
    if len(given.parts) == 1:
        matches, refused = find(given.name, limit=6, exact=True)
        blocked += refused
        if len(matches) == 1:
            return matches[0].path, ""
        if matches:
            listed = "\n".join(f"- {m.path}" for m in matches)
            return None, f"Several files are called {given.name}; send again with the full path of one:\n{listed}"
    where = "that path" if given.is_absolute() else f"{workspace} or ~/" + ", ~/".join(FOLDERS)
    return None, f"File not found: {text} (looked in {where}).{blocked_hint(blocked)}"
