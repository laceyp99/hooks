"""Regression tests locking in OpenCode's tool vocabulary.

OpenCode ships lowercase tool names (``bash``, ``edit``, ``write``, ``read``, ``glob``,
``grep``) and camelCase file-target fields (``filePath``) rather than Claude Code's
``file_path`` or Codex's mixed conventions. Nothing here is OpenCode-specific in the
production code: ``normalize_tool_name`` already lowercases tool names and the field-name
sets already collate case-insensitively, so ``filePath`` reaches the same ``filepath`` bucket
as every other host's spelling. These tests exist so a future edit to the tool-name sets
cannot silently stop covering OpenCode without a failing test to say so.
"""

import io
import json


def _dot(name: str) -> str:
    return "." + name


def _join_suffix(name: str, suffix: str) -> str:
    return name + "." + suffix


def _run_main(module, monkeypatch, payload_text: str):
    stdout = io.StringIO()
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(payload_text))
    monkeypatch.setattr(module.sys, "stdout", stdout)
    exit_code = module.main()
    return exit_code, stdout.getvalue()


# ---------------------------------------------------------------------------
# Tool-name gating: OpenCode's lowercase names must land in the right buckets.
# ---------------------------------------------------------------------------


def test_security_recognizes_opencode_file_tools(pre_tool_security) -> None:
    for tool_name in ("bash", "edit", "write", "read"):
        assert pre_tool_security._should_check(tool_name) is True

    # glob and grep only search; they never touch a file's contents directly.
    assert pre_tool_security._should_check("glob") is False
    assert pre_tool_security._should_check("grep") is False


def test_security_treats_edit_and_write_as_mutating_but_not_bash_or_read(
    pre_tool_security,
) -> None:
    assert pre_tool_security._should_check_git_paths("edit") is True
    assert pre_tool_security._should_check_git_paths("write") is True
    assert pre_tool_security._should_check_git_paths("bash") is False
    assert pre_tool_security._should_check_git_paths("read") is False


def test_dangerous_commands_only_checks_opencode_bash(
    pre_tool_dangerous_commands,
) -> None:
    assert pre_tool_dangerous_commands._should_check("bash") is True

    for tool_name in ("edit", "write", "read", "glob", "grep"):
        assert pre_tool_dangerous_commands._should_check(tool_name) is False


def test_post_tool_cleaner_lints_opencode_edit_and_write(load_script_module) -> None:
    cleaner = load_script_module(
        "scripts/post_tool_cleaner.py", "post_tool_cleaner_opencode_should_lint"
    )

    assert cleaner._should_lint("edit") is True
    assert cleaner._should_lint("write") is True

    for tool_name in ("bash", "read", "glob", "grep"):
        assert cleaner._should_lint(tool_name) is False


# ---------------------------------------------------------------------------
# Field-name gating: camelCase ``filePath`` must reach the same targets as
# every other host's spelling of the file-target field.
# ---------------------------------------------------------------------------


def test_camel_case_file_path_field_is_a_file_target(pre_tool_security) -> None:
    target = _dot("env")
    payload = {"filePath": target}

    assert pre_tool_security._find_env_path(payload) == target


def test_camel_case_file_path_field_reaches_protected_git_paths(
    pre_tool_security, git_internal_path
) -> None:
    blocked = git_internal_path("config")
    payload = {"filePath": blocked}

    assert pre_tool_security._find_protected_git_path(payload) == blocked


# ---------------------------------------------------------------------------
# End-to-end: OpenCode-shaped payloads through main().
# ---------------------------------------------------------------------------


def test_main_denies_opencode_read_of_env_file(pre_tool_security, monkeypatch) -> None:
    target = _dot("env")
    payload = {"tool_name": "read", "tool_input": {"filePath": target}}

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert target in message["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_denies_opencode_edit_of_env_file(pre_tool_security, monkeypatch) -> None:
    target = _dot("env")
    payload = {
        "tool_name": "edit",
        "tool_input": {"filePath": target, "oldString": "a", "newString": "b"},
    }

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_denies_opencode_write_of_env_file(pre_tool_security, monkeypatch) -> None:
    target = _dot("env")
    payload = {"tool_name": "write", "tool_input": {"filePath": target, "content": "X"}}

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_allows_opencode_read_of_env_example(pre_tool_security, monkeypatch) -> None:
    payload = {
        "tool_name": "read",
        "tool_input": {"filePath": _join_suffix(_dot("env"), "example")},
    }

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))

    assert exit_code == 0
    assert output == ""


def test_main_denies_opencode_edit_and_write_of_git_internals(
    pre_tool_security, git_internal_path, monkeypatch
) -> None:
    blocked = git_internal_path("config")

    for tool_name in ("edit", "write"):
        payload = {"tool_name": tool_name, "tool_input": {"filePath": blocked}}

        exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
        message = json.loads(output)

        assert exit_code == 0
        assert message["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert blocked in message["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_denies_opencode_bash_reading_env_file(pre_tool_security, monkeypatch) -> None:
    target = _dot("env")
    payload = {"tool_name": "bash", "tool_input": {"command": f"cat {target}"}}

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert target in message["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_allows_opencode_bash_git_commit_mentioning_env_file(
    pre_tool_security, monkeypatch
) -> None:
    """Known past regression: a commit message naming .env must not be denied."""
    target = _dot("env")
    payload = {
        "tool_name": "bash",
        "tool_input": {"command": f'git commit -m "fix {target} loading"'},
    }

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))

    assert exit_code == 0
    assert output == ""


def test_main_denies_opencode_bash_rm_rf_root(pre_tool_dangerous_commands, monkeypatch) -> None:
    payload = {"tool_name": "bash", "tool_input": {"command": "rm -rf /"}}

    exit_code, output = _run_main(pre_tool_dangerous_commands, monkeypatch, json.dumps(payload))
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_ignores_opencode_glob_and_grep_for_secret_patterns(
    pre_tool_security, monkeypatch
) -> None:
    """glob/grep only search; naming a secret file in a pattern is not access to it."""
    target = _dot("env")
    payloads = [
        {"tool_name": "glob", "tool_input": {"pattern": f"**/{target}"}},
        {"tool_name": "grep", "tool_input": {"pattern": target, "path": target}},
    ]

    for payload in payloads:
        exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))

        assert exit_code == 0
        assert output == ""


def test_opencode_write_of_python_file_is_linted(load_script_module, tmp_path) -> None:
    cleaner = load_script_module(
        "scripts/post_tool_cleaner.py", "post_tool_cleaner_opencode_collect"
    )
    target = tmp_path / "sample.py"
    target.write_text("print('x')\n", encoding="utf-8")
    seen: set = set()

    cleaner._collect_python_paths({"filePath": str(target)}, seen, tmp_path)

    assert seen == {target.resolve()}
