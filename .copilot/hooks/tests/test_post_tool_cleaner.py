import io
import json
from types import SimpleNamespace

import pytest


def _set_stdio(module, monkeypatch, payload: dict):
    stdout = io.StringIO()
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(module.sys, "stdout", stdout)
    return stdout


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [
        ("ApplyPatch", True),
        ("functions.apply_patch", True),
        ("apply_patch", True),
        ("edit_notebook_file", True),
        ("replace_string_in_file", True),
        ("shell", False),
    ],
)
def test_should_lint(load_script_module, tool_name: str, expected: bool) -> None:
    cleaner = load_script_module("scripts/post_tool_cleaner.py", "post_tool_cleaner_should_lint")

    assert cleaner._should_lint(tool_name) is expected


def test_collect_python_paths_recurses_and_deduplicates(load_script_module, tmp_path) -> None:
    cleaner = load_script_module("scripts/post_tool_cleaner.py", "post_tool_cleaner_collect")
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("print('a')\n", encoding="utf-8")
    second.write_text("print('b')\n", encoding="utf-8")
    seen = set()

    cleaner._collect_python_paths(
        {
            "items": [
                str(first),
                {"nested": [str(second), str(first), str(tmp_path / "notes.txt")]},
            ]
        },
        seen,
    )

    assert seen == {first.resolve(), second.resolve()}


def test_run_ruff_builds_expected_command(load_script_module, monkeypatch, tmp_path) -> None:
    cleaner = load_script_module("scripts/post_tool_cleaner.py", "post_tool_cleaner_run_ruff")
    path = tmp_path / "module.py"
    path.write_text("print('x')\n", encoding="utf-8")
    recorded = {}

    def _fake_run(command, check, capture_output, text):
        recorded["command"] = command
        recorded["check"] = check
        recorded["capture_output"] = capture_output
        recorded["text"] = text
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cleaner.subprocess, "run", _fake_run)

    exit_code, stdout, stderr = cleaner._run_ruff("check", [path], "--fix")

    assert exit_code == 0
    assert stdout == ""
    assert stderr == ""
    assert recorded["command"] == [
        cleaner.sys.executable,
        "-m",
        "ruff",
        "check",
        "--fix",
        str(path),
    ]
    assert recorded["check"] is False
    assert recorded["capture_output"] is True
    assert recorded["text"] is True


def test_main_skips_when_repo_does_not_use_ruff(load_script_module, monkeypatch) -> None:
    cleaner = load_script_module("scripts/post_tool_cleaner.py", "post_tool_cleaner_skip")
    monkeypatch.setattr(cleaner, "repo_uses_ruff", lambda root: False)
    stdout = _set_stdio(
        cleaner, monkeypatch, {"tool_name": "apply_patch", "tool_input": {"path": "file.py"}}
    )

    assert cleaner.main() == 0
    assert stdout.getvalue() == ""


def test_main_skips_when_no_python_paths_are_present(load_script_module, monkeypatch) -> None:
    cleaner = load_script_module("scripts/post_tool_cleaner.py", "post_tool_cleaner_no_paths")
    monkeypatch.setattr(cleaner, "repo_uses_ruff", lambda root: True)
    stdout = _set_stdio(
        cleaner, monkeypatch, {"tool_name": "apply_patch", "tool_input": {"path": "notes.txt"}}
    )

    assert cleaner.main() == 0
    assert stdout.getvalue() == ""


def test_main_runs_ruff_commands_in_order(load_script_module, monkeypatch, tmp_path) -> None:
    cleaner = load_script_module("scripts/post_tool_cleaner.py", "post_tool_cleaner_main_order")
    path = tmp_path / "module.py"
    path.write_text("print('x')\n", encoding="utf-8")
    stdout = _set_stdio(
        cleaner, monkeypatch, {"tool_name": "apply_patch", "tool_input": {"path": str(path)}}
    )
    monkeypatch.setattr(cleaner, "repo_uses_ruff", lambda root: True)
    calls = []

    def _fake_run_ruff(command_name, paths, *args):
        calls.append((command_name, [item.name for item in paths], args))
        return 0, "", ""

    monkeypatch.setattr(cleaner, "_run_ruff", _fake_run_ruff)

    assert cleaner.main() == 0
    assert stdout.getvalue() == ""
    assert calls == [
        ("check", ["module.py"], ("--fix",)),
        ("format", ["module.py"], ()),
        ("check", ["module.py"], ()),
    ]


def test_main_emits_additional_context_for_remaining_ruff_issues(
    load_script_module, monkeypatch, tmp_path
) -> None:
    cleaner = load_script_module("scripts/post_tool_cleaner.py", "post_tool_cleaner_main_emit")
    path = tmp_path / "module.py"
    path.write_text("print('x')\n", encoding="utf-8")
    stdout = _set_stdio(
        cleaner, monkeypatch, {"tool_name": "apply_patch", "tool_input": {"path": str(path)}}
    )
    monkeypatch.setattr(cleaner, "repo_uses_ruff", lambda root: True)
    results = iter(
        [
            (1, "fixed one issue", ""),
            (0, "", ""),
            (1, "", "still failing"),
        ]
    )

    monkeypatch.setattr(cleaner, "_run_ruff", lambda *args: next(results))

    assert cleaner.main() == 0
    message = json.loads(stdout.getvalue())
    assert message["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "ruff check --fix" in message["hookSpecificOutput"]["additionalContext"]
    assert "ruff format" not in message["hookSpecificOutput"]["additionalContext"]
    assert "ruff check" in message["hookSpecificOutput"]["additionalContext"]
