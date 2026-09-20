from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import PurePath
from typing import Any

from agent_hooks.common import (
    COMMAND_FIELD_NAMES,
    FILE_TARGET_FIELD_NAMES,
    first_matching_string,
    iter_command_strings,
    iter_field_strings,
    iter_string_tokens,
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
    "multiedit",
    "notebookedit",
    "powershell",
    "pwsh",
    "read",
    "read_file",
    "rename",
    "shell",
    "shell_command",
    "write",
}

MUTATING_FILE_TOOLS = FILE_ACCESS_TOOLS - {
    "read",
    "read_file",
    "bash",
    "shell",
    "shell_command",
    "command_execution",
    "powershell",
    "pwsh",
}

ALLOWED_GIT_PROJECT_EXACT_NAMES = {
    ".gitattributes",
    ".gitignore",
}

SHELL_COMMAND_TOOLS = {
    "bash",
    "command_execution",
    "powershell",
    "pwsh",
    "shell",
    "shell_command",
    "run_command",
}

# Programs that read, write, move, or delete a file named on their command line. A protected
# name that appears without one of these is being *talked about* rather than touched: a commit
# message, a PR body, a grep pattern. The git rule already gates on a mutation verb this way;
# without the same gate here, `git commit -m "fix .env loading"` was denied.
ENV_ACCESS_COMMANDS = frozenset(
    {
        ".",
        "add-content",
        "awk",
        "bat",
        "cat",
        "clear-content",
        "code",
        "copy-item",
        "cp",
        "del",
        "emacs",
        "erase",
        "gc",
        "get-content",
        "head",
        "install",
        "less",
        "ln",
        "more",
        "move-item",
        "mv",
        "nano",
        "new-item",
        "notepad",
        "od",
        "out-file",
        "remove-item",
        "rename-item",
        "rm",
        "rmdir",
        "rsync",
        "scp",
        "sed",
        "set-content",
        "source",
        "strings",
        "subl",
        "tail",
        "tee",
        "tee-object",
        "touch",
        "type",
        "vi",
        "vim",
        "xxd",
    }
)

# Shells and interpreters carry another command line inside an argument this hook cannot parse.
# Treating them as access verbs keeps `bash -c "cat <file>"` and `python -c "open('<file>')"`
# denied on the name alone, which is what they did before the access gate existed.
INTERPRETER_COMMANDS = frozenset(
    {
        "bash",
        "bun",
        "cmd",
        "dash",
        "deno",
        "fish",
        "irb",
        "ksh",
        "node",
        "perl",
        "php",
        "powershell",
        "pwsh",
        "py",
        "python",
        "python2",
        "python3",
        "ruby",
        "sh",
        "zsh",
    }
)

# ``git`` alone is not an access verb, or every commit message naming a protected file would be
# denied. Only these subcommands touch the named file.
GIT_ACCESS_SUBCOMMANDS = frozenset({"add", "checkout", "mv", "restore", "rm", "stage"})

# Git global options that consume the next word, so the subcommand search can step past them.
# Options spelled ``--flag=value`` carry their value already and need no special handling.
GIT_VALUE_FLAGS = frozenset(
    {
        "-C",
        "-c",
        "--config-env",
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--work-tree",
    }
)

# Splits a command line into the segments a shell would run separately.
COMMAND_SEGMENT_RE = re.compile(r"[;&|\r\n]+")

# The file a redirect writes to is the token right after the operator. Everything else on an
# ``echo ... >> file`` line is data, so ``echo ".env" >> .gitignore`` writes .gitignore only.
REDIRECT_TARGET_RE = re.compile(r"\d*>>?\s*([^\s;&|<>]+)")

# Leading ``VAR=value`` assignments and ``sudo`` sit in front of the real program name.
ENV_ASSIGNMENT_RE = re.compile(r"^\w+=")

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

    # ``.github`` and other dot-prefixed project directories never equal ``.git`` after
    # normalization, so no allowlist is needed beyond the exact names above.
    return any(segment == ".git" for segment in parts)


RELEVANT_FIELD_NAMES = FILE_TARGET_FIELD_NAMES | COMMAND_FIELD_NAMES


def _first_matching_relevant_string(value: Any, predicate: Callable[[str], bool]) -> str | None:
    if isinstance(value, str):
        return first_matching_string(value, predicate)

    for item in iter_field_strings(value, RELEVANT_FIELD_NAMES):
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


def _normalize_program_token(token: str) -> str:
    stripped = token.strip("\"'")
    if not stripped:
        return ""

    name = PurePath(stripped.replace("\\", "/")).name.lower()
    if name.endswith(".exe"):
        name = name[: -len(".exe")]

    return name


def _segment_words(segment: str) -> list[str]:
    """Return a segment's words with leading ``VAR=value`` assignments and ``sudo`` dropped.

    Stripping the prefixes here rather than in the caller keeps the program and its subcommand
    adjacent, so ``sudo git add <file>`` reads the same as ``git add <file>``.
    """
    words: list[str] = []
    for token in segment.split():
        if not words and ENV_ASSIGNMENT_RE.match(token):
            continue

        normalized = _normalize_program_token(token)
        if not words and normalized == "sudo":
            continue

        if not normalized and not words:
            continue

        words.append(token)

    return words


def _segment_program(segment: str) -> str:
    """Return the program a shell segment runs, lowercased and stripped of path and suffix."""
    words = _segment_words(segment)
    return _normalize_program_token(words[0]) if words else ""


def _git_subcommand(words: list[str]) -> str:
    """Return the subcommand in ``git [global options] <subcommand> ...``.

    Global options have to be stepped over, and the ones taking a separate value take the word
    after them with it, or ``git -C . add <file>`` would read ``.`` as the subcommand.
    """
    index = 1
    while index < len(words):
        word = words[index]
        if not word.startswith("-"):
            return _normalize_program_token(word)

        takes_value = word in GIT_VALUE_FLAGS
        index += 2 if takes_value else 1

    return ""


def _segment_accesses_files(segment: str) -> bool:
    words = _segment_words(segment)
    if not words:
        return False

    program = _normalize_program_token(words[0])
    if not program:
        return False

    if program in INTERPRETER_COMMANDS:
        # An interpreter's argument is another command line this hook cannot parse. Reading the
        # whole segment keeps `bash -c "cat <file>"` denied; narrowing it would open a bypass.
        return True

    if program == "git":
        return _git_subcommand(words) in GIT_ACCESS_SUBCOMMANDS

    return program in ENV_ACCESS_COMMANDS


def _find_env_access_in_command(command: str) -> str | None:
    """Return the protected name this command line actually touches.

    A segment whose program reads or writes files puts every name on it in reach. Any other
    segment only touches its redirect targets, so ``echo ".env" >> .gitignore`` is an ordinary
    append and ``echo x >> .env`` is not.
    """
    for segment in COMMAND_SEGMENT_RE.split(command):
        if not segment.strip():
            continue

        if _segment_accesses_files(segment):
            # Report the token, not the whole segment, so the deny reason names the file.
            for token in iter_string_tokens(segment):
                if _matches_env_path(token):
                    return token
            continue

        for target in REDIRECT_TARGET_RE.findall(segment):
            match = first_matching_string(target.strip("\"'"), _matches_env_path)
            if match:
                return match

    return None


def _find_env_path_in_shell_payload(value: Any) -> str | None:
    """Env check for shell tools: command fields are gated, file-target fields are not."""
    for command in iter_command_strings(value):
        match = _find_env_access_in_command(command)
        if match:
            return match

    if isinstance(value, str):
        return None

    for item in iter_field_strings(value, FILE_TARGET_FIELD_NAMES):
        match = first_matching_string(item, _matches_env_path)
        if match:
            return match

    return None


def _matches_protected_git_mutation_command(value: str) -> bool:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return False

    if not first_matching_string(normalized, _matches_protected_git_path):
        return False

    return any(pattern.search(normalized) for pattern in PROTECTED_GIT_MUTATION_PATTERNS)


def _find_protected_git_mutation_command(value: Any) -> str | None:
    # Only command fields can carry a mutation.
    for item in iter_command_strings(value):
        match = first_matching_string(item, _matches_protected_git_mutation_command)
        if match:
            return match
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
    is_shell = name in SHELL_COMMAND_TOOLS or short_name in SHELL_COMMAND_TOOLS
    if is_shell:
        blocked_git_command = _find_protected_git_mutation_command(tool_input)
        if blocked_git_command:
            _emit_git_block(blocked_git_command)
            return 0

    # A tool that names its target in a dedicated field is denied on the name alone. A shell
    # command is denied only when it actually reads or writes the file, because everything else
    # on a command line is text the command carries rather than a file it touches.
    blocked_path = (
        _find_env_path_in_shell_payload(tool_input) if is_shell else _find_env_path(tool_input)
    )
    if blocked_path:
        _emit_block(blocked_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
