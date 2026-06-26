from __future__ import annotations

from pathlib import Path

RUFF_CONFIG_FILES = (
    "ruff.toml",
    ".ruff.toml",
)

RUFF_METADATA_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
)

RUFF_MARKERS = (
    "[tool.ruff]",
    '"ruff"',
    "'ruff'",
    "ruff>=",
    "ruff==",
    "ruff~=",
    "ruff<=",
    "ruff!=",
    "ruff\n",
)


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

    lowered = content.lower()
    return any(marker in lowered for marker in RUFF_MARKERS)
