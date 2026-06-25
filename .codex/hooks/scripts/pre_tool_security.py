import json
import re
import sys
from pathlib import PurePath
from typing import Any

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

PATH_TOKEN_RE = re.compile(r"[^\s\"'`;&|<>]+")


def _load_stdin() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _should_check(tool_name: str) -> bool:
    name = tool_name.lower()
    short_name = name.rsplit(".", 1)[-1]
    return name in FILE_ACCESS_TOOLS or short_name in FILE_ACCESS_TOOLS


def _should_check_git_paths(tool_name: str) -> bool:
    name = tool_name.lower()
    short_name = name.rsplit(".", 1)[-1]
    return name in MUTATING_FILE_TOOLS or short_name in MUTATING_FILE_TOOLS


def _matches_env_path(value: str) -> bool:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return False

    parts = [segment for segment in normalized.split("/") if segment]
    if not parts:
        return False

    normalized_parts = [PurePath(segment).name.lower() for segment in parts]
    if any(part in PROTECTED_ENV_PATH_PARTS for part in normalized_parts[:-1]):
        return True

    basename = normalized_parts[-1]
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

    if any(
        lowered == prefix[:-1] or lowered.startswith(prefix)
        for prefix in ALLOWED_GIT_PROJECT_PREFIXES
    ):
        return False

    parts = [segment for segment in lowered.split("/") if segment]
    return any(PurePath(segment).name.lower() == ".git" for segment in parts)


def _find_env_path(value: Any) -> str | None:
    if isinstance(value, dict):
        for item in value.values():
            match = _find_env_path(item)
            if match:
                return match
        return None

    if isinstance(value, list):
        for item in value:
            match = _find_env_path(item)
            if match:
                return match
        return None

    if not isinstance(value, str):
        return None

    if _matches_env_path(value):
        return value

    for token in PATH_TOKEN_RE.findall(value):
        if _matches_env_path(token):
            return token

    return None


def _find_protected_git_path(value: Any) -> str | None:
    if isinstance(value, dict):
        for item in value.values():
            match = _find_protected_git_path(item)
            if match:
                return match
        return None

    if isinstance(value, list):
        for item in value:
            match = _find_protected_git_path(item)
            if match:
                return match
        return None

    if not isinstance(value, str):
        return None

    if _matches_protected_git_path(value):
        return value

    for token in PATH_TOKEN_RE.findall(value):
        if _matches_protected_git_path(token):
            return token

    return None


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
    payload = _load_stdin()
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "")
    if not _should_check(tool_name):
        return 0

    tool_input = payload.get("tool_input") or payload.get("toolArgs") or {}
    blocked_git_path = (
        _find_protected_git_path(tool_input)
        if _should_check_git_paths(tool_name)
        else None
    )
    if blocked_git_path:
        _emit_git_block(blocked_git_path)
        return 0

    blocked_path = _find_env_path(tool_input)
    if blocked_path:
        _emit_block(blocked_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
