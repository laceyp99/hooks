from __future__ import annotations

import json
import os
import re
import stat
import sys
from collections.abc import Callable, Iterator
from fnmatch import fnmatchcase
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


# Content-search tools. They return file contents, so a search aimed at a protected file reads
# it. Glob is deliberately absent: it returns paths, never contents.
GREP_TOOLS = frozenset({"grep"})

# Search-tool fields holding a filename glob: Claude Code's Grep says ``glob``, OpenCode's grep
# says ``include``. The ``pattern`` field is the regex being searched for and is never a target.
GREP_GLOB_FIELD_NAMES = frozenset({"glob", "include"})

# Claude Code names MCP tools ``mcp__<server>__<tool>``.
MCP_TOOL_PREFIX = "mcp__"

# Words in an MCP tool's own name that mark it as changing files, so the Git-internals rule
# applies to it. Only the tool part is inspected, so a server called ``editor`` does not make
# every one of its tools mutating.
MCP_MUTATION_WORDS = ("write", "edit", "create", "move", "delete", "rename")

# Names a search glob is tried against. A glob that selects any of these, but none of the
# ordinary names below, is aimed at a secret file.
PROTECTED_GLOB_SAMPLES = (
    ".env",
    ".env.local",
    ".env.production",
    ".envrc",
    "prod.env",
    "x.secret",
    "x.secrets",
    ".direnv/x",
)

# Ordinary names that a broad glob such as ``*`` or ``**/*`` also selects. A broad search is not
# aimed at a secret file, and denying every one of them would make the tool useless.
ORDINARY_GLOB_SAMPLES = ("main.py", "readme.md", "src/app.ts", "notes.txt")

# Brace expansion is bounded so a pathological glob cannot make the hook spin.
MAX_GLOB_ALTERNATIVES = 64

GLOB_BRACE_RE = re.compile(r"\{([^{}]*)\}")

# ``\\?\C:\...`` and ``\\.\C:\...`` name the same file as ``C:\...``, and ``\\?\UNC\srv\...`` is
# ``\\srv\...``. Only the drive and UNC forms are unwrapped; other device paths are left alone.
EXTENDED_UNC_PREFIX_RE = re.compile(r"^[\\/]{2}[?.][\\/]UNC[\\/]", re.IGNORECASE)
EXTENDED_DRIVE_PREFIX_RE = re.compile(r"^[\\/]{2}[?.][\\/](?=[A-Za-z]:)")

DRIVE_RE = re.compile(r"^[A-Za-z]:")
PATH_SEPARATOR_RE = re.compile(r"([\\/])")

# A hard-link lookup reads at most this many entries per directory, so a huge directory costs a
# bounded amount rather than a walk.
MAX_HARD_LINK_SCAN_ENTRIES = 4096


def _is_grep_tool(tool_name: str) -> bool:
    name, short_name = normalize_tool_name(tool_name)
    return name in GREP_TOOLS or short_name in GREP_TOOLS


def _is_mcp_tool(tool_name: str) -> bool:
    return tool_name.lower().startswith(MCP_TOOL_PREFIX)


def _is_mutating_mcp_tool(tool_name: str) -> bool:
    if not _is_mcp_tool(tool_name):
        return False

    tool_part = tool_name.lower()[len(MCP_TOOL_PREFIX) :].split("__", 1)[-1]
    return any(word in tool_part for word in MCP_MUTATION_WORDS)


def _should_check(tool_name: str) -> bool:
    name, short_name = normalize_tool_name(tool_name)
    if name in FILE_ACCESS_TOOLS or short_name in FILE_ACCESS_TOOLS:
        return True
    return _is_grep_tool(tool_name) or _is_mcp_tool(tool_name)


def _should_check_git_paths(tool_name: str) -> bool:
    name, short_name = normalize_tool_name(tool_name)
    if name in MUTATING_FILE_TOOLS or short_name in MUTATING_FILE_TOOLS:
        return True
    return _is_mutating_mcp_tool(tool_name)


def _segment_names(segment: str) -> list[str]:
    """Return the names one path segment can stand for, lowercased.

    NTFS reads ``name:stream`` and ``name::$DATA`` as the file ``name``, and Win32 drops trailing
    dots and spaces, so ``.env::$DATA`` and ``.env.`` both open ``.env``. Every colon-separated
    piece is a candidate rather than only the first, because ``host:.env`` in an ``scp`` argument
    names the remote ``.env``. A drive letter ``C:`` yields ``c``, which matches nothing.
    """
    names: list[str] = []
    for piece in segment.split(":"):
        name = PurePath(piece).name.lower().rstrip(" .") if piece else ""
        if name:
            names.append(name)
    return names


def _is_protected_env_name(name: str) -> bool:
    if name in PROTECTED_ENV_PATH_PARTS or name in PROTECTED_ENV_EXACT_NAMES:
        return True

    if name.endswith(".example") or name.endswith(".sample"):
        return False

    if any(name.startswith(prefix) for prefix in PROTECTED_ENV_PREFIXES):
        return True

    return any(name.endswith(suffix) for suffix in PROTECTED_ENV_SUFFIXES)


def _matches_env_path(value: str) -> bool:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return False

    parts = [segment for segment in normalized.split("/") if segment]
    if not parts:
        return False

    if any(_is_protected_env_name(name) for name in _segment_names(parts[-1])):
        return True

    return any(
        name in PROTECTED_ENV_PATH_PARTS
        for segment in parts[:-1]
        for name in _segment_names(segment)
    )


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

    parts: list[list[str]] = []
    for segment in lowered.split("/"):
        if not segment or segment == ".":
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(_segment_names(segment))

    # ``.github`` and other dot-prefixed project directories never equal ``.git`` after
    # normalization, so no allowlist is needed beyond the exact names above.
    return any(".git" in names for names in parts)


RELEVANT_FIELD_NAMES = FILE_TARGET_FIELD_NAMES | COMMAND_FIELD_NAMES


def _first_matching_relevant_string(value: Any, predicate: Callable[[str], bool]) -> str | None:
    if isinstance(value, str):
        return first_matching_string(value, predicate)

    for item in iter_field_strings(value, RELEVANT_FIELD_NAMES):
        match = first_matching_string(item, predicate)
        if match:
            return match
    return None


def _strip_extended_prefix(path: str) -> str:
    """Unwrap ``\\\\?\\C:\\x`` to ``C:\\x`` and ``\\\\?\\UNC\\srv\\x`` to ``\\\\srv\\x``."""
    unc = EXTENDED_UNC_PREFIX_RE.match(path)
    if unc:
        return "\\\\" + path[unc.end() :]

    drive = EXTENDED_DRIVE_PREFIX_RE.match(path)
    if drive:
        return path[drive.end() :]

    return path


def _strip_stream_suffixes(path: str) -> str:
    """Drop NTFS stream suffixes (``:name``, ``::$DATA``) from every segment of ``path``.

    A leading drive letter is kept whole, so ``C:\\x\\.env:s`` becomes ``C:\\x\\.env``.
    """
    drive = ""
    rest = path
    if DRIVE_RE.match(path):
        drive, rest = path[:2], path[2:]

    if ":" not in rest:
        return path

    pieces = PATH_SEPARATOR_RE.split(rest)
    return drive + "".join(piece.split(":", 1)[0] for piece in pieces)


def _base_directory(cwd: str | None) -> str | None:
    if cwd:
        return cwd
    try:
        return os.getcwd()
    except OSError:
        return None


def _resolve_target(value: str, cwd: str | None) -> str | None:
    """Return the canonical on-disk form of a file-target string, or None.

    A relative path is taken against the hook payload's ``cwd``. ``realpath`` follows symlinks and
    junctions and, for paths that exist, expands 8.3 short names; for a path that does not exist
    yet it still resolves the deepest existing parent, so a new file inside a linked directory is
    seen where it will really land. The result is case-normalized. Anything that cannot be
    resolved returns None and the caller keeps the name-only verdict: this must never raise.
    """
    text = value.strip()
    if not text or "\0" in text:
        return None

    text = _strip_stream_suffixes(_strip_extended_prefix(text))
    if not text:
        return None

    try:
        if not os.path.isabs(text):
            base = _base_directory(cwd)
            if base is None:
                return None
            text = os.path.join(base, text)
        resolved = os.path.realpath(text)
    except (OSError, ValueError):
        return None

    return os.path.normcase(_strip_extended_prefix(resolved))


def _path_for_matching(resolved: str, cwd: str | None) -> str:
    """Return the part of ``resolved`` the name rules should judge.

    The ancestor rules ask whether a *target* sits in a protected directory, so they have to be
    applied to the path the tool reached for, not to the session's location. A session whose
    working directory is itself inside ``.direnv`` or ``.git`` would otherwise deny every
    relative path in it, including ordinary source files. Anything under the working directory is
    therefore judged by the part below it; anything outside is judged whole.
    """
    base = _base_directory(cwd)
    if not base:
        return resolved

    try:
        base_resolved = os.path.normcase(os.path.realpath(base))
        relative = os.path.relpath(resolved, base_resolved)
    except (OSError, ValueError):
        return resolved

    if relative.startswith(os.pardir) or os.path.isabs(relative):
        return resolved

    return relative


def _find_hard_link_twin(resolved: str, cwd: str | None) -> str | None:
    """Return a protected-named file that ``resolved`` is a hard link to, if one is nearby.

    A hard link is just another name for the same file, so ``notes.txt`` can read ``.env`` while
    matching nothing by name. Only files with more than one link are looked at, and only the
    protected names in the target's own directory and in ``cwd``: a bounded, non-recursive scan
    rather than a search of the disk.
    """
    try:
        info = os.stat(resolved)
    except (OSError, ValueError):
        return None

    if not stat.S_ISREG(info.st_mode) or info.st_nlink <= 1:
        return None

    directories = [os.path.dirname(resolved)]
    base = _base_directory(cwd)
    if base:
        directories.append(base)

    seen: set[str] = set()
    for directory in directories:
        key = os.path.normcase(os.path.abspath(directory))
        if key in seen:
            continue
        seen.add(key)

        try:
            with os.scandir(directory) as entries:
                for index, entry in enumerate(entries):
                    if index >= MAX_HARD_LINK_SCAN_ENTRIES:
                        break
                    if not _matches_env_path(entry.name):
                        continue
                    try:
                        if os.path.samefile(entry.path, resolved):
                            return entry.path
                    except (OSError, ValueError):
                        continue
        except (OSError, ValueError):
            continue

    return None


def _find_resolved_env_target(value: str, cwd: str | None) -> str | None:
    """Return a deny target when ``value`` resolves to, or hard-links, a protected file."""
    resolved = _resolve_target(value, cwd)
    if resolved is None:
        return None

    if _matches_env_path(_path_for_matching(resolved, cwd)):
        return f"{value} (resolves to {resolved})"

    twin = _find_hard_link_twin(resolved, cwd)
    if twin:
        return f"{value} (hard link to {twin})"

    return None


def _find_resolved_git_target(value: str, cwd: str | None) -> str | None:
    """Return a deny target when ``value`` resolves inside a ``.git`` directory."""
    resolved = _resolve_target(value, cwd)
    if resolved is not None and _matches_protected_git_path(_path_for_matching(resolved, cwd)):
        return f"{value} (resolves to {resolved})"
    return None


def _iter_file_targets(value: Any) -> Iterator[str]:
    """Yield the file-target strings of a payload: target fields and apply_patch headers."""
    if isinstance(value, str):
        return
    yield from iter_field_strings(value, FILE_TARGET_FIELD_NAMES)


def _expand_braces(pattern: str) -> list[str]:
    """Expand ``{a,b}`` alternatives in a glob, innermost first, up to a fixed bound."""
    results: list[str] = []
    pending = [pattern]
    while pending and len(results) < MAX_GLOB_ALTERNATIVES:
        current = pending.pop()
        match = GLOB_BRACE_RE.search(current)
        if match is None:
            results.append(current)
            continue

        head, tail = current[: match.start()], current[match.end() :]
        for option in match.group(1).split(","):
            if len(pending) + len(results) >= MAX_GLOB_ALTERNATIVES:
                break
            pending.append(head + option + tail)

    return results


def _glob_candidates(alternative: str) -> set[str]:
    """Return the forms of one glob alternative worth testing against sample names.

    ``**/`` and ``./`` prefixes match at any depth, so they are dropped; the last segment alone
    is also tried, so ``config/.env`` counts as aimed at ``.env``.
    """
    pattern = alternative.strip().replace("\\", "/").lower()
    candidates = {pattern}

    trimmed = pattern
    while True:
        for prefix in ("**/", "./", "/"):
            if trimmed.startswith(prefix):
                trimmed = trimmed[len(prefix) :]
                break
        else:
            break
    candidates.add(trimmed)

    if "/" in trimmed.rstrip("/"):
        candidates.add(trimmed.rstrip("/").rsplit("/", 1)[-1])

    return {candidate for candidate in candidates if candidate}


def _glob_targets_protected_file(glob: str) -> bool:
    """Return True when a search glob selects a secret file rather than files in general.

    A negated glob (``!*.env``) excludes files and never counts. A glob that matches ordinary
    names too, such as ``*`` or ``**/*.*``, is a broad search and is allowed. Templates such as
    ``.env.example`` are not among the protected samples, so a glob naming only them is allowed.
    """
    for alternative in _expand_braces(glob.strip()):
        if not alternative or alternative.startswith("!"):
            continue

        for candidate in _glob_candidates(alternative):
            if any(fnmatchcase(sample, candidate) for sample in ORDINARY_GLOB_SAMPLES):
                continue
            if any(fnmatchcase(sample, candidate) for sample in PROTECTED_GLOB_SAMPLES):
                return True

    return False


def _find_protected_glob(value: Any) -> str | None:
    if isinstance(value, str):
        return None

    for glob in iter_field_strings(value, GREP_GLOB_FIELD_NAMES, include_patch_targets=False):
        if _glob_targets_protected_file(glob):
            return glob
    return None


def _find_env_path(value: Any, cwd: str | None = None, tool_name: str = "") -> str | None:
    """Env check for tools that name their target in a field.

    The name check runs first and is authoritative on its own. File-target strings are then
    resolved on disk, so a symlink, junction, short name, stream suffix, or hard link that leads
    to a protected file is caught too. MCP tools are narrowed to their file-target fields, since
    an arbitrary MCP string field is data rather than a command line. A search tool's glob is
    checked for aiming at a secret file; its regex pattern is not.
    """
    if _is_mcp_tool(tool_name):
        match = None
        for item in _iter_file_targets(value):
            match = first_matching_string(item, _matches_env_path)
            if match:
                break
    else:
        match = _first_matching_relevant_string(value, _matches_env_path)
    if match:
        return match

    for target in _iter_file_targets(value):
        match = _find_resolved_env_target(target, cwd)
        if match:
            return match

    if _is_grep_tool(tool_name):
        return _find_protected_glob(value)

    return None


def _find_protected_git_path(value: Any, cwd: str | None = None, tool_name: str = "") -> str | None:
    if _is_mcp_tool(tool_name):
        match = None
        for item in _iter_file_targets(value):
            match = first_matching_string(item, _matches_protected_git_path)
            if match:
                break
    else:
        match = _first_matching_relevant_string(value, _matches_protected_git_path)
    if match:
        return match

    for target in _iter_file_targets(value):
        match = _find_resolved_git_target(target, cwd)
        if match:
            return match

    return None


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


def _find_env_path_in_shell_payload(value: Any, cwd: str | None = None) -> str | None:
    """Env check for shell tools: command fields are gated, file-target fields are not.

    Command tokens are matched by name only; resolving every word of a command line would be
    slow and mostly meaningless. A dedicated file-target field is resolved like any other.
    """
    for command in iter_command_strings(value):
        match = _find_env_access_in_command(command)
        if match:
            return match

    for item in _iter_file_targets(value):
        match = first_matching_string(item, _matches_env_path)
        if match:
            return match

    for item in _iter_file_targets(value):
        match = _find_resolved_env_target(item, cwd)
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


def _block_response(path: str) -> dict[str, Any]:
    return {
        "systemMessage": "Human must handle env-like secret files manually.",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"Human must handle env-like secret files manually; do not read or modify them. Blocked target: {path}",
        },
    }


def _git_block_response(path: str) -> dict[str, Any]:
    return {
        "systemMessage": "Human must handle protected Git internals manually.",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"Human must handle protected Git internals manually; do not write or move them. Blocked target: {path}",
        },
    }


def evaluate(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return the PreToolUse deny response for ``payload``, or None to allow the call."""
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "")
    if not _should_check(tool_name):
        return None

    tool_input = payload.get("tool_input") or payload.get("toolArgs") or {}
    cwd = str(payload.get("cwd") or "")
    blocked_git_path = (
        _find_protected_git_path(tool_input, cwd, tool_name)
        if _should_check_git_paths(tool_name)
        else None
    )
    if blocked_git_path:
        return _git_block_response(blocked_git_path)

    name, short_name = normalize_tool_name(tool_name)
    is_shell = name in SHELL_COMMAND_TOOLS or short_name in SHELL_COMMAND_TOOLS
    if is_shell:
        blocked_git_command = _find_protected_git_mutation_command(tool_input)
        if blocked_git_command:
            return _git_block_response(blocked_git_command)

    # A tool that names its target in a dedicated field is denied on the name, or on the path it
    # resolves to (links, junctions, short names, stream suffixes). A shell
    # command is denied only when it actually reads or writes the file, because everything else
    # on a command line is text the command carries rather than a file it touches.
    blocked_path = (
        _find_env_path_in_shell_payload(tool_input, cwd)
        if is_shell
        else _find_env_path(tool_input, cwd, tool_name)
    )
    if blocked_path:
        return _block_response(blocked_path)

    return None


def main() -> int:
    """Run these rules alone as a hook. The installed entry point is ``agent_hooks.dispatch``."""
    response = evaluate(load_stdin_payload())
    if response is not None:
        json.dump(response, sys.stdout)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
