import json
import subprocess
import sys
from pathlib import Path

from agent_hooks import post_tool_cleaner as _impl
from agent_hooks.ruff_support import repo_uses_ruff

_should_lint = _impl._should_lint
_collect_python_paths = _impl._collect_python_paths
_run_ruff = _impl._run_ruff
_emit_additional_context = _impl._emit_additional_context


def main() -> int:
    originals = {
        "Path": _impl.Path,
        "_collect_python_paths": _impl._collect_python_paths,
        "_emit_additional_context": _impl._emit_additional_context,
        "_run_ruff": _impl._run_ruff,
        "_should_lint": _impl._should_lint,
        "json": _impl.json,
        "repo_uses_ruff": _impl.repo_uses_ruff,
        "subprocess": _impl.subprocess,
        "sys": _impl.sys,
    }
    try:
        _impl.Path = Path
        _impl.json = json
        _impl.repo_uses_ruff = repo_uses_ruff
        _impl.subprocess = subprocess
        _impl.sys = sys
        _impl._should_lint = _should_lint
        _impl._collect_python_paths = _collect_python_paths
        _impl._run_ruff = _run_ruff
        _impl._emit_additional_context = _emit_additional_context
        return _impl.main()
    finally:
        _impl.Path = originals["Path"]
        _impl._collect_python_paths = originals["_collect_python_paths"]
        _impl._emit_additional_context = originals["_emit_additional_context"]
        _impl._run_ruff = originals["_run_ruff"]
        _impl._should_lint = originals["_should_lint"]
        _impl.json = originals["json"]
        _impl.repo_uses_ruff = originals["repo_uses_ruff"]
        _impl.subprocess = originals["subprocess"]
        _impl.sys = originals["sys"]


if __name__ == "__main__":
    raise SystemExit(main())
