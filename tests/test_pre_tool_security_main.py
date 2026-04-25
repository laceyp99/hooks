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


def test_main_skips_non_file_tools(pre_tool_security, monkeypatch) -> None:
    payload = {"tool_name": "shell", "tool_input": {"file_path": "notes.txt"}}

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))

    assert exit_code == 0
    assert output == ""


def test_main_blocks_env_like_paths(pre_tool_security, monkeypatch) -> None:
    blocked = "config/" + _join_suffix(_dot("env"), "production")
    payload = {"tool_name": "read_file", "tool_input": {"file_path": blocked}}

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert blocked in message["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_blocks_git_internal_paths_for_mutating_tools(
    pre_tool_security, git_internal_path, monkeypatch
) -> None:
    blocked = git_internal_path("hooks", "pre-commit")
    payload = {"tool_name": "apply_patch", "tool_input": {"file_path": blocked}}

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert blocked in message["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_allows_git_internal_paths_for_read_only_tools(
    pre_tool_security, git_internal_path, monkeypatch
) -> None:
    payload = {"tool_name": "read_file", "tool_input": {"file_path": git_internal_path("HEAD")}}

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))

    assert exit_code == 0
    assert output == ""


def test_main_ignores_invalid_json(pre_tool_security, monkeypatch) -> None:
    exit_code, output = _run_main(pre_tool_security, monkeypatch, "not json")

    assert exit_code == 0
    assert output == ""
