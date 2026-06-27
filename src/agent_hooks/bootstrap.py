from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

MIN_PYTHON_VERSION = (3, 10)
CURRENT_INTERPRETER_ERROR = (
    "Current Python interpreter is below Python 3.10 and no compatible fallback is available."
)
VENV_DIR_NAMES = (
    ".venv",
    "venv",
    "env",
)
VENV_VERSION_RE = re.compile(r"^version\s*=\s*(\d+)\.(\d+)", re.IGNORECASE | re.MULTILINE)


def _venv_python_path(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _hooks_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _project_root() -> Path:
    return Path.cwd()


def _venv_python_version(venv_dir: Path) -> tuple[int, int] | None:
    cfg_path = venv_dir / "pyvenv.cfg"
    if not cfg_path.is_file():
        return None

    try:
        content = cfg_path.read_text(encoding="utf-8")
    except OSError:
        return None

    match = VENV_VERSION_RE.search(content)
    if not match:
        return None

    return int(match.group(1)), int(match.group(2))


def _resolve_python(cwd: Path) -> Path:
    for dirname in VENV_DIR_NAMES:
        venv_dir = cwd / dirname
        candidate = _venv_python_path(venv_dir)
        if candidate.is_file():
            version = _venv_python_version(venv_dir)
            if version is None:
                raise RuntimeError(
                    f"Unable to determine Python version for virtual environment: {venv_dir}"
                )

            if version < MIN_PYTHON_VERSION:
                raise RuntimeError(
                    f"Virtual environment {venv_dir} uses Python {version[0]}.{version[1]}, "
                    "but hooks require Python 3.10+."
                )

            return candidate

    return Path(sys.executable)


def _resolve_python_command(cwd: Path) -> list[str]:
    resolved_python = _resolve_python(cwd)
    if resolved_python != Path(sys.executable):
        return [str(resolved_python)]

    if sys.version_info >= MIN_PYTHON_VERSION:
        return [sys.executable]

    if sys.platform == "win32":
        return ["py", "-3.10"]

    raise RuntimeError(CURRENT_INTERPRETER_ERROR)


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


def _read_stdin_bytes() -> bytes:
    stdin_buffer = getattr(sys.stdin, "buffer", None)
    if stdin_buffer is not None:
        return stdin_buffer.read()
    data = sys.stdin.read()
    if isinstance(data, bytes):
        return data
    return data.encode("utf-8")


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: run_hook.py <hook_script> [args...]\n")
        return 2

    hook_script = _resolve_hook_script(sys.argv[1])
    if not hook_script.is_file():
        sys.stderr.write(f"Hook script not found: {hook_script}\n")
        return 2

    try:
        python_command = _resolve_python_command(_project_root())
    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    stdin_data = _read_stdin_bytes()
    command = [*python_command, str(hook_script), *sys.argv[2:]]
    env = os.environ.copy()
    env["PYTHONPATH"] = _project_pythonpath(_hooks_root())
    try:
        completed = subprocess.run(
            command,
            check=False,
            input=stdin_data,
            stdout=sys.stdout,
            stderr=sys.stderr,
            env=env,
        )
    except OSError as exc:
        sys.stderr.write(f"Unable to launch hook interpreter: {' '.join(command)} ({exc})\n")
        return 2
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
