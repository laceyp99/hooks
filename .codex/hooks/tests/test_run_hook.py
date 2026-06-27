import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
CODEX_BUNDLE_ROOT = REPO_ROOT / ".codex" / "hooks"
REPO_SRC = REPO_ROOT / "src"


def test_venv_python_path_uses_windows_layout(load_script_module, monkeypatch, tmp_path) -> None:
    run_hook = load_script_module("run_hook.py", "run_hook_windows")
    monkeypatch.setattr(run_hook.sys, "platform", "win32")

    assert run_hook._venv_python_path(tmp_path) == tmp_path / "Scripts" / "python.exe"


def test_venv_python_path_uses_posix_layout(load_script_module, monkeypatch, tmp_path) -> None:
    run_hook = load_script_module("run_hook.py", "run_hook_posix")
    monkeypatch.setattr(run_hook.sys, "platform", "linux")

    assert run_hook._venv_python_path(tmp_path) == tmp_path / "bin" / "python"


def test_resolve_python_prefers_first_existing_virtualenv(
    load_script_module, monkeypatch, tmp_path
) -> None:
    run_hook = load_script_module("run_hook.py", "run_hook_resolve")
    monkeypatch.setattr(run_hook.sys, "platform", "win32")
    preferred = tmp_path / ".venv"
    fallback = tmp_path / "venv"
    preferred_scripts = preferred / "Scripts"
    fallback_scripts = fallback / "Scripts"
    preferred_scripts.mkdir(parents=True)
    fallback_scripts.mkdir(parents=True)
    (preferred / "pyvenv.cfg").write_text("version = 3.11.0\n", encoding="utf-8")
    (fallback / "pyvenv.cfg").write_text("version = 3.11.0\n", encoding="utf-8")
    (preferred_scripts / "python.exe").write_text("", encoding="utf-8")
    (fallback_scripts / "python.exe").write_text("", encoding="utf-8")

    assert run_hook._resolve_python(tmp_path) == preferred_scripts / "python.exe"


def test_resolve_python_falls_back_to_current_interpreter(
    load_script_module, monkeypatch, tmp_path
) -> None:
    run_hook = load_script_module("run_hook.py", "run_hook_fallback")
    monkeypatch.setattr(run_hook.sys, "executable", "C:/Python/python.exe")

    assert run_hook._resolve_python(tmp_path) == run_hook.Path("C:/Python/python.exe")


def test_resolve_python_command_uses_current_interpreter_when_supported(
    load_script_module, monkeypatch, tmp_path
) -> None:
    run_hook = load_script_module("run_hook.py", "run_hook_command_current")
    monkeypatch.setattr(run_hook.sys, "executable", "C:/Python/python.exe")
    monkeypatch.setattr(run_hook.sys, "version_info", (3, 11, 0))

    assert run_hook._resolve_python_command(tmp_path) == [run_hook.sys.executable]


def test_resolve_python_command_uses_py_310_fallback_when_current_is_too_old(
    load_script_module, monkeypatch, tmp_path
) -> None:
    run_hook = load_script_module("run_hook.py", "run_hook_command_fallback")
    monkeypatch.setattr(run_hook.sys, "executable", "C:/Python/python.exe")
    monkeypatch.setattr(run_hook.sys, "version_info", (3, 9, 9))
    monkeypatch.setattr(run_hook.sys, "platform", "win32")

    assert run_hook._resolve_python_command(tmp_path) == ["py", "-3.10"]


def test_resolve_python_command_raises_when_too_old_and_no_windows_fallback(
    load_script_module, monkeypatch, tmp_path
) -> None:
    run_hook = load_script_module("run_hook.py", "run_hook_command_error")
    monkeypatch.setattr(run_hook.sys, "executable", "C:/Python/python.exe")
    monkeypatch.setattr(run_hook.sys, "version_info", (3, 9, 9))
    monkeypatch.setattr(run_hook.sys, "platform", "linux")

    with pytest.raises(RuntimeError, match="no compatible fallback"):
        run_hook._resolve_python_command(tmp_path)


def test_main_returns_error_when_python_launch_fails(
    load_script_module, monkeypatch, tmp_path
) -> None:
    run_hook = load_script_module("run_hook.py", "run_hook_launch_error")
    hook_script = tmp_path / "hook.py"
    hook_script.write_text("print('ok')\n", encoding="utf-8")
    stderr = io.StringIO()
    monkeypatch.setattr(run_hook.sys, "argv", ["run_hook.py", str(hook_script)])
    monkeypatch.setattr(run_hook.sys, "stderr", stderr)
    monkeypatch.setattr(run_hook.sys, "stdin", io.BytesIO(b""))
    monkeypatch.setattr(run_hook, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(run_hook, "_resolve_python_command", lambda cwd: ["py", "-3.10"])

    def _raise(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(run_hook.subprocess, "run", _raise)

    assert run_hook.main() == 2
    assert "Unable to launch hook interpreter" in stderr.getvalue()


def test_resolve_hook_script_anchors_relative_paths_to_hooks_root(
    load_script_module, monkeypatch, tmp_path
) -> None:
    run_hook = load_script_module("run_hook.py", "run_hook_hook_root")
    hook_script = tmp_path / "scripts" / "session_stop.py"
    hook_script.parent.mkdir(parents=True)
    hook_script.write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr(run_hook, "_hooks_root", lambda: tmp_path)

    assert run_hook._resolve_hook_script("scripts/session_stop.py") == hook_script.resolve()


def test_main_returns_usage_error_without_script_argument(load_script_module, monkeypatch) -> None:
    run_hook = load_script_module("run_hook.py", "run_hook_usage")
    stderr = io.StringIO()
    monkeypatch.setattr(run_hook.sys, "argv", ["run_hook.py"])
    monkeypatch.setattr(run_hook.sys, "stderr", stderr)

    assert run_hook.main() == 2
    assert "Usage:" in stderr.getvalue()


def test_main_returns_error_for_missing_hook_script(
    load_script_module, monkeypatch, tmp_path
) -> None:
    run_hook = load_script_module("run_hook.py", "run_hook_missing")
    stderr = io.StringIO()
    missing = tmp_path / "missing.py"
    monkeypatch.setattr(run_hook.sys, "argv", ["run_hook.py", str(missing)])
    monkeypatch.setattr(run_hook.sys, "stderr", stderr)

    assert run_hook.main() == 2
    assert str(missing.resolve()) in stderr.getvalue()


def test_main_invokes_hook_with_resolved_python_and_passthrough_stdio(
    load_script_module, monkeypatch, tmp_path
) -> None:
    run_hook = load_script_module("run_hook.py", "run_hook_main")
    hook_script = tmp_path / "hook.py"
    hook_script.write_text("print('ok')\n", encoding="utf-8")
    project_root = tmp_path / "project"
    project_root.mkdir()
    stdout = io.StringIO()
    stderr = io.StringIO()
    stdin_buffer = io.BytesIO(b'{"event": "value"}')
    stdin = SimpleNamespace(buffer=stdin_buffer)
    recorded = {}

    monkeypatch.setattr(run_hook.sys, "argv", ["run_hook.py", str(hook_script), "--flag"])
    monkeypatch.setattr(run_hook.sys, "stdin", stdin)
    monkeypatch.setattr(run_hook.sys, "stdout", stdout)
    monkeypatch.setattr(run_hook.sys, "stderr", stderr)
    monkeypatch.setattr(run_hook, "_project_root", lambda: project_root)
    monkeypatch.setattr(run_hook, "_resolve_hook_script", lambda arg: hook_script.resolve())

    def _fake_resolve_python(cwd):
        recorded["python_cwd"] = cwd
        return run_hook.Path("C:/Python/python.exe")

    monkeypatch.setattr(run_hook, "_resolve_python", _fake_resolve_python)

    def _fake_run(command, check, input, stdout, stderr, env):
        recorded["command"] = command
        recorded["check"] = check
        recorded["input"] = input
        recorded["stdout"] = stdout
        recorded["stderr"] = stderr
        recorded["env"] = env
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(run_hook.subprocess, "run", _fake_run)

    assert run_hook.main() == 7
    assert recorded["command"] == [
        str(run_hook.Path("C:/Python/python.exe")),
        str(hook_script.resolve()),
        "--flag",
    ]
    assert recorded["check"] is False
    assert recorded["input"] == b'{"event": "value"}'
    assert recorded["stdout"] is stdout
    assert recorded["stderr"] is stderr
    assert "PYTHONPATH" in recorded["env"]
    assert recorded["python_cwd"] == project_root


def test_run_hook_imports_from_a_copied_bundle_without_repo_src_on_sys_path(
    tmp_path,
) -> None:
    bundle_root = tmp_path / "bundle"
    hooks_dir = bundle_root / ".codex" / "hooks"
    src_dir = bundle_root / "src" / "agent_hooks"
    hooks_dir.mkdir(parents=True)
    src_dir.mkdir(parents=True)
    shutil.copy2(CODEX_BUNDLE_ROOT / "run_hook.py", hooks_dir / "run_hook.py")
    shutil.copy2(REPO_SRC / "agent_hooks" / "bootstrap.py", src_dir / "bootstrap.py")
    (src_dir / "__init__.py").write_text("", encoding="utf-8")

    code = """
import importlib.util
from pathlib import Path

path = Path({path!r})
spec = importlib.util.spec_from_file_location('clean_room_run_hook', path)
if spec is None or spec.loader is None:
    raise RuntimeError('Unable to load module from ' + str(path))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
""".format(path=str(hooks_dir / "run_hook.py"))
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=bundle_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr
