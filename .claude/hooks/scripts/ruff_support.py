from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

RUFF_CONFIG_FILES = (
    "ruff.toml",
    ".ruff.toml",
)

RUFF_METADATA_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
)

# Fallback substring markers used only when pyproject.toml cannot be parsed as TOML.
RUFF_MARKERS = (
    "[tool.ruff]",
    "[tool.ruff.",
    '"ruff"',
    "'ruff'",
    "ruff>=",
    "ruff==",
    "ruff~=",
    "ruff<=",
    "ruff!=",
    "ruff\n",
)

# PEP 508 project name at the start of a requirement specifier, e.g. ``ruff``, ``ruff[all]``,
# ``ruff>=0.6``, ``ruff @ https://...``, ``ruff ; python_version > "3.10"``.
REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


def repo_uses_ruff(root: Path) -> bool:
    for name in RUFF_CONFIG_FILES:
        if (root / name).is_file():
            return True

    for name in RUFF_METADATA_FILES:
        if file_mentions_ruff(root / name):
            return True

    return False


def file_mentions_ruff(path: Path) -> bool:
    if not path.is_file():
        return False

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False

    if path.suffix.lower() == ".toml":
        return pyproject_declares_ruff(content)

    return requirements_declare_ruff(content)


def _normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_names_ruff(requirement: str) -> bool:
    match = REQUIREMENT_NAME_RE.match(requirement)
    return bool(match) and _normalize_name(match.group(1)) == "ruff"


def requirements_declare_ruff(content: str) -> bool:
    """Return True when a requirements file lists Ruff, regardless of newline style."""
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        if _requirement_names_ruff(line):
            return True
    return False


def _iter_requirement_specs(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            # PEP 735 ``{include-group = "..."}`` table entries carry no requirement.
            if isinstance(item, str):
                yield item


def _table(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _any_requirement_is_ruff(value: Any) -> bool:
    return any(_requirement_names_ruff(spec) for spec in _iter_requirement_specs(value))


def pyproject_declares_ruff(content: str) -> bool:
    """Return True when pyproject.toml configures Ruff or declares it as a dependency."""
    if tomllib is None:
        return _mentions_ruff_marker(content)

    try:
        data = tomllib.loads(content)
    except (tomllib.TOMLDecodeError, ValueError):
        return _mentions_ruff_marker(content)

    tool = _table(data.get("tool"))
    if "ruff" in tool:
        return True

    project = _table(data.get("project"))
    if _any_requirement_is_ruff(project.get("dependencies")):
        return True
    if any(
        _any_requirement_is_ruff(specs)
        for specs in _table(project.get("optional-dependencies")).values()
    ):
        return True
    if any(
        _any_requirement_is_ruff(specs) for specs in _table(data.get("dependency-groups")).values()
    ):
        return True

    poetry = _table(tool.get("poetry"))
    poetry_tables = [
        _table(poetry.get("dependencies")),
        _table(poetry.get("dev-dependencies")),
    ]
    for group in _table(poetry.get("group")).values():
        poetry_tables.append(_table(_table(group).get("dependencies")))
    return any(_normalize_name(str(name)) == "ruff" for table in poetry_tables for name in table)


def _mentions_ruff_marker(content: str) -> bool:
    lowered = content.lower()
    return any(marker in lowered for marker in RUFF_MARKERS)
