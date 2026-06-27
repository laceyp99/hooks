import json
import sys

from agent_hooks import security as _impl

_matches_env_path = _impl._matches_env_path
_matches_protected_git_path = _impl._matches_protected_git_path
_should_check = _impl._should_check
_should_check_git_paths = _impl._should_check_git_paths
_find_env_path = _impl._find_env_path
_find_protected_git_path = _impl._find_protected_git_path
_matches_protected_git_mutation_command = _impl._matches_protected_git_mutation_command
_find_protected_git_mutation_command = _impl._find_protected_git_mutation_command
_emit_block = _impl._emit_block
_emit_git_block = _impl._emit_git_block


def main() -> int:
    originals = {
        "_emit_block": _impl._emit_block,
        "_emit_git_block": _impl._emit_git_block,
        "_find_env_path": _impl._find_env_path,
        "_find_protected_git_path": _impl._find_protected_git_path,
        "_find_protected_git_mutation_command": _impl._find_protected_git_mutation_command,
        "_matches_env_path": _impl._matches_env_path,
        "_matches_protected_git_path": _impl._matches_protected_git_path,
        "_matches_protected_git_mutation_command": _impl._matches_protected_git_mutation_command,
        "_should_check": _impl._should_check,
        "_should_check_git_paths": _impl._should_check_git_paths,
        "json": _impl.json,
        "sys": _impl.sys,
    }
    try:
        _impl.json = json
        _impl.sys = sys
        _impl._matches_env_path = _matches_env_path
        _impl._matches_protected_git_path = _matches_protected_git_path
        _impl._should_check = _should_check
        _impl._should_check_git_paths = _should_check_git_paths
        _impl._find_env_path = _find_env_path
        _impl._find_protected_git_path = _find_protected_git_path
        _impl._find_protected_git_mutation_command = _find_protected_git_mutation_command
        _impl._matches_protected_git_mutation_command = _matches_protected_git_mutation_command
        _impl._emit_block = _emit_block
        _impl._emit_git_block = _emit_git_block
        return _impl.main()
    finally:
        _impl._emit_block = originals["_emit_block"]
        _impl._emit_git_block = originals["_emit_git_block"]
        _impl._find_env_path = originals["_find_env_path"]
        _impl._find_protected_git_path = originals["_find_protected_git_path"]
        _impl._find_protected_git_mutation_command = originals[
            "_find_protected_git_mutation_command"
        ]
        _impl._matches_env_path = originals["_matches_env_path"]
        _impl._matches_protected_git_path = originals["_matches_protected_git_path"]
        _impl._matches_protected_git_mutation_command = originals[
            "_matches_protected_git_mutation_command"
        ]
        _impl._should_check = originals["_should_check"]
        _impl._should_check_git_paths = originals["_should_check_git_paths"]
        _impl.json = originals["json"]
        _impl.sys = originals["sys"]


if __name__ == "__main__":
    raise SystemExit(main())
