from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

VENV_DIR_NAMES = (
    ".venv",
    "venv",
    "env",
)


def _venv_python_path(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _hooks_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _project_root() -> Path:
    return Path.cwd()


def _resolve_python(cwd: Path) -> Path:
    for dirname in VENV_DIR_NAMES:
        candidate = _venv_python_path(cwd / dirname)
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


def _resolve_hook_script(hook_script_arg: str) -> Path:
    hook_script = Path(hook_script_arg)
    if not hook_script.is_absolute():
        hook_script = _hooks_root() / hook_script
    return hook_script.resolve()


def _project_pythonpath(root: Path) -> str:
    pythonpath_parts = [str(root / "src"), str(root)]
    existing = os.environ.get("PYTHONPATH")
    if existing:
        pythonpath_parts.append(existing)
    return os.pathsep.join(part for part in pythonpath_parts if part)


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: run_hook.py <hook_script> [args...]\n")
        return 2

    hook_script = _resolve_hook_script(sys.argv[1])
    if not hook_script.is_file():
        sys.stderr.write(f"Hook script not found: {hook_script}\n")
        return 2

    python_executable = _resolve_python(_project_root())
    stdin_data = sys.stdin.buffer.read()
    command = [str(python_executable), str(hook_script), *sys.argv[2:]]
    env = os.environ.copy()
    env["PYTHONPATH"] = _project_pythonpath(_hooks_root())
    completed = subprocess.run(
        command,
        check=False,
        input=stdin_data,
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=env,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
