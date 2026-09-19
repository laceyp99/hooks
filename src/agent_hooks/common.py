from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Iterator
from typing import Any

PATH_TOKEN_RE = re.compile(r"[^\s\"'`;&|<>]+")

# Payload fields that name a file the tool is about to read, write, move, or delete.
FILE_TARGET_FIELD_NAMES = frozenset(
    {
        "destination",
        "destination_path",
        "dst",
        "file",
        "file_path",
        "filepath",
        "filename",
        "new_path",
        "notebook_path",
        "old_path",
        "path",
        "paths",
        "source",
        "source_path",
        "src",
        "target",
        "target_path",
    }
)

# Payload fields that carry an executable shell command.
COMMAND_FIELD_NAMES = frozenset(
    {
        "cmd",
        "command",
        "raw",
        "script",
    }
)

# Payload fields that carry an apply_patch style document. Only the file headers inside the
# patch name real targets; the patch body is inert data.
PATCH_FIELD_NAMES = frozenset({"patch"})

PATCH_TARGET_RE = re.compile(
    r"^\*{3} (?:Add|Delete|Update) File:\s*(.+?)\s*$|^\*{3} Move to:\s*(.+?)\s*$",
    re.MULTILINE,
)


def load_stdin_payload() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def normalize_tool_name(tool_name: str) -> tuple[str, str]:
    name = tool_name.lower()
    return name, name.rsplit(".", 1)[-1]


def iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)
        return

    if isinstance(value, list):
        for item in value:
            yield from iter_strings(item)
        return

    if isinstance(value, str):
        yield value


def iter_string_tokens(value: str) -> Iterator[str]:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return

    tokens = PATH_TOKEN_RE.findall(normalized)
    if tokens:
        yield from tokens
    else:
        yield normalized


def iter_patch_targets(patch: str) -> Iterator[str]:
    for match in PATCH_TARGET_RE.finditer(patch):
        target = match.group(1) or match.group(2)
        if target:
            yield target


def iter_field_strings(
    value: Any,
    field_names: frozenset[str] | set[str],
    *,
    include_patch_targets: bool = True,
    selected: bool = False,
) -> Iterator[str]:
    """Yield strings that live under the selected payload fields.

    Only dictionary keys in ``field_names`` (case-insensitive) are inspected. Strings nested in
    lists or dictionaries beneath a selected key are yielded, while everything else is ignored so
    that inert data such as documentation, metadata, or patch bodies cannot masquerade as a
    command or file target. Patch documents contribute only their file headers.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).lower()
            if include_patch_targets and normalized_key in PATCH_FIELD_NAMES:
                if isinstance(item, str):
                    yield from iter_patch_targets(item)
                continue

            if selected or normalized_key in field_names:
                yield from iter_field_strings(
                    item,
                    field_names,
                    include_patch_targets=include_patch_targets,
                    selected=True,
                )
        return

    if isinstance(value, list):
        if selected:
            for item in value:
                yield from iter_field_strings(
                    item,
                    field_names,
                    include_patch_targets=include_patch_targets,
                    selected=True,
                )
        return

    if selected and isinstance(value, str):
        yield value


def _iter_command_values(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
        return

    if isinstance(value, list):
        parts = [item for item in value if isinstance(item, str)]
        if len(parts) > 1:
            yield " ".join(parts)
        yield from parts
        return

    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_command_values(item)


def iter_command_strings(value: Any) -> Iterator[str]:
    """Yield executable command strings found under command fields.

    A list under a command field is an argv vector, as emitted by Codex's ``shell`` tool. Its
    string elements are joined with single spaces and yielded first so patterns written for a
    command line see the program and its arguments together; each element is then yielded on
    its own so a script wrapped in ``["bash", "-lc", "..."]`` is still inspected verbatim.
    """
    if isinstance(value, str):
        yield value
        return

    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in COMMAND_FIELD_NAMES:
                yield from _iter_command_values(item)


def first_matching_string(value: Any, predicate: Callable[[str], bool]) -> str | None:
    for item in iter_strings(value):
        if predicate(item):
            return item

        for token in iter_string_tokens(item):
            if predicate(token):
                return token

    return None
