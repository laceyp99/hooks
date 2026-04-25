import io
from types import SimpleNamespace


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
    preferred = tmp_path / ".venv" / "Scripts"
    fallback = tmp_path / "venv" / "Scripts"
    preferred.mkdir(parents=True)
    fallback.mkdir(parents=True)
    (preferred / "python.exe").write_text("", encoding="utf-8")
    (fallback / "python.exe").write_text("", encoding="utf-8")

    assert run_hook._resolve_python(tmp_path) == preferred / "python.exe"


def test_resolve_python_falls_back_to_current_interpreter(
    load_script_module, monkeypatch, tmp_path
) -> None:
    run_hook = load_script_module("run_hook.py", "run_hook_fallback")
    monkeypatch.setattr(run_hook.sys, "executable", "C:/Python/python.exe")

    assert run_hook._resolve_python(tmp_path) == run_hook.Path("C:/Python/python.exe")


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
    stdout = io.StringIO()
    stderr = io.StringIO()
    stdin_buffer = io.BytesIO(b'{"event": "value"}')
    stdin = SimpleNamespace(buffer=stdin_buffer)
    recorded = {}

    monkeypatch.setattr(run_hook.sys, "argv", ["run_hook.py", str(hook_script), "--flag"])
    monkeypatch.setattr(run_hook.sys, "stdin", stdin)
    monkeypatch.setattr(run_hook.sys, "stdout", stdout)
    monkeypatch.setattr(run_hook.sys, "stderr", stderr)
    monkeypatch.setattr(
        run_hook, "_resolve_python", lambda cwd: run_hook.Path("C:/Python/python.exe")
    )

    def _fake_run(command, check, input, stdout, stderr):
        recorded["command"] = command
        recorded["check"] = check
        recorded["input"] = input
        recorded["stdout"] = stdout
        recorded["stderr"] = stderr
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
