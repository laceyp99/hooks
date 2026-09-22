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
import re
from pathlib import Path


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

    # glob returns paths only. grep returns file contents, so its path and include are checked.
    assert pre_tool_security._should_check("glob") is False
    assert pre_tool_security._should_check("grep") is True


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
    """Naming a secret file in a search pattern is not access to it.

    grep's ``path`` is a real target and is checked; its regex ``pattern`` never is.
    """
    target = _dot("env")
    payloads = [
        {"tool_name": "glob", "tool_input": {"pattern": f"**/{target}"}},
        {"tool_name": "grep", "tool_input": {"pattern": target, "path": "src"}},
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


# ---------------------------------------------------------------------------
# apply_patch: OpenCode sends its patch in ``patchText``, which is not a known
# field name. Only the patch's opening marker identifies it.
# ---------------------------------------------------------------------------


def _patch_text(header: str, path: str) -> str:
    return f"*** Begin Patch\n*** {header} File: {path}\n+x = 1\n*** End Patch"


def test_apply_patch_is_checked_and_linted(pre_tool_security, load_script_module) -> None:
    cleaner = load_script_module(
        "scripts/post_tool_cleaner.py", "post_tool_cleaner_opencode_apply_patch"
    )

    assert pre_tool_security._should_check("apply_patch") is True
    assert pre_tool_security._should_check_git_paths("apply_patch") is True
    assert cleaner._should_lint("apply_patch") is True


def test_main_denies_opencode_apply_patch_touching_env_file(pre_tool_security, monkeypatch) -> None:
    target = _dot("env")
    payload = {
        "tool_name": "apply_patch",
        "tool_input": {"patchText": _patch_text("Update", target)},
    }

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert target in message["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_allows_opencode_apply_patch_of_ordinary_file(pre_tool_security, monkeypatch) -> None:
    payload = {
        "tool_name": "apply_patch",
        "tool_input": {"patchText": _patch_text("Add", "src/sample.py")},
    }

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))

    assert exit_code == 0
    assert output == ""


# ---------------------------------------------------------------------------
# The OpenCode plugin gates which tools reach the guards, to avoid paying for
# Python startups on read-only calls. These read the gate lists straight out of
# the plugin source so the two sides cannot drift apart unnoticed.
# ---------------------------------------------------------------------------

PLUGIN_TEMPLATE = Path(__file__).resolve().parents[3] / ".opencode" / "agent-hooks.example.ts"


def _plugin_string_list(declaration: str) -> set[str]:
    source = PLUGIN_TEMPLATE.read_text(encoding="utf-8")
    start = source.index(declaration)
    end = source.index("]", start)
    return set(re.findall(r'"([^"]+)"', source[start:end]))


def test_plugin_skip_list_never_skips_a_tool_the_guards_check(
    pre_tool_security, pre_tool_dangerous_commands
) -> None:
    """Skipping is only safe for tools no guard would have inspected anyway."""
    inert_tools = _plugin_string_list("const INERT_TOOLS")

    assert inert_tools, "INERT_TOOLS not found in the plugin"
    for tool_name in inert_tools:
        assert pre_tool_security._should_check(tool_name) is False, tool_name
        assert pre_tool_dangerous_commands._should_check(tool_name) is False, tool_name


def test_plugin_write_markers_match_the_cleaner() -> None:
    # The scripts/ wrapper does not re-export the marker tuple, so read the implementation.
    from agent_hooks import post_tool_cleaner

    assert _plugin_string_list("const WRITE_TOOL_MARKERS") == set(
        post_tool_cleaner.WRITE_TOOL_MARKERS
    )
