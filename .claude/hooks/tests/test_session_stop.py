import io
import json
import subprocess

import pytest


def _ruff(stop_hook, *args: str) -> list[str]:
    return [stop_hook.sys.executable, "-m", "ruff", *args]


@pytest.fixture
def stop_hook(load_script_module, monkeypatch, tmp_path):
    module = load_script_module("scripts/session_stop.py", "session_stop_under_test")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module.sys, "stdin", io.StringIO("{}"))
    monkeypatch.delenv(module.STOP_FIX_ENV_VAR, raising=False)
    monkeypatch.setattr(module, "repo_uses_ruff", lambda root: True)
    return module


def test_main_skips_when_repo_does_not_use_ruff(stop_hook, monkeypatch, capsys) -> None:
    monkeypatch.setattr(stop_hook, "repo_uses_ruff", lambda root: False)
    monkeypatch.setattr(stop_hook, "_run", lambda command: pytest.fail("ruff must not run"))

    assert stop_hook.main() == 0
    assert capsys.readouterr().out == ""


def test_main_fixes_changed_files_by_default(stop_hook, monkeypatch, capsys) -> None:
    monkeypatch.setattr(stop_hook, "_changed_python_files", lambda root: ["pkg/a.py"])
    calls = []

    def _fake_run(command):
        calls.append(command)
        return 0, "", ""

    monkeypatch.setattr(stop_hook, "_run", _fake_run)

    assert stop_hook.main() == 0
    assert capsys.readouterr().out == ""
    assert calls == [
        _ruff(stop_hook, "check", "--fix", "pkg/a.py"),
        _ruff(stop_hook, "format", "pkg/a.py"),
        _ruff(stop_hook, "check", "."),
        _ruff(stop_hook, "format", "--check", "."),
    ]


def test_main_blocks_when_a_check_fails_and_explains_opt_out(
    stop_hook, monkeypatch, capsys
) -> None:
    monkeypatch.setenv(stop_hook.STOP_FIX_ENV_VAR, "0")
    results = iter([(1, "line 1: failure", ""), (0, "", "")])
    monkeypatch.setattr(stop_hook, "_run", lambda command: next(results))

    assert stop_hook.main() == 0
    message = json.loads(capsys.readouterr().out)
    reason = message["hookSpecificOutput"]["reason"]
    assert message["hookSpecificOutput"]["decision"] == "block"
    assert "Command: " in reason
    assert "ruff check ." in reason
    assert "line 1: failure" in reason
    assert stop_hook.STOP_FIX_ENV_VAR in reason


def test_main_blocks_when_format_check_fails(stop_hook, monkeypatch, capsys) -> None:
    monkeypatch.setattr(stop_hook, "_changed_python_files", lambda root: [])
    results = iter([(0, "", ""), (1, "Would reformat: a.py", "")])
    monkeypatch.setattr(stop_hook, "_run", lambda command: next(results))

    assert stop_hook.main() == 0
    message = json.loads(capsys.readouterr().out)
    assert message["hookSpecificOutput"]["decision"] == "block"
    assert "ruff format --check ." in message["hookSpecificOutput"]["reason"]
    assert "Would reformat: a.py" in message["hookSpecificOutput"]["reason"]


@pytest.mark.parametrize("value", ["1", "true", "YES", " on ", "anything"])
def test_truthy_values_fix_only_changed_files_then_checks(
    stop_hook, monkeypatch, capsys, value: str
) -> None:
    monkeypatch.setenv(stop_hook.STOP_FIX_ENV_VAR, value)
    monkeypatch.setattr(stop_hook, "_changed_python_files", lambda root: ["pkg/a.py", "b.py"])
    calls = []

    def _fake_run(command):
        calls.append(command)
        return 0, "", ""

    monkeypatch.setattr(stop_hook, "_run", _fake_run)

    assert stop_hook.main() == 0
    assert capsys.readouterr().out == ""
    assert calls == [
        _ruff(stop_hook, "check", "--fix", "pkg/a.py", "b.py"),
        _ruff(stop_hook, "format", "pkg/a.py", "b.py"),
        _ruff(stop_hook, "check", "."),
        _ruff(stop_hook, "format", "--check", "."),
    ]


@pytest.mark.parametrize("value", ["0", "false", "NO", " off "])
def test_opt_out_values_make_stop_check_only(stop_hook, monkeypatch, value: str) -> None:
    monkeypatch.setenv(stop_hook.STOP_FIX_ENV_VAR, value)
    monkeypatch.setattr(
        stop_hook, "_changed_python_files", lambda root: pytest.fail("git must not run")
    )
    calls = []

    def _fake_run(command):
        calls.append(command)
        return 0, "", ""

    monkeypatch.setattr(stop_hook, "_run", _fake_run)

    assert stop_hook.main() == 0
    assert calls == [
        _ruff(stop_hook, "check", "."),
        _ruff(stop_hook, "format", "--check", "."),
    ]
    assert not any("--fix" in command for command in calls)


def test_no_changed_files_never_runs_fixers(stop_hook, monkeypatch) -> None:
    monkeypatch.setattr(stop_hook, "_changed_python_files", lambda root: [])
    calls = []

    def _fake_run(command):
        calls.append(command)
        return 0, "", ""

    monkeypatch.setattr(stop_hook, "_run", _fake_run)

    assert stop_hook.main() == 0
    assert calls == [
        _ruff(stop_hook, "check", "."),
        _ruff(stop_hook, "format", "--check", "."),
    ]


def test_default_block_reason_omits_opt_out_hint(stop_hook, monkeypatch, capsys) -> None:
    monkeypatch.setattr(stop_hook, "_changed_python_files", lambda root: [])
    results = iter([(1, "still failing", ""), (0, "", "")])
    monkeypatch.setattr(stop_hook, "_run", lambda command: next(results))

    assert stop_hook.main() == 0
    message = json.loads(capsys.readouterr().out)
    assert "still failing" in message["hookSpecificOutput"]["reason"]
    assert stop_hook.STOP_FIX_ENV_VAR not in message["hookSpecificOutput"]["reason"]


def _fake_git(toplevel, porcelain: str):
    def _run(command):
        if "rev-parse" in command:
            return 0, f"{toplevel.as_posix()}\n", ""
        if "status" in command:
            return 0, porcelain + "\n", ""
        raise AssertionError(f"unexpected command: {command}")

    return _run


def test_changed_python_files_parses_git_status(stop_hook, monkeypatch, tmp_path) -> None:
    for name in ("kept.py", "renamed_new.py", "untracked.py", "notes.txt"):
        (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
    porcelain = "\n".join(
        [
            " M kept.py",
            "R  renamed_old.py -> renamed_new.py",
            "?? untracked.py",
            " D deleted.py",
            " M notes.txt",
            " M missing.py",
        ]
    )
    monkeypatch.setattr(stop_hook._impl, "_run", _fake_git(tmp_path, porcelain))

    assert stop_hook._changed_python_files(tmp_path) == [
        "kept.py",
        "renamed_new.py",
        "untracked.py",
    ]


def test_changed_python_files_is_empty_outside_git(stop_hook, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(stop_hook._impl, "_run", lambda command: (128, "", "fatal: not a git repo"))

    assert stop_hook._changed_python_files(tmp_path) == []


def test_block_payload_exposes_decision_for_every_harness(stop_hook, monkeypatch, capsys) -> None:
    monkeypatch.setattr(stop_hook, "_changed_python_files", lambda root: [])
    results = iter([(1, "E501 too long", ""), (0, "", "")])
    monkeypatch.setattr(stop_hook, "_run", lambda command: next(results))

    assert stop_hook.main() == 0
    message = json.loads(capsys.readouterr().out)
    # Claude Code reads the top-level keys; Codex and the Pi bridge read hookSpecificOutput.
    assert message["decision"] == "block"
    assert "E501 too long" in message["reason"]
    assert message["hookSpecificOutput"]["decision"] == "block"
    assert message["hookSpecificOutput"]["reason"] == message["reason"]


def test_changed_python_files_resolves_against_git_toplevel_from_subdirectory(
    stop_hook, monkeypatch, tmp_path
) -> None:
    package = tmp_path / "pkg"
    (package / "sub").mkdir(parents=True)
    (package / "sub" / "inside.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "outside.py").write_text("x = 1\n", encoding="utf-8")
    porcelain = "\n".join([" M pkg/sub/inside.py", "?? outside.py"])
    monkeypatch.setattr(stop_hook._impl, "_run", _fake_git(tmp_path, porcelain))

    # Porcelain paths are relative to the repository top level, not to the hook's cwd.
    assert stop_hook._changed_python_files(package) == ["sub/inside.py"]


def test_changed_python_files_uses_real_git_from_subdirectory(stop_hook, tmp_path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "root.py").write_text("x = 1\n", encoding="utf-8")

    assert stop_hook._changed_python_files(package) == ["mod.py"]
    assert stop_hook._changed_python_files(tmp_path) == ["pkg/mod.py", "root.py"]


def _set_payload(stop_hook, monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(stop_hook.sys, "stdin", io.StringIO(json.dumps(payload)))


def test_stop_hook_active_reports_without_blocking(stop_hook, monkeypatch, capsys) -> None:
    _set_payload(stop_hook, monkeypatch, {"stop_hook_active": True})
    monkeypatch.setattr(stop_hook, "_changed_python_files", lambda root: [])
    results = iter([(1, "E501 too long", ""), (0, "", "")])
    monkeypatch.setattr(stop_hook, "_run", lambda command: next(results))

    assert stop_hook.main() == 0
    message = json.loads(capsys.readouterr().out)
    assert "decision" not in message
    assert "hookSpecificOutput" not in message
    assert "E501 too long" in message["systemMessage"]
    assert "not blocked again" in message["systemMessage"]


@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_only_a_true_stop_hook_active_flag_disables_blocking(
    stop_hook, monkeypatch, capsys, value
) -> None:
    _set_payload(stop_hook, monkeypatch, {"stop_hook_active": value})
    monkeypatch.setattr(stop_hook, "_changed_python_files", lambda root: [])
    results = iter([(1, "E501 too long", ""), (0, "", "")])
    monkeypatch.setattr(stop_hook, "_run", lambda command: next(results))

    assert stop_hook.main() == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"


def test_stop_hook_active_still_runs_fixes_and_checks(stop_hook, monkeypatch, capsys) -> None:
    _set_payload(stop_hook, monkeypatch, {"stop_hook_active": True})
    monkeypatch.setattr(stop_hook, "_changed_python_files", lambda root: ["a.py"])
    calls = []

    def _fake_run(command):
        calls.append(command)
        return 0, "", ""

    monkeypatch.setattr(stop_hook, "_run", _fake_run)

    assert stop_hook.main() == 0
    assert capsys.readouterr().out == ""
    assert calls == [
        _ruff(stop_hook, "check", "--fix", "a.py"),
        _ruff(stop_hook, "format", "a.py"),
        _ruff(stop_hook, "check", "."),
        _ruff(stop_hook, "format", "--check", "."),
    ]
