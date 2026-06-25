import json
import subprocess
import sys
from pathlib import Path

from ruff_support import repo_uses_ruff


def _run(command: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    return completed.returncode, completed.stdout, completed.stderr


def _emit_block(reason: str) -> None:
    payload = {
        "systemMessage": "Ruff still reports issues after auto-fix and format.",
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "decision": "block",
            "reason": reason,
        },
    }
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


def main() -> int:
    if not repo_uses_ruff(Path.cwd()):
        return 0

    commands = [
        [sys.executable, "-m", "ruff", "check", ".", "--fix"],
        [sys.executable, "-m", "ruff", "format", "."],
        [sys.executable, "-m", "ruff", "check", "."],
    ]

    outputs: list[tuple[list[str], int, str, str]] = []
    for command in commands:
        exit_code, stdout, stderr = _run(command)
        outputs.append((command, exit_code, stdout, stderr))

    final_command, final_exit_code, final_stdout, final_stderr = outputs[-1]
    if final_exit_code != 0:
        result_text = (final_stdout or final_stderr).strip()
        reason = "Ruff still reports issues after auto-fix and format.\n"
        reason += f"Final command: {' '.join(final_command)}"
        if result_text:
            reason += f"\n{result_text}"
        _emit_block(reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
