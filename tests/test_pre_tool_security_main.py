import io
import json
import sys


def _dot(name: str) -> str:
    return "." + name


def _join_suffix(name: str, suffix: str) -> str:
    return name + "." + suffix


def _run_main(module, monkeypatch, payload_text: str):
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload_text))
    monkeypatch.setattr(sys, "stdout", stdout)
    exit_code = module.main()
    return exit_code, stdout.getvalue()


class _BinaryStdin:
    """A stdin whose bytes are only reachable through ``buffer``, like a real pipe.

    Reading the text stream raises, so a test fails loudly if the payload reader stops
    preferring the binary buffer and silently re-introduces the decoding bug.
    """

    def __init__(self, raw: bytes) -> None:
        self.buffer = io.BytesIO(raw)

    def read(self):  # pragma: no cover - only reached on regression
        raise AssertionError("payload was read as text despite a binary buffer")


def _run_main_with_stdin(module, monkeypatch, stdin) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    exit_code = module.main()
    return exit_code, stdout.getvalue(), stderr.getvalue()


def test_main_denies_through_a_byte_order_mark(pre_tool_security, monkeypatch) -> None:
    """A BOM in front of the JSON must not disable the guard.

    PowerShell prepends one when a string is piped into a native program. Before this was
    handled the payload parsed as empty and the hook allowed the call.
    """
    target = _dot("env")
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": f"cat {target}"}})
    stdin = _BinaryStdin(b"\xef\xbb\xbf" + payload.encode("utf-8"))

    exit_code, output, _ = _run_main_with_stdin(pre_tool_security, monkeypatch, stdin)
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert target in message["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_denies_through_a_decoded_byte_order_mark(pre_tool_security, monkeypatch) -> None:
    """Same payload, but already decoded to text with the mark intact."""
    target = _dot("env")
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": f"cat {target}"}})

    exit_code, output = _run_main(pre_tool_security, monkeypatch, "\ufeff" + payload)
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_warns_when_the_payload_cannot_be_parsed(pre_tool_security, monkeypatch) -> None:
    """An unreadable payload still allows the call, but says so on stderr.

    A hook that cannot see the tool call has no grounds to deny it, so this stays fail-open.
    The warning is what keeps that decision visible in the host's debug log.
    """
    exit_code, output, errors = _run_main_with_stdin(
        pre_tool_security, monkeypatch, io.StringIO("not json at all")
    )

    assert exit_code == 0
    assert output == ""
    assert "could not parse" in errors
    assert "enforced nothing" in errors


def test_main_skips_non_file_tools(pre_tool_security, monkeypatch) -> None:
    payload = {"tool_name": "shell", "tool_input": {"file_path": "notes.txt"}}

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))

    assert exit_code == 0
    assert output == ""


def test_main_blocks_protected_git_commands_for_shell_tools(
    pre_tool_security, git_internal_path, monkeypatch
) -> None:
    payloads = [
        "rm -rf " + git_internal_path(),
        "git rm -r " + git_internal_path(),
        "git mv " + git_internal_path("config") + " " + git_internal_path("config.bak"),
    ]

    for blocked in payloads:
        payload = {"tool_name": "shell", "tool_input": {"command": blocked}}

        exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
        message = json.loads(output)

        assert exit_code == 0
        assert message["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert blocked in message["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_blocks_powershell_git_writes_for_shell_tools(
    pre_tool_security, git_internal_path, monkeypatch
) -> None:
    blocked = "Set-Content " + git_internal_path("config") + " x"
    payload = {"tool_name": "shell", "tool_input": {"command": blocked}}

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert blocked in message["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_blocks_env_like_paths(pre_tool_security, monkeypatch) -> None:
    blocked = "config/" + _join_suffix(_dot("env"), "production")
    payload = {"tool_name": "read_file", "tool_input": {"file_path": blocked}}

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert blocked in message["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_allows_a_protected_name_a_shell_command_only_mentions(
    pre_tool_security, monkeypatch
) -> None:
    """A commit message, a PR body, or a grep pattern carries the name as text.

    The git rule has always required a mutation verb before denying. Without the same gate here,
    writing about a secret file was as blocked as reading one.
    """
    target = _dot("env")
    mentions = [
        f'git commit -m "fix {target} loading"',
        f'gh pr create --body "Adds {target} support"',
        f'grep -rn "{target}" src/',
        f'echo "{target}" >> .gitignore',
    ]

    for command in mentions:
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))

        assert exit_code == 0
        assert output == "", command


def test_main_still_blocks_shell_commands_that_touch_the_file(
    pre_tool_security, monkeypatch
) -> None:
    target = _dot("env")
    accesses = [
        f"cat {target}",
        f"sudo cat {target}",
        f"ls -a; cat {target}",
        f"cp {target} /tmp/stolen",
        f"rm -f {target}",
        f"echo LEAK=1 >> {target}",
        f"git add {target}",
        f"Get-Content -LiteralPath {target} -Raw",
    ]

    for command in accesses:
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
        message = json.loads(output)

        assert exit_code == 0
        assert message["hookSpecificOutput"]["permissionDecision"] == "deny", command
        assert target in message["hookSpecificOutput"]["permissionDecisionReason"]


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


def test_main_blocks_traversal_to_git_internal_path(pre_tool_security, monkeypatch) -> None:
    blocked = ".github/../.git/config"
    payload = {
        "tool_name": "apply_patch",
        "tool_input": {"patch": f"*** Update File: {blocked}\n+unsafe"},
    }

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert blocked in message["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_allows_protected_text_in_patch_body(pre_tool_security, monkeypatch) -> None:
    payload = {
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": "*** Update File: README.md\n+Never read src/.env or write .git/config."
        },
    }

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))

    assert exit_code == 0
    assert output == ""


def test_main_allows_git_internal_paths_for_read_only_tools(
    pre_tool_security, git_internal_path, monkeypatch
) -> None:
    payloads = [
        {"tool_name": "read_file", "tool_input": {"file_path": git_internal_path("HEAD")}},
        {"tool_name": "shell", "tool_input": {"command": "git clone https://example.com/repo.git"}},
        {"tool_name": "shell", "tool_input": {"command": "git checkout feature"}},
    ]

    for payload in payloads:
        exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))

        assert exit_code == 0
        assert output == ""


def test_main_ignores_invalid_json(pre_tool_security, monkeypatch) -> None:
    exit_code, output = _run_main(pre_tool_security, monkeypatch, "not json")

    assert exit_code == 0
    assert output == ""


def test_main_blocks_env_paths_for_the_powershell_tool(pre_tool_security, monkeypatch) -> None:
    target = _dot("env")
    payload = {"tool_name": "PowerShell", "tool_input": {"command": f"Get-Content {target}"}}

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert target in message["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_blocks_git_mutations_for_the_powershell_tool(pre_tool_security, monkeypatch) -> None:
    target = "/".join((_dot("git"), "config"))
    payload = {
        "tool_name": "PowerShell",
        "tool_input": {"command": f"Remove-Item -Force {target}"},
    }

    exit_code, output = _run_main(pre_tool_security, monkeypatch, json.dumps(payload))
    message = json.loads(output)

    assert exit_code == 0
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"
