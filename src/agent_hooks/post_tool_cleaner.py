from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from agent_hooks.common import (
    FILE_TARGET_FIELD_NAMES,
    emit_response,
    iter_field_strings,
    iter_string_tokens,
    load_stdin_payload,
    normalize_tool_name,
)
from agent_hooks.ruff_support import repo_uses_ruff, ruff_command

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


def _should_lint(tool_name: str) -> bool:
    name, short_name = normalize_tool_name(tool_name)
    return (
        name in WRITE_TOOL_NAMES
        or short_name in WRITE_TOOL_NAMES
        or any(marker in name for marker in WRITE_TOOL_MARKERS)
    )


def _resolve_python_path(candidate: str, root: Path) -> Path | None:
    """Resolve ``candidate`` to an existing Python file inside ``root``.

    Relative paths are interpreted against ``root``. Symlinks are resolved before the
    containment check so a link inside the repository cannot point Ruff at a file outside it.
    """
    normalized = candidate.strip()
    if not normalized.replace("\\", "/").endswith(".py"):
        return None

    path = Path(normalized)
    if not path.is_absolute():
        path = root / path

    try:
        if not path.is_file():
            return None
        resolved = path.resolve(strict=True)
        resolved_root = root.resolve(strict=True)
    except OSError:
        return None

    if not resolved.is_relative_to(resolved_root):
        return None

    return resolved


def _collect_python_paths(value: Any, seen: set[Path], root: Path) -> None:
    """Collect edited Python files named by the tool's file-target fields.

    Only known target fields (``path``, ``file_path``, ``destination``, ...) and apply_patch
    file headers are consulted. Free text elsewhere in the payload is ignored, and any path that
    resolves outside ``root`` is rejected so the cleaner never rewrites files outside the repo.
    """
    for target in iter_field_strings(value, FILE_TARGET_FIELD_NAMES):
        resolved = _resolve_python_path(target, root)
        if resolved is not None:
            seen.add(resolved)
            continue

        for token in iter_string_tokens(target):
            resolved = _resolve_python_path(token, root)
            if resolved is not None:
                seen.add(resolved)


def _run_ruff(
    ruff: list[str], command_name: str, paths: list[Path], *args: str
) -> tuple[int, str, str]:
    command = [*ruff, command_name, *args, *[str(path) for path in paths]]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    return completed.returncode, completed.stdout, completed.stderr


def _additional_context_response(paths: list[Path], details: list[str]) -> dict[str, Any] | None:
    output = "\n\n".join(detail for detail in details if detail.strip())
    if not output:
        return None

    path_list = ", ".join(str(path) for path in paths)
    return {
        "systemMessage": f"Ruff found issues while cleaning edited Python files: {path_list}",
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": f"Ruff cleaner results for edited Python files ({path_list}):\n{output}",
        },
    }


def evaluate(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Clean the Python files the tool call edited and return any remaining Ruff findings.

    The project is the current directory, which every harness sets. Ruff runs as a subprocess
    chosen by ``ruff_command``; nothing else here touches the project's environment.
    """
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "")
    if not _should_lint(tool_name):
        return None

    root = Path.cwd()
    if not repo_uses_ruff(root):
        return None

    tool_input = payload.get("tool_input") or payload.get("toolArgs") or {}
    paths: set[Path] = set()
    _collect_python_paths(tool_input, paths, root)

    if not paths:
        return None

    python_paths = sorted(paths)
    ruff = ruff_command(root)
    details: list[str] = []

    fix_code, fix_stdout, fix_stderr = _run_ruff(ruff, "check", python_paths, "--fix")
    if fix_code != 0:
        fix_output = (fix_stdout or fix_stderr).strip()
        if fix_output:
            details.append(f"ruff check --fix:\n{fix_output}")

    format_code, format_stdout, format_stderr = _run_ruff(ruff, "format", python_paths)
    if format_code != 0:
        format_output = (format_stdout or format_stderr).strip()
        if format_output:
            details.append(f"ruff format:\n{format_output}")

    check_code, check_stdout, check_stderr = _run_ruff(ruff, "check", python_paths)
    if check_code != 0:
        check_output = (check_stdout or check_stderr).strip()
        if check_output:
            details.append(f"ruff check:\n{check_output}")

    return _additional_context_response(python_paths, details)


def main() -> int:
    """Run the cleaner alone as a hook. The installed entry point is ``agent_hooks.dispatch``."""
    emit_response(evaluate(load_stdin_payload()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
