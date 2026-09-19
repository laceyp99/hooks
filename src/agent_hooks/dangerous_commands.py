from __future__ import annotations

import json
import re
import sys
from typing import Any

from agent_hooks.common import (
    COMMAND_FIELD_NAMES,
    iter_field_strings,
    load_stdin_payload,
    normalize_tool_name,
)
from agent_hooks.security import _matches_protected_git_mutation_command

COMMAND_TOOLS = {
    "bash",
    "command_execution",
    "shell",
    "shell_command",
    "run_command",
}

DANGEROUS_COMMAND_PATTERNS = (
    re.compile(
        r"(^|[;&|\r\n])\s*(?:sudo\s+)?rm\s+-[A-Za-z]*[rf][A-Za-z]*\s+(?:--\s+)?(?:/|~|\$HOME|\.|\.\.)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(^|[;&|\r\n])\s*rmdir\s+/s\s+/q\s+(?:[A-Za-z]:\\|\\\\|%USERPROFILE%|%HOMEPATH%)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(^|[;&|\r\n])\s*del(?:\s+/[A-Za-z]+)+\s+(?:[A-Za-z]:\\|\\\\|%USERPROFILE%|%HOMEPATH%)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(^|[;&|\r\n])\s*(?:sudo\s+)?dd\s+.*\bof=(?:/dev/|\\\\\.\\PhysicalDrive)",
        re.IGNORECASE,
    ),
    # Match the real disk-formatting commands (`mkfs`, `mkfs.ext4`, `format`, `format.com`)
    # as complete tokens. PowerShell presentation cmdlets such as `Format-Table` contain a
    # hyphen immediately after the word and must not be treated as disk formatting.
    re.compile(
        r"(^|[;&|\r\n])\s*(?:sudo\s+)?(?:mkfs(?:\.[A-Za-z0-9]+)?|format(?:\.(?:com|exe))?)"
        r"(?=\s|[;&|\r\n]|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:bash|sh|zsh|pwsh|powershell)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(^|[;&|\r\n])\s*(?:sudo\s+)?ch(?:mod|own)\s+-R\b", re.IGNORECASE),
)


def _should_check(tool_name: str) -> bool:
    name, short_name = normalize_tool_name(tool_name)
    return name in COMMAND_TOOLS or short_name in COMMAND_TOOLS


def _normalize_command(value: str) -> str:
    return "\n".join(" ".join(line.split()) for line in value.strip().splitlines())


def _matches_dangerous_command(value: str) -> bool:
    normalized = _normalize_command(value)
    if not normalized:
        return False

    return any(pattern.search(normalized) for pattern in DANGEROUS_COMMAND_PATTERNS)


def _is_blocked_command(value: str) -> bool:
    return _matches_dangerous_command(value) or _matches_protected_git_mutation_command(value)


def _find_dangerous_command(value: Any) -> str | None:
    """Return the first executable command string in the payload that must be blocked.

    Only command fields (``command``, ``cmd``, ``script``, ``raw``) are inspected. Other strings
    in the payload are inert data and are never treated as executable intent.
    """
    if isinstance(value, str):
        return value if _is_blocked_command(value) else None

    for command in iter_field_strings(value, COMMAND_FIELD_NAMES, include_patch_targets=False):
        if _is_blocked_command(command):
            return command

    return None


def _emit_block(command: str) -> None:
    payload = {
        "systemMessage": "Human must review dangerous shell commands manually.",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Human must review dangerous shell commands manually; blocked command: {command}"
            ),
        },
    }
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


def main() -> int:
    payload = load_stdin_payload()
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "")
    if not _should_check(tool_name):
        return 0

    tool_input = payload.get("tool_input") or payload.get("toolArgs") or {}
    blocked_command = _find_dangerous_command(tool_input)
    if blocked_command:
        _emit_block(blocked_command)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
