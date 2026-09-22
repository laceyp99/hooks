"""Launch an Agent Hooks event in the interpreter the harness started.

Usage: ``python run_hook.py {pre-tool|post-tool|stop}`` with the hook payload on stdin.

This is the one runner every harness calls. The installer copies it to
``%USERPROFILE%\\.claude\\hooks`` and ``%USERPROFILE%\\.codex\\hooks``; the Pi and OpenCode
bridges run it from the checkout's ``bin`` directory. It never executes the project's own
interpreter: the guards must not run under a Python the repository being guarded controls.
Only the Ruff hooks reach into the project, and only to launch Ruff as a subprocess.

This file must stay importable by old interpreters, since its job on Python < 3.10 is to hand
off to a newer one, so keep 3.10-only syntax out of it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

MIN_PYTHON_VERSION = (3, 10)
CURRENT_INTERPRETER_ERROR = (
    "Current Python interpreter is below Python 3.10 and no compatible fallback is available.\n"
)


def _find_src_dir() -> Path | None:
    """Return the ``src`` directory holding ``agent_hooks`` next to or above this runner.

    In the checkout the runner is ``bin/run_hook.py`` and the package is ``src/agent_hooks``.
    Installed, it is ``%USERPROFILE%\\.claude\\hooks\\run_hook.py`` with the package at
    ``%USERPROFILE%\\src\\agent_hooks``. The search is anchored to this file, never to the
    current directory, so a project cannot supply its own ``agent_hooks``.
    """
    here = Path(__file__).resolve().parent
    for base in (here, *list(here.parents)[:2]):
        candidate = base / "src"
        if (candidate / "agent_hooks" / "__init__.py").is_file():
            return candidate
    return None


def _fallback_command(version_info: tuple[int, ...], platform: str) -> list[str] | None:
    """Return the interpreter to hand off to, ``[]`` to stay in-process, or None if stuck."""
    if tuple(version_info[:2]) >= MIN_PYTHON_VERSION:
        return []
    if platform == "win32":
        return ["py", "-3.10"]
    return None


def main() -> int:
    fallback = _fallback_command(tuple(sys.version_info), sys.platform)
    if fallback is None:
        sys.stderr.write(CURRENT_INTERPRETER_ERROR)
        return 2

    if fallback:
        # The child inherits stdin, stdout, and stderr, so the payload and the response pass
        # straight through without this interpreter touching them.
        command = [*fallback, str(Path(__file__).resolve()), *sys.argv[1:]]
        try:
            return subprocess.run(command, check=False).returncode
        except OSError as exc:
            sys.stderr.write(f"Unable to launch hook interpreter: {' '.join(command)} ({exc})\n")
            return 2

    src_dir = _find_src_dir()
    if src_dir is None:
        sys.stderr.write(f"agent_hooks package not found near {Path(__file__).resolve()}\n")
        return 2

    src = str(src_dir)
    if src not in sys.path:
        sys.path.insert(0, src)

    from agent_hooks.dispatch import main as dispatch_main

    return dispatch_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
