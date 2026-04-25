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


def _resolve_python(cwd: Path) -> Path:
    for dirname in VENV_DIR_NAMES:
        candidate = _venv_python_path(cwd / dirname)
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: run_hook.py <hook_script> [args...]\n")
        return 2

    hook_script = Path(sys.argv[1]).resolve()
    if not hook_script.is_file():
        sys.stderr.write(f"Hook script not found: {hook_script}\n")
        return 2

    python_executable = _resolve_python(Path.cwd())
    stdin_data = sys.stdin.buffer.read()
    command = [str(python_executable), str(hook_script), *sys.argv[2:]]
    completed = subprocess.run(
        command,
        check=False,
        input=stdin_data,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
