import json
import subprocess
import sys
from pathlib import Path

from agent_hooks import session_stop as _impl
from agent_hooks.ruff_support import repo_uses_ruff

_run = _impl._run
_emit_block = _impl._emit_block


def main() -> int:
    originals = {
        "Path": _impl.Path,
        "_emit_block": _impl._emit_block,
        "_run": _impl._run,
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
        _impl._run = _run
        _impl._emit_block = _emit_block
        return _impl.main()
    finally:
        _impl.Path = originals["Path"]
        _impl._emit_block = originals["_emit_block"]
        _impl._run = originals["_run"]
        _impl.json = originals["json"]
        _impl.repo_uses_ruff = originals["repo_uses_ruff"]
        _impl.subprocess = originals["subprocess"]
        _impl.sys = originals["sys"]


if __name__ == "__main__":
    raise SystemExit(main())
