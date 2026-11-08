"""House rules for what agents may do on a laptop: run shell commands, read files and call tools.

The gateway decides with these rules plus the family's own. The laptop runs the same code when the
gateway can't be reached, so an agent is never left unguarded.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Literal

from .secrets import is_sensitive_path, scan_text

Kind = Literal["shell", "read", "mcp"]
Verdict = Literal["allow", "deny", "ask"]
KINDS: tuple[Kind, ...] = ("shell", "read", "mcp")


@dataclass(frozen=True)
class Rule:
    kind: Kind
    pattern: str  # case-insensitive regex, searched in the command, the file path or "server.tool"
    verdict: Verdict
    reason: str
    id: str | None = None  # set for a family's own rules

    def matches(self, target: str) -> bool:
        return re.search(self.pattern, target, re.IGNORECASE) is not None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str | None = None
    rule: str | None = None  # "secret", "credential", "built-in" or a family rule id

    def as_dict(self) -> dict:
        return asdict(self)


_SEGMENT_START = r"(?:^|[;&|(]\s*)"

BUILTIN_RULES: tuple[Rule, ...] = (
    Rule("shell", r"\brm\s+(?:-\S+\s+)*(?:/\*?|~/?|\"?\$HOME\"?/?)(?:\s|$)", "deny",
         "Deletes the whole disk or home folder"),
    Rule("shell", r"\bmkfs(?:\.\w+)?\b|\bdd\b[^|;&]*\bof=/dev/", "deny", "Overwrites a disk"),
    Rule("shell", r"\.cursor/hooks\.json|\.claude/settings(?:\.local)?\.json|\.daddy/", "deny",
         "Touches Daddy AI's guard or this computer's Daddy token"),
    Rule("shell", r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b", "ask",
         "Runs a script straight from the internet"),
    Rule("shell", r"\bgit\s+push\b[^;&|]*\s(?:--force(?:-with-lease)?|-f)(?:\s|$)", "ask",
         "Rewrites shared git history"),
    Rule("shell", r"\bterraform\s+destroy\b|\bkubectl\s+delete\b|\bdrop\s+(?:database|table|schema)\b"
         r"|\baws\s+\S+\s+delete-\S+|\baws\s+s3\s+(?:rm|rb)\b", "ask", "Deletes infrastructure or data"),
    Rule("shell", _SEGMENT_START + r"sudo\b", "ask", "Runs with administrator rights"),
    Rule("shell", _SEGMENT_START + r"(?:printenv|env)\s*(?:$|[;&|])", "ask",
         "Prints every environment variable, secrets included"),
    Rule("read", r"(?:^|/)\.daddy/", "deny", "This computer's Daddy token"),
    Rule("mcp", r"\.(?:delete|drop|destroy|remove|purge)", "ask", "Deletes something through a connected tool"),
)

# Programs that show, copy or send a file's contents; with a credential file as argument they leak it.
_READERS = frozenset({
    "cat", "less", "more", "head", "tail", "bat", "base64", "xxd", "od", "strings", "grep", "rg", "awk",
    "sed", "scp", "rsync", "curl", "wget", "nc", "ncat", "pbcopy", "xclip", "openssl",
})


def _segments(command: str) -> list[list[str]]:
    words = []
    for part in re.split(r"\|\||&&|[;|&\n]", command):
        try:
            tokens = shlex.split(part)
        except ValueError:
            tokens = part.split()
        while tokens and (re.match(r"^\w+=", tokens[0]) or tokens[0] == "sudo"):
            tokens = tokens[1:]
        if tokens:
            words.append(tokens)
    return words


def _credential_use(command: str) -> str | None:
    for tokens in _segments(command):
        program = PurePosixPath(tokens[0]).name
        files = [t.lstrip("@") for t in tokens[1:] if not t.startswith("-") and is_sensitive_path(t.lstrip("@"))]
        if not files:
            continue
        name = PurePosixPath(files[0]).name
        if program in _READERS:
            return f"Reads or sends the credential file {name}"
        if program == "git" and len(tokens) > 1 and tokens[1] == "add":
            return f"Would commit the credential file {name}"
    return None


_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec", "fixtures", "testdata"})
_TEST_FILE = re.compile(r"^test_.+|.+_test\.\w+$|.+\.(?:test|spec)\.\w+$|^conftest\.py$")


def is_test_path(path: str) -> bool:
    """Tests are full of fake keys by design, so reading them is not treated as reading a secret."""
    pure = PurePosixPath(path.replace("\\", "/"))
    return bool(_TEST_DIRS.intersection(p.lower() for p in pure.parts[:-1])) or bool(_TEST_FILE.match(pure.name))


def hard_check(kind: Kind, target: str, content: str = "") -> Decision | None:
    """Checks no rule can switch off: secrets never reach a model, a tool or the shell history."""
    if kind == "shell":
        if findings := scan_text(target):
            return Decision("deny", f"The command contains a secret ({findings[0].describe()})", "secret")
        if reason := _credential_use(target):
            return Decision("deny", reason, "credential")
    elif kind == "read":
        if is_sensitive_path(target):
            name = PurePosixPath(target).name
            return Decision("deny", f"{name} holds credentials; Daddy keeps it away from the model", "credential")
        if not is_test_path(target) and (findings := scan_text(content)):
            return Decision("deny", f"The file contains a secret ({findings[0].describe()})", "secret")
    elif kind == "mcp" and (findings := scan_text(content)):
        server = target.split(".", 1)[0]
        return Decision("deny", f"Would send a secret to {server} ({findings[0].describe()})", "secret")
    return None


def evaluate(kind: Kind, target: str, content: str = "", family_rules: tuple[Rule, ...] = ()) -> Decision:
    """Decide one agent action. The family's rules come before the built-in ones; the first match wins."""
    if decision := hard_check(kind, target, content):
        return decision
    for rule in (*family_rules, *BUILTIN_RULES):
        if rule.kind == kind and rule.matches(target):
            return Decision(rule.verdict, rule.reason, rule.id or "built-in")
    return Decision("allow")


def rules_from(items: list[dict]) -> tuple[Rule, ...]:
    """Rules as returned by ``GET /api/guard/rules``, skipping anything malformed."""
    rules = []
    for item in items:
        if item.get("kind") in KINDS and item.get("verdict") in ("allow", "deny", "ask") and valid_pattern(
            item.get("pattern") or ""
        ):
            rules.append(Rule(item["kind"], item["pattern"], item["verdict"], item.get("reason") or "", item.get("id")))
    return tuple(rules)


def valid_pattern(pattern: str) -> bool:
    try:
        re.compile(pattern)
    except re.error:
        return False
    return 0 < len(pattern) <= 200
