import io
import json


def test_main_skips_when_repo_does_not_use_ruff(load_script_module, monkeypatch) -> None:
    stop_hook = load_script_module("scripts/session_stop.py", "session_stop_skip")
    monkeypatch.setattr(stop_hook, "repo_uses_ruff", lambda root: False)
    monkeypatch.setattr(stop_hook.sys, "stdout", io.StringIO())

    assert stop_hook.main() == 0
    assert stop_hook.sys.stdout.getvalue() == ""


def test_main_runs_commands_in_order_without_blocking(load_script_module, monkeypatch) -> None:
    stop_hook = load_script_module("scripts/session_stop.py", "session_stop_ok")
    monkeypatch.setattr(stop_hook, "repo_uses_ruff", lambda root: True)
    stdout = io.StringIO()
    monkeypatch.setattr(stop_hook.sys, "stdout", stdout)
    calls = []
    results = iter([(0, "", ""), (0, "", ""), (0, "", "")])

    def _fake_run(command):
        calls.append(command)
        return next(results)

    monkeypatch.setattr(stop_hook, "_run", _fake_run)

    assert stop_hook.main() == 0
    assert stdout.getvalue() == ""
    assert calls == [
        [stop_hook.sys.executable, "-m", "ruff", "check", ".", "--fix"],
        [stop_hook.sys.executable, "-m", "ruff", "format", "."],
        [stop_hook.sys.executable, "-m", "ruff", "check", "."],
    ]


def test_main_blocks_when_final_ruff_check_fails(load_script_module, monkeypatch) -> None:
    stop_hook = load_script_module("scripts/session_stop.py", "session_stop_fail")
    monkeypatch.setattr(stop_hook, "repo_uses_ruff", lambda root: True)
    stdout = io.StringIO()
    monkeypatch.setattr(stop_hook.sys, "stdout", stdout)
    results = iter([(0, "", ""), (0, "", ""), (1, "line 1: failure", "")])

    monkeypatch.setattr(stop_hook, "_run", lambda command: next(results))

    assert stop_hook.main() == 0
    message = json.loads(stdout.getvalue())
    assert message["hookSpecificOutput"]["decision"] == "block"
    assert "Final command" in message["hookSpecificOutput"]["reason"]
    assert "line 1: failure" in message["hookSpecificOutput"]["reason"]
