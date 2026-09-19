from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from agent_hooks.ruff_support import repo_uses_ruff

# Automatic fixes at session stop are on by default. Set this to 0/false/no/off for check-only.
STOP_FIX_ENV_VAR = "AGENT_HOOKS_STOP_FIX"
FALSY_VALUES = {"0", "false", "no", "off"}


def _run(command: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    return completed.returncode, completed.stdout, completed.stderr


def _emit_block(reason: str) -> None:
    # Claude Code reads ``decision``/``reason`` at the top level; Codex and the Pi bridge read
    # them from ``hookSpecificOutput``. Emit both so one payload serves every harness.
    payload = {
        "systemMessage": "Ruff reports issues at session stop.",
        "decision": "block",
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "decision": "block",
            "reason": reason,
        },
    }
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


def _fixes_enabled() -> bool:
    return os.environ.get(STOP_FIX_ENV_VAR, "1").strip().lower() not in FALSY_VALUES


def _changed_python_files(root: Path) -> list[str]:
    """Return Python files changed in the working tree, relative to ``root``.

    Uses ``git status --porcelain`` so only files the session actually touched are eligible for
    automatic fixes. Deleted files are skipped. Returns an empty list outside a Git repository.
    """
    exit_code, stdout, _ = _run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"]
    )
    if exit_code != 0:
        return []

    changed: list[str] = []
    for line in stdout.splitlines():
        if len(line) < 4:
            continue
        status, entry = line[:2], line[3:]
        if "D" in status:
            continue
        if " -> " in entry:
            entry = entry.rsplit(" -> ", 1)[1]
        entry = entry.strip().strip('"')
        if entry.endswith(".py") and (root / entry).is_file():
            changed.append(entry)

    return sorted(set(changed))


def main() -> int:
    root = Path.cwd()
    if not repo_uses_ruff(root):
        return 0

    fixes_enabled = _fixes_enabled()
    if fixes_enabled:
        changed = _changed_python_files(root)
        if changed:
            _run([sys.executable, "-m", "ruff", "check", "--fix", *changed])
            _run([sys.executable, "-m", "ruff", "format", *changed])

    checks = [
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "ruff", "format", "--check", "."],
    ]

    failures: list[tuple[list[str], str]] = []
    for command in checks:
        exit_code, stdout, stderr = _run(command)
        if exit_code != 0:
            failures.append((command, (stdout or stderr).strip()))

    if failures:
        lines = ["Ruff reports issues at session stop."]
        for command, result_text in failures:
            lines.append(f"Command: {' '.join(command)}")
            if result_text:
                lines.append(result_text)
        if not fixes_enabled:
            lines.append(
                f"Automatic fixes are disabled by {STOP_FIX_ENV_VAR}. Unset it to let the Stop "
                "hook run `ruff check --fix` and `ruff format` on files changed in this session."
            )
        _emit_block("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
