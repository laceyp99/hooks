from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Iterator
from pathlib import PurePath
from typing import Any

from agent_hooks.common import (
    first_matching_string,
    load_stdin_payload,
    normalize_tool_name,
)

PROTECTED_ENV_EXACT_NAMES = {
    ".env",
    ".envrc",
    ".secrets",
    "local.env",
    "secrets.env",
}

PROTECTED_ENV_PREFIXES = (
    ".env.",
    ".envrc.",
    ".secrets.",
)

PROTECTED_ENV_SUFFIXES = (
    ".env",
    ".secret",
    ".secrets",
)

PROTECTED_ENV_PATH_PARTS = {
    ".direnv",
}

FILE_ACCESS_TOOLS = {
    "apply_patch",
    "applypatch",
    "bash",
    "command_execution",
    "create_file",
    "delete_file",
    "edit",
    "edit_tool",
    "move_file",
    "read",
    "read_file",
    "rename",
    "shell",
    "shell_command",
    "write",
}

MUTATING_FILE_TOOLS = FILE_ACCESS_TOOLS - {
    "read_file",
    "bash",
    "shell",
    "shell_command",
    "command_execution",
}

ALLOWED_GIT_PROJECT_EXACT_NAMES = {
    ".gitattributes",
    ".gitignore",
}

ALLOWED_GIT_PROJECT_PREFIXES = (".github/",)

FILE_TARGET_FIELD_NAMES = {
    "destination",
    "destination_path",
    "dst",
    "file",
    "file_path",
    "filepath",
    "filename",
    "new_path",
    "old_path",
    "path",
    "paths",
    "source",
    "source_path",
    "src",
    "target",
    "target_path",
}

COMMAND_FIELD_NAMES = {
    "cmd",
    "command",
    "raw",
    "script",
}

PATCH_FIELD_NAMES = {
    "patch",
}

PATCH_TARGET_RE = re.compile(
    r"^\*{3} (?:Add|Delete|Update) File:\s*(.+?)\s*$|^\*{3} Move to:\s*(.+?)\s*$",
    re.MULTILINE,
)

SHELL_COMMAND_TOOLS = {
    "bash",
    "command_execution",
    "shell",
    "shell_command",
    "run_command",
}

PROTECTED_GIT_MUTATION_PATTERNS = (
    re.compile(r"(^|[;&|\r\n])\s*(?:sudo\s+)?rm\s+-[A-Za-z]*[rf][A-Za-z]*\b", re.IGNORECASE),
    re.compile(r"(^|[;&|\r\n])\s*rmdir\s+/s\s+/q\b", re.IGNORECASE),
    re.compile(r"(^|[;&|\r\n])\s*del(?:\s+/[A-Za-z]+)+\b", re.IGNORECASE),
    re.compile(r"(^|[;&|\r\n])\s*(?:sudo\s+)?Remove-Item\b", re.IGNORECASE),
    re.compile(r"(^|[;&|\r\n])\s*(?:sudo\s+)?Move-Item\b", re.IGNORECASE),
    re.compile(r"(^|[;&|\r\n])\s*(?:sudo\s+)?Rename-Item\b", re.IGNORECASE),
    re.compile(r"(^|[;&|\r\n])\s*(?:sudo\s+)?Copy-Item\b", re.IGNORECASE),
    re.compile(r"(^|[;&|\r\n])\s*(?:sudo\s+)?git\s+rm\b", re.IGNORECASE),
    re.compile(r"(^|[;&|\r\n])\s*(?:sudo\s+)?git\s+mv\b", re.IGNORECASE),
    re.compile(
        r"(^|[;&|\r\n])\s*(?:sudo\s+)?(?:Set-Content|Add-Content|Out-File|Clear-Content|New-Item)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(^|[;&|\r\n])\s*(?:echo|printf|type|cat)\b.*(?:>{1,2}|>>)", re.IGNORECASE),
    re.compile(r"(^|[;&|\r\n])\s*(?:sudo\s+)?(?:mv|cp|tee|tee-object)\b", re.IGNORECASE),
)


def _should_check(tool_name: str) -> bool:
    name, short_name = normalize_tool_name(tool_name)
    return name in FILE_ACCESS_TOOLS or short_name in FILE_ACCESS_TOOLS


def _should_check_git_paths(tool_name: str) -> bool:
    name, short_name = normalize_tool_name(tool_name)
    return name in MUTATING_FILE_TOOLS or short_name in MUTATING_FILE_TOOLS


def _matches_env_path(value: str) -> bool:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return False

    parts = [segment for segment in normalized.split("/") if segment]
    if not parts:
        return False

    normalized_parts = [PurePath(segment).name.lower() for segment in parts]
    basename = normalized_parts[-1]

    if basename in PROTECTED_ENV_PATH_PARTS:
        return True

    if any(part in PROTECTED_ENV_PATH_PARTS for part in normalized_parts[:-1]):
        return True

    if basename in PROTECTED_ENV_EXACT_NAMES:
        return True

    if basename.endswith(".example") or basename.endswith(".sample"):
        return False

    if any(basename.startswith(prefix) for prefix in PROTECTED_ENV_PREFIXES):
        return True

    return any(basename.endswith(suffix) for suffix in PROTECTED_ENV_SUFFIXES)


def _matches_protected_git_path(value: str) -> bool:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return False

    trimmed = normalized
    while trimmed.startswith("./"):
        trimmed = trimmed[2:]

    lowered = trimmed.lower()
    if not lowered:
        return False

    if lowered in ALLOWED_GIT_PROJECT_EXACT_NAMES:
        return False

    parts: list[str] = []
    for segment in lowered.split("/"):
        if not segment or segment == ".":
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(PurePath(segment).name.lower())

    if any(segment == ".git" for segment in parts):
        return True

    normalized_path = "/".join(parts)
    if any(
        normalized_path == prefix[:-1] or normalized_path.startswith(prefix)
        for prefix in ALLOWED_GIT_PROJECT_PREFIXES
    ):
        return False

    return False


def _iter_relevant_strings(value: Any, *, selected: bool = False) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in PATCH_FIELD_NAMES and isinstance(item, str):
                for match in PATCH_TARGET_RE.finditer(item):
                    target = match.group(1) or match.group(2)
                    if target:
                        yield target
                continue

            is_selected = normalized_key in FILE_TARGET_FIELD_NAMES | COMMAND_FIELD_NAMES
            if is_selected:
                yield from _iter_relevant_strings(item, selected=True)
        return

    if isinstance(value, list):
        if selected:
            for item in value:
                yield from _iter_relevant_strings(item, selected=True)
        return

    if selected and isinstance(value, str):
        yield value


def _first_matching_relevant_string(value: Any, predicate: Callable[[str], bool]) -> str | None:
    if isinstance(value, str):
        return first_matching_string(value, predicate)

    for item in _iter_relevant_strings(value):
        match = first_matching_string(item, predicate)
        if match:
            return match
    return None


def _find_env_path(value: Any) -> str | None:
    match = _first_matching_relevant_string(value, _matches_env_path)
    return match


def _find_protected_git_path(value: Any) -> str | None:
    match = _first_matching_relevant_string(value, _matches_protected_git_path)
    return match


def _matches_protected_git_mutation_command(value: str) -> bool:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return False

    if not first_matching_string(normalized, _matches_protected_git_path):
        return False

    return any(pattern.search(normalized) for pattern in PROTECTED_GIT_MUTATION_PATTERNS)


def _find_protected_git_mutation_command(value: Any) -> str | None:
    match = _first_matching_relevant_string(value, _matches_protected_git_mutation_command)
    return match


def _emit_block(path: str) -> None:
    payload = {
        "systemMessage": "Human must handle env-like secret files manually.",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"Human must handle env-like secret files manually; do not read or modify them. Blocked target: {path}",
        },
    }
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


def _emit_git_block(path: str) -> None:
    payload = {
        "systemMessage": "Human must handle protected Git internals manually.",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"Human must handle protected Git internals manually; do not write or move them. Blocked target: {path}",
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
    blocked_git_path = (
        _find_protected_git_path(tool_input) if _should_check_git_paths(tool_name) else None
    )
    if blocked_git_path:
        _emit_git_block(blocked_git_path)
        return 0

    name, short_name = normalize_tool_name(tool_name)
    if name in SHELL_COMMAND_TOOLS or short_name in SHELL_COMMAND_TOOLS:
        blocked_git_command = _find_protected_git_mutation_command(tool_input)
        if blocked_git_command:
            _emit_git_block(blocked_git_command)
            return 0

    blocked_path = _find_env_path(tool_input)
    if blocked_path:
        _emit_block(blocked_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
