import subprocess
import sys
from pathlib import Path


def _bootstrap_bundle_src_path() -> None:
    bundle_src = Path(__file__).resolve().parents[2] / "src"
    bundle_src_str = str(bundle_src)
    if bundle_src_str not in sys.path:
        sys.path.insert(0, bundle_src_str)


_bootstrap_bundle_src_path()

from agent_hooks import bootstrap as _impl

VENV_DIR_NAMES = _impl.VENV_DIR_NAMES
_hooks_root = _impl._hooks_root
_project_root = _impl._project_root
_resolve_hook_script_impl = _impl._resolve_hook_script
_resolve_python = _impl._resolve_python
_venv_python_path = _impl._venv_python_path


def _resolve_hook_script_proxy(hook_script_arg: str) -> Path:
    original_hooks_root = _impl._hooks_root
    try:
        _impl._hooks_root = _hooks_root
        return _resolve_hook_script_impl(hook_script_arg)
    finally:
        _impl._hooks_root = original_hooks_root


_resolve_hook_script = _resolve_hook_script_proxy


def main() -> int:
    originals = {
        "Path": getattr(_impl, "Path", None),
        "_hooks_root": _impl._hooks_root,
        "_project_root": _impl._project_root,
        "_resolve_hook_script": _impl._resolve_hook_script,
        "_resolve_python": _impl._resolve_python,
        "_venv_python_path": _impl._venv_python_path,
        "subprocess": _impl.subprocess,
        "sys": _impl.sys,
    }
    try:
        _impl.sys = sys
        _impl.subprocess = subprocess
        _impl.Path = Path
        _impl._hooks_root = _hooks_root
        _impl._project_root = _project_root
        _impl._resolve_hook_script = _resolve_hook_script
        _impl._resolve_python = _resolve_python
        _impl._venv_python_path = _venv_python_path
        return _impl.main()
    finally:
        _impl.Path = originals["Path"]
        _impl._hooks_root = originals["_hooks_root"]
        _impl._project_root = originals["_project_root"]
        _impl._resolve_hook_script = originals["_resolve_hook_script"]
        _impl._resolve_python = originals["_resolve_python"]
        _impl._venv_python_path = originals["_venv_python_path"]
        _impl.subprocess = originals["subprocess"]
        _impl.sys = originals["sys"]


if __name__ == "__main__":
    raise SystemExit(main())
