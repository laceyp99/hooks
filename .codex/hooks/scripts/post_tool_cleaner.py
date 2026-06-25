import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from ruff_support import repo_uses_ruff

WRITE_TOOL_NAMES = {
    "applypatch",
    "apply_patch",
    "create_file",
    "edit",
    "editfiles",
    "edit_notebook_file",
    "insert_edit_into_file",
    "move_file",
    "replace_string_in_file",
    "vscode_renamesymbol",
}

WRITE_TOOL_MARKERS = (
    "apply_patch",
    "create",
    "edit",
    "insert_edit",
    "move",
    "patch",
    "rename",
    "replace_string",
    "write",
)


def _load_stdin() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _should_lint(tool_name: str) -> bool:
    name = tool_name.lower()
    short_name = name.rsplit(".", 1)[-1]
    return (
        name in WRITE_TOOL_NAMES
        or short_name in WRITE_TOOL_NAMES
        or any(marker in name for marker in WRITE_TOOL_MARKERS)
    )


def _collect_python_paths(value: Any, seen: set[Path]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _collect_python_paths(item, seen)
        return

    if isinstance(value, list):
        for item in value:
            _collect_python_paths(item, seen)
        return

    if not isinstance(value, str):
        return

    normalized = value.strip().replace("\\", "/")
    if normalized.endswith(".py"):
        candidate = Path(value)
        if candidate.exists() and candidate.is_file():
            seen.add(candidate.resolve())


def _run_ruff(command_name: str, paths: list[Path], *args: str) -> tuple[int, str, str]:
    command = [
        sys.executable,
        "-m",
        "ruff",
        command_name,
        *args,
        *[str(path) for path in paths],
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    return completed.returncode, completed.stdout, completed.stderr


def _emit_additional_context(paths: list[Path], details: list[str]) -> None:
    output = "\n\n".join(detail for detail in details if detail.strip())
    if not output:
        return

    path_list = ", ".join(str(path) for path in paths)
    payload = {
        "systemMessage": f"Ruff found issues while cleaning edited Python files: {path_list}",
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": f"Ruff cleaner results for edited Python files ({path_list}):\n{output}",
        },
    }
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


def main() -> int:
    payload = _load_stdin()
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "")
    if not _should_lint(tool_name):
        return 0

    if not repo_uses_ruff(Path.cwd()):
        return 0

    tool_input = payload.get("tool_input") or payload.get("toolArgs") or {}
    paths: set[Path] = set()
    _collect_python_paths(tool_input, paths)

    if not paths:
        return 0

    python_paths = sorted(paths)
    details: list[str] = []

    format_code, format_stdout, format_stderr = _run_ruff("format", python_paths)
    if format_code != 0:
        format_output = (format_stdout or format_stderr).strip()
        if format_output:
            details.append(f"ruff format:\n{format_output}")

    fix_code, fix_stdout, fix_stderr = _run_ruff("check", python_paths, "--fix")
    if fix_code != 0:
        fix_output = (fix_stdout or fix_stderr).strip()
        if fix_output:
            details.append(f"ruff check --fix:\n{fix_output}")

    check_code, check_stdout, check_stderr = _run_ruff("check", python_paths)
    if check_code != 0:
        check_output = (check_stdout or check_stderr).strip()
        if check_output:
            details.append(f"ruff check:\n{check_output}")

    if details:
        _emit_additional_context(python_paths, details)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
