"""The `daddy` command's version, shared so the server (deployed from the same repo) knows the latest one.

Bump CLI_VERSION (and packages/cli/pyproject.toml) with every change people should install.
"""

from __future__ import annotations

import re

CLI_VERSION = "0.2.0"
BINARY_FILES_SINCE = "0.2.0"

_CLI_AGENT = re.compile(r"daddy-cli/(\d+(?:\.\d+)*)")


def parse(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version)[:3])


def is_older(version: str, than: str) -> bool:
    return parse(version) < parse(than)


def cli_version_of(user_agent: str | None) -> str | None:
    """The version in a ``daddy-cli/<version>`` User-Agent, or None for browsers and other clients."""
    match = _CLI_AGENT.search(user_agent or "")
    return match.group(1) if match else None
