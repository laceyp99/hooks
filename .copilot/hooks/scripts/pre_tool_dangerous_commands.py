import json
import re
import sys

from agent_hooks import dangerous_commands as _impl

_should_check = _impl._should_check
_normalize_command = _impl._normalize_command
_matches_dangerous_command = _impl._matches_dangerous_command
_find_dangerous_command = _impl._find_dangerous_command
_emit_block = _impl._emit_block


def main() -> int:
    originals = {
        "_emit_block": _impl._emit_block,
        "_find_dangerous_command": _impl._find_dangerous_command,
        "_matches_dangerous_command": _impl._matches_dangerous_command,
        "_normalize_command": _impl._normalize_command,
        "_should_check": _impl._should_check,
        "json": _impl.json,
        "re": _impl.re,
        "sys": _impl.sys,
    }
    try:
        _impl.json = json
        _impl.re = re
        _impl.sys = sys
        _impl._should_check = _should_check
        _impl._normalize_command = _normalize_command
        _impl._matches_dangerous_command = _matches_dangerous_command
        _impl._find_dangerous_command = _find_dangerous_command
        _impl._emit_block = _emit_block
        return _impl.main()
    finally:
        _impl._emit_block = originals["_emit_block"]
        _impl._find_dangerous_command = originals["_find_dangerous_command"]
        _impl._matches_dangerous_command = originals["_matches_dangerous_command"]
        _impl._normalize_command = originals["_normalize_command"]
        _impl._should_check = originals["_should_check"]
        _impl.json = originals["json"]
        _impl.re = originals["re"]
        _impl.sys = originals["sys"]


if __name__ == "__main__":
    raise SystemExit(main())
