from __future__ import annotations

import base64
import functools
import json
import os
import re
import stat
import sys
import time
from collections.abc import Callable, Iterator
from fnmatch import fnmatchcase
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
    "credentials.json",
    "id_rsa",
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
    ".pem",
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
    ".env.development",
    ".env.test",
    ".env.staging",
    ".env.production",
    ".env.production.local",
    ".env.development.local",
    ".env.test.local",
    ".env.staging.local",
    ".envrc",
    ".envrc.local",
    ".secrets",
    ".secrets.local",
    "credentials.json",
    "id_rsa",
    "local.env",
    "prod.env",
    "private.pem",
    "secrets.env",
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

STRING_LITERAL_RE = re.compile(r"(['\"])(.*?)\1")
STRING_CONCAT_RE = re.compile(r"(['\"])([^'\"\r\n]*)\1\s*\+\s*(['\"])([^'\"\r\n]*)\3")
PRINTF_FORMAT_RE = re.compile(
    r"(?i)\bprintf\s+(['\"])([^'\"]*)\1\s+(?:'([^']*)'|\"([^\"]*)\"|([^\s;|)&]+))"
)
ASSIGNMENT_RE = re.compile(r"(?i)(?<![a-z0-9_$])\$?([a-z_][a-z0-9_]*)\s*=\s*([^;|&\r\n]+)")
VARIABLE_RE = re.compile(r"\$\{?([a-z_][a-z0-9_]*)\}?", re.IGNORECASE)
OPAQUE_COMMAND_RE = re.compile(r"(?i)(?:^|[\s;&|])(?:iex|invoke-expression|start-process)\b")
# A launch can open a subshell or group, as in ``$(pwsh -e ...)`` or ``(pwsh -e ...)``.
POWERSHELL_INVOCATION_RE = re.compile(
    r"(?i)(?:^|[\s;&|({])(?:[\"'](?:[^\"']*[\\/])?|[^\s;&|(){}]*[\\/])?"
    r"(?:powershell|pwsh)(?:\.exe)?[\"']?(?=\s|$)"
)
# PowerShell takes ``-``, ``/`` and (pwsh) ``--`` option prefixes, and ``-ec`` as an alias.
POWERSHELL_ENCODED_FLAG_RE = re.compile(
    r"(?i)(?:^|\s)(?:--?|/)(?:e|ec|en|enc|enco|encod|encode|encoded|encodedc|encodedco|"
    r"encodedcom|encodedcomm|encodedcomma|encodedcomman|encodedcommand)\b"
)
POWERSHELL_SCRIPT_FLAG_RE = re.compile(
    r"(?i)(?:^|\s)(?:--?|/)(?:c|co|com|comm|comma|comman|command|f|fi|fil|file)\b"
)
COMMAND_SEGMENT_END_RE = re.compile(r"[;&|\r\n]")

# Characters that end a shell word. A quoted piece touching anything else is part of a larger
# word, as in ``.en""v`` or ``po''wershell``.
SHELL_WORD_BREAKS = frozenset(" \t\r\n;&|<>()")
QUOTED_PIECE_RE = re.compile(r"'([^'\r\n]*)'|\"([^\"\r\n]*)\"")
SHELL_ESCAPE_RE = re.compile(r"\\(?:\r?\n|(.))")
# Anchored at a word start: an unanchored ``[^...]*`` prefix rescans a long word from every
# character and turns one long word into quadratic work.
BRACE_WORD_RE = re.compile(
    r"(?<![^\s;&|<>()])[^\s;&|<>(){}]*(?<!\$)\{[^{}\s]*,[^{}\s]*\}[^\s;&|<>()]*"
)

# A check that runs past the harness timeout lets the call through, so a command that cannot be
# inspected within this budget is denied instead. The installed hook timeout is 30 s.
SHELL_CHECK_BUDGET_SECONDS = 10.0
BUDGET_EXCEEDED = "command too large to inspect in time"


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
    Callers split on ``/`` first, so a piece is already a bare name; plain string handling keeps
    this cheap, since a shell check calls it for every word.
    """
    names: list[str] = []
    for piece in segment.replace("\\", "/").split(":"):
        name = piece.rsplit("/", 1)[-1].lower().rstrip(" .")
        if name:
            names.append(name)
    return names


def _is_protected_env_name(name: str, *, suffixes: bool = True) -> bool:
    if name in PROTECTED_ENV_PATH_PARTS or name in PROTECTED_ENV_EXACT_NAMES:
        return True

    if name.endswith(".example") or name.endswith(".sample"):
        return False

    if any(name.startswith(prefix) for prefix in PROTECTED_ENV_PREFIXES):
        return True

    return suffixes and any(name.endswith(suffix) for suffix in PROTECTED_ENV_SUFFIXES)


def _matches_env_path(value: str, *, bare_suffixes: bool = True) -> bool:
    """Return whether ``value`` names a protected file or sits in a protected directory.

    With ``bare_suffixes=False`` the suffix rules (``*.env``, ``*.pem``) apply only to a value
    with a directory part, so a bare word in shell text such as ``process.env`` is not a file.
    """
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return False

    parts = [segment for segment in normalized.split("/") if segment]
    if not parts:
        return False

    suffixes = bare_suffixes or len(parts) > 1
    if any(_is_protected_env_name(name, suffixes=suffixes) for name in _segment_names(parts[-1])):
        return True

    return any(
        name in PROTECTED_ENV_PATH_PARTS
        for segment in parts[:-1]
        for name in _segment_names(segment)
    )


def _matches_env_path_pattern(value: str) -> bool:
    """Return whether a word in shell text names, or as a glob can select, a protected file.

    Suffix rules apply only to words with a directory part; an existing file is caught by its
    resolved path instead (see ``_find_resolved_shell_word``).
    """
    if _matches_env_path(value, bare_suffixes=False):
        return True

    normalized = value.strip().strip("\"'")
    if "=" in normalized:
        normalized = normalized.rsplit("=", 1)[-1]
    normalized = normalized.lstrip("@").replace("\\", "/").lower()
    if _matches_env_path(normalized, bare_suffixes=False):
        return True
    if not normalized or not any(char in normalized for char in "*?["):
        return False

    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return False

    protected_names = (
        ".env",
        ".env.local",
        ".env.production",
        ".envrc",
        ".envrc.local",
        ".secrets",
        ".secrets.local",
        "credentials.json",
        "id_rsa",
        "local.env",
        "prod.env",
        "private.pem",
        "secrets.env",
        "x.secret",
        "x.secrets",
    )
    if any(fnmatchcase(name, parts[-1]) for name in protected_names):
        return True
    return any(fnmatchcase(".direnv", part) for part in parts[:-1])


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


@functools.lru_cache(maxsize=1024)
def _canonical_directory(base: str | None) -> str | None:
    """Return the resolved, case-normalized directory ``base``.

    Cached because a shell check asks once per word. Each hook call is its own process, so the
    cache never outlives the filesystem state it describes.
    """
    if not base:
        return None
    try:
        return os.path.normcase(os.path.realpath(base))
    except (OSError, ValueError):
        return None


def _path_for_matching(resolved: str, cwd: str | None) -> str:
    """Return the part of ``resolved`` the name rules should judge.

    The ancestor rules ask whether a *target* sits in a protected directory, so they have to be
    applied to the path the tool reached for, not to the session's location. A session whose
    working directory is itself inside ``.direnv`` or ``.git`` would otherwise deny every
    relative path in it, including ordinary source files. Anything under the working directory is
    therefore judged by the part below it; anything outside is judged whole.
    """
    base_resolved = _canonical_directory(_base_directory(cwd))
    if not base_resolved:
        return resolved

    try:
        relative = os.path.relpath(resolved, base_resolved)
    except ValueError:
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


def _find_resolved_shell_word(word: str, cwd: str) -> str | None:
    """Resolve a word from shell text when it names something on disk.

    Most words in a command are not paths, and resolving each one is what makes a large command
    slow. Only a word that exists, or a path whose parent directory exists (a file about to be
    created), is resolved; any other word cannot reach a protected file through a link.
    """
    text = _strip_stream_suffixes(_strip_extended_prefix(word.strip()))
    base = _base_directory(cwd)
    if not text or base is None:
        return None

    try:
        path = text if os.path.isabs(text) else os.path.join(base, text)
        if os.path.lexists(path):
            return _find_resolved_env_target(word, cwd)
        if "/" not in text and "\\" not in text:
            return None
        # A file about to be created can only be redirected by its directory, and many words
        # share one directory, so the directory's resolution is cached.
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            return None
        parent = _canonical_directory(directory)
    except (OSError, ValueError):
        return None

    if parent is None:
        return None
    resolved = os.path.normcase(os.path.join(parent, os.path.basename(path)))
    if _matches_env_path(_path_for_matching(resolved, cwd)):
        return f"{word} (resolves to {resolved})"
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


def _static_shell_variables(command: str) -> dict[str, str]:
    """Resolve simple literal shell and PowerShell assignments without executing them."""
    variables: dict[str, str] = {}
    for match in ASSIGNMENT_RE.finditer(command):
        rhs = match.group(2).strip()
        literals = list(STRING_LITERAL_RE.finditer(rhs))
        if literals:
            separators = rhs
            for literal in reversed(literals):
                separators = separators[: literal.start()] + separators[literal.end() :]
            if not re.fullmatch(r"\s*(?:\+\s*)*", separators):
                continue
            value = "".join(literal.group(2) for literal in literals)
        elif not any(char.isspace() for char in rhs):
            value = rhs.strip("\"'")
        else:
            continue

        for _ in range(3):
            value = VARIABLE_RE.sub(
                lambda item: variables.get(item.group(1).lower(), item.group(0)), value
            )
        variables[match.group(1).lower()] = value
    return variables


def _expand_static_shell_variables(command: str) -> str:
    variables = _static_shell_variables(command)
    if not variables:
        return command
    return VARIABLE_RE.sub(
        lambda item: variables.get(item.group(1).lower(), item.group(0)), command
    )


def _has_powershell_encoded_launch(command: str) -> bool:
    """Check launch options before PowerShell's command or file argument."""
    for invocation in POWERSHELL_INVOCATION_RE.finditer(command):
        segment_end = COMMAND_SEGMENT_END_RE.search(command, invocation.end())
        arguments = command[invocation.end() : segment_end.start() if segment_end else None]
        script_flag = POWERSHELL_SCRIPT_FLAG_RE.search(arguments)
        if script_flag:
            arguments = arguments[: script_flag.start()]
        if POWERSHELL_ENCODED_FLAG_RE.search(arguments):
            return True
    return False


def _join_glued_quote(match: re.Match[str]) -> str:
    text = match.string
    start, end = match.start(), match.end()
    content = match.group(1) if match.group(1) is not None else match.group(2)
    glued = (start > 0 and text[start - 1] not in SHELL_WORD_BREAKS) or (
        end < len(text) and text[end] not in SHELL_WORD_BREAKS
    )
    return content if glued or not content else match.group(0)


def _join_shell_words(command: str) -> str:
    """Return ``command`` with each word spelled the way the shell will receive it.

    Quoted pieces glued into a larger word are joined (``.en""v``, ``po''wershell``), backslash
    escapes are dropped (``.e\\nv``), and ``{a,b}`` braces are expanded (``{.e,}nv``). A word that
    is quoted whole keeps its quotes, so ``rg 'iex' src`` still reads as data. Dropping escapes
    mangles Windows paths, so this form is checked in addition to the original, never instead.
    """
    joined = QUOTED_PIECE_RE.sub(_join_glued_quote, command)
    joined = SHELL_ESCAPE_RE.sub(lambda match: match.group(1) or "", joined)
    return BRACE_WORD_RE.sub(lambda match: " ".join(_expand_braces(match.group(0))), joined)


def _find_env_access_in_command(command: str, cwd: str | None = None) -> str | None:
    """Block protected names and matching globs wherever they appear in shell text.

    Each word is checked by name and, when ``cwd`` is known and the word exists on disk, by where
    it resolves. A check that would outrun ``SHELL_CHECK_BUDGET_SECONDS`` denies instead.
    """
    deadline = time.monotonic() + SHELL_CHECK_BUDGET_SECONDS
    expanded = _expand_static_shell_variables(command).replace("`", "")
    joined = _join_shell_words(expanded)
    texts = (expanded,) if joined == expanded else (expanded, joined)
    for text in texts:
        if OPAQUE_COMMAND_RE.search(text) or _has_powershell_encoded_launch(text):
            return "opaque dynamic command"

    for match in PRINTF_FORMAT_RE.finditer(expanded):
        argument = next(value for value in match.groups()[2:] if value is not None)
        formatted = match.group(2).replace("%s", argument, 1)
        if _matches_env_path_pattern(formatted):
            return formatted

    # Fold ``'a' + 'b'`` one level per pass. ``re.sub`` rebuilds the text, so later matches in
    # the same pass are never read at offsets from the text before an earlier fold.
    folded_hits: list[str] = []

    def fold(match: re.Match[str]) -> str:
        literal = match.group(2) + match.group(4)
        if not folded_hits and _matches_env_path_pattern(literal):
            folded_hits.append(literal)
        return "'" + literal + "'"

    for _ in range(4):
        folded = STRING_CONCAT_RE.sub(fold, expanded)
        if folded_hits:
            return folded_hits[0]
        if folded == expanded:
            break
        expanded = folded

    seen: set[str] = set()

    def check_word(word: str) -> str | None:
        if word in seen:
            return None
        seen.add(word)
        if time.monotonic() > deadline:
            return BUDGET_EXCEEDED
        if _matches_env_path_pattern(word):
            return word
        if cwd and _find_resolved_shell_word(word, cwd):
            return word
        return None

    for literal in STRING_LITERAL_RE.finditer(expanded):
        escaped = re.sub(
            r"\\x([0-9a-fA-F]{2})",
            lambda item: chr(int(item.group(1), 16)),
            literal.group(2),
        )
        escaped = re.sub(
            r"\\u([0-9a-fA-F]{4})",
            lambda item: chr(int(item.group(1), 16)),
            escaped,
        )
        for word in (escaped, *iter_string_tokens(escaped)):
            match = check_word(word)
            if match:
                return match

    if re.search(r"(?i)\b(?:base64|frombase64string)\b", expanded):
        for token in iter_string_tokens(expanded):
            if time.monotonic() > deadline:
                return BUDGET_EXCEEDED
            try:
                decoded = base64.b64decode(token, validate=True)
            except (ValueError, base64.binascii.Error):
                continue
            for encoding in ("utf-8", "utf-16-le"):
                try:
                    decoded_text = decoded.decode(encoding)
                except UnicodeDecodeError:
                    continue
                if any(
                    _matches_env_path_pattern(item.strip("(){}[],"))
                    for item in iter_string_tokens(decoded_text)
                ):
                    return "encoded protected path"

    for text in (expanded, joined):
        for token in iter_string_tokens(text):
            match = check_word(token.strip("(){}").split(")", 1)[0].rstrip(","))
            if match:
                return match

    return None


def _find_env_path_in_shell_payload(value: Any, cwd: str | None = None) -> str | None:
    """Env check for shell tools: command fields are gated, file-target fields are not.

    Command tokens are checked by name and against nearby links, independent of command name.
    A dedicated file-target field is resolved like any other.
    """
    for command in iter_command_strings(value):
        match = _find_env_access_in_command(command, cwd)
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

    # Dedicated file targets are checked by name and resolved path. Shell commands are also
    # checked for protected names wherever they appear, plus nearby links and opaque commands.
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
