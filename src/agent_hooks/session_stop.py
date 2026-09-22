from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from agent_hooks.common import emit_response, load_stdin_payload
from agent_hooks.ruff_support import repo_uses_ruff, ruff_command

# Automatic fixes at session stop are on by default. Set this to 0/false/no/off for check-only.
STOP_FIX_ENV_VAR = "AGENT_HOOKS_STOP_FIX"
FALSY_VALUES = {"0", "false", "no", "off"}
SUMMARY = "Ruff reports issues at session stop."


def _run(command: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    return completed.returncode, completed.stdout, completed.stderr


def _block_response(reason: str) -> dict[str, Any]:
    # Claude Code reads ``decision``/``reason`` at the top level; Codex and the Pi bridge read
    # them from ``hookSpecificOutput``. Emit both so one payload serves every harness.
    return {
        "systemMessage": SUMMARY,
        "decision": "block",
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "decision": "block",
            "reason": reason,
        },
    }


def _notice_response(reason: str) -> dict[str, Any]:
    # Informational only: no ``decision`` key, so no harness treats this as a block.
    return {"systemMessage": reason}


def _stop_hook_active(payload: dict[str, Any]) -> bool:
    """Return True when the harness is already continuing because this hook blocked a stop.

    Claude Code sets ``stop_hook_active`` on Stop payloads in that situation and expects hooks
    to return success while it is true; otherwise every stop is blocked again until the harness
    gives up after a fixed number of attempts.
    """
    return payload.get("stop_hook_active") is True


def _fixes_enabled() -> bool:
    return os.environ.get(STOP_FIX_ENV_VAR, "1").strip().lower() not in FALSY_VALUES


def _git_toplevel(root: Path) -> Path | None:
    exit_code, stdout, _ = _run(["git", "-C", str(root), "rev-parse", "--show-toplevel"])
    if exit_code != 0 or not stdout.strip():
        return None
    return Path(stdout.strip())


def _changed_python_files(root: Path) -> list[str]:
    """Return Python files with uncommitted changes under ``root``, relative to ``root``.

    ``git status --porcelain`` reports paths relative to the repository top level, so entries are
    joined to that directory and then filtered to files inside ``root``. Every uncommitted Python
    change in the working tree is included, not only files edited during the session; that is a
    deliberate trade-off, and ``AGENT_HOOKS_STOP_FIX=0`` disables fixes entirely. Deleted files
    are skipped. Returns an empty list outside a Git repository.
    """
    toplevel = _git_toplevel(root)
    if toplevel is None:
        return []

    exit_code, stdout, _ = _run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"]
    )
    if exit_code != 0:
        return []

    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        return []

    changed: set[str] = set()
    for line in stdout.splitlines():
        if len(line) < 4:
            continue
        status, entry = line[:2], line[3:]
        if "D" in status:
            continue
        if " -> " in entry:
            entry = entry.rsplit(" -> ", 1)[1]
        entry = entry.strip().strip('"')
        if not entry.endswith(".py"):
            continue

        candidate = toplevel / entry
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if not resolved.is_relative_to(resolved_root):
            continue
        changed.add(resolved.relative_to(resolved_root).as_posix())

    return sorted(changed)


def evaluate(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Fix and check the project with Ruff, returning the stop block or notice, if any.

    The project is the current directory, which every harness sets. Ruff runs as a subprocess
    chosen by ``ruff_command``; nothing else here touches the project's environment.
    """
    root = Path.cwd()
    if not repo_uses_ruff(root):
        return None

    ruff = ruff_command(root)
    fixes_enabled = _fixes_enabled()
    if fixes_enabled:
        changed = _changed_python_files(root)
        if changed:
            _run([*ruff, "check", "--fix", *changed])
            _run([*ruff, "format", *changed])

    checks = [
        [*ruff, "check", "."],
        [*ruff, "format", "--check", "."],
    ]

    failures: list[tuple[list[str], str]] = []
    for command in checks:
        exit_code, stdout, stderr = _run(command)
        if exit_code != 0:
            failures.append((command, (stdout or stderr).strip()))

    if not failures:
        return None

    lines = [SUMMARY]
    for command, result_text in failures:
        lines.append(f"Command: {' '.join(command)}")
        if result_text:
            lines.append(result_text)
    if not fixes_enabled:
        lines.append(
            f"Automatic fixes are disabled by {STOP_FIX_ENV_VAR}. Unset it to let the Stop "
            "hook run `ruff check --fix` and `ruff format` on Python files with uncommitted "
            "changes under the current directory."
        )

    if _stop_hook_active(payload):
        lines.append(
            "The previous stop was already blocked by this hook, so this stop is not blocked "
            "again. Remaining findings are reported for review."
        )
        return _notice_response("\n".join(lines))

    return _block_response("\n".join(lines))


def main() -> int:
    """Run the stop sweep alone as a hook. The installed entry point is ``agent_hooks.dispatch``."""
    emit_response(evaluate(load_stdin_payload()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
