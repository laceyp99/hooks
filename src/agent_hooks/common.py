from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Iterator
from typing import Any

PATH_TOKEN_RE = re.compile(r"[^\s\"'`;&|<>]+")


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


def first_matching_string(value: Any, predicate: Callable[[str], bool]) -> str | None:
    for item in iter_strings(value):
        if predicate(item):
            return item

        for token in iter_string_tokens(item):
            if predicate(token):
                return token

    return None
