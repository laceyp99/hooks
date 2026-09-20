import pytest


def _dot(name: str) -> str:
    return "." + name


def _join_suffix(name: str, suffix: str) -> str:
    return name + "." + suffix


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (_dot("env"), True),
        (_join_suffix(_dot("env"), "local"), True),
        ("config/app" + "." + "env", True),
        ("secrets" + "." + "secret", True),
        ("nested/" + _dot("direnv") + "/config", True),
        (_join_suffix(_dot("env"), "example"), False),
        (_join_suffix(_dot("env"), "sample"), False),
        ("README.md", False),
        ("", False),
    ],
)
def test_matches_env_path(pre_tool_security, value: str, expected: bool) -> None:
    assert pre_tool_security._matches_env_path(value) is expected


def test_matches_protected_git_internal_paths(
    pre_tool_security, git_internal_path, git_name: str
) -> None:
    values = [
        git_internal_path("config"),
        "repo/" + git_internal_path("HEAD"),
        "repo\\" + git_name + "\\objects\\ab",
        "./" + git_internal_path("hooks", "pre-commit"),
        _dot("github") + "/../" + git_internal_path("config"),
        _dot("github") + "/" + git_internal_path("config"),
    ]

    for value in values:
        assert pre_tool_security._matches_protected_git_path(value) is True


@pytest.mark.parametrize(
    "value",
    [
        _dot("gitignore"),
        _dot("gitattributes"),
        _dot("github") + "/workflows/test.yml",
        "src/git_helper.py",
        "",
    ],
)
def test_allows_normal_git_project_files(pre_tool_security, value: str) -> None:
    assert pre_tool_security._matches_protected_git_path(value) is False


@pytest.mark.parametrize(
    ("tool_name", "expected_check", "expected_git_check"),
    [
        ("Bash", True, False),
        ("functions.shell_command", True, False),
        ("ApplyPatch", True, True),
        ("apply_patch", True, True),
        ("read_file", True, False),
        ("rename", True, True),
        ("shell", True, False),
    ],
)
def test_tool_gating(
    pre_tool_security, tool_name: str, expected_check: bool, expected_git_check: bool
) -> None:
    assert pre_tool_security._should_check(tool_name) is expected_check
    assert pre_tool_security._should_check_git_paths(tool_name) is expected_git_check


def test_finds_protected_git_paths_in_nested_tool_input(
    pre_tool_security, git_internal_path
) -> None:
    payload = {
        "src": _dot("github") + "/workflows/test.yml",
        "dst": {
            "filePath": "repo/" + git_internal_path("hooks", "pre-commit"),
        },
    }

    assert pre_tool_security._find_protected_git_path(payload) == "repo/" + git_internal_path(
        "hooks", "pre-commit"
    )


def test_finds_env_paths_in_nested_tool_input(pre_tool_security) -> None:
    payload = {
        "paths": [
            "README.md",
            {"target": "config/" + _join_suffix(_dot("env"), "production")},
        ],
        "source": _join_suffix(_dot("env"), "example"),
    }

    assert pre_tool_security._find_env_path(payload) == "config/" + _join_suffix(
        _dot("env"), "production"
    )


def test_finds_env_paths_embedded_in_shell_commands(pre_tool_security) -> None:
    env_path = _join_suffix(_dot("env"), "local")
    payload = {"command": "Get-Content " + env_path}

    assert pre_tool_security._find_env_path(payload) == env_path


def test_finds_git_paths_embedded_in_shell_commands(pre_tool_security, git_internal_path) -> None:
    protected_path = git_internal_path("hooks", "pre-commit")
    payload = {"command": "Set-Content " + protected_path}

    assert pre_tool_security._find_protected_git_path(payload) == protected_path


def test_finds_protected_git_mutation_commands(pre_tool_security, git_internal_path) -> None:
    payloads = [
        {"command": "rm -rf " + git_internal_path()},
        {"command": "git rm -r " + git_internal_path()},
        {
            "command": "git mv "
            + git_internal_path("config")
            + " "
            + git_internal_path("config.bak")
        },
        {"command": "echo x > " + git_internal_path("config")},
        {"command": "Set-Content " + git_internal_path("config") + " x"},
        {"command": "Write-Output safe\nSet-Content " + git_internal_path("config") + " x"},
    ]

    for payload in payloads:
        assert pre_tool_security._find_protected_git_mutation_command(payload) is not None


def test_ignores_non_string_nested_values(pre_tool_security) -> None:
    payload = {
        "paths": [None, 3, {"nested": [False, {"deep": []}]}],
    }

    assert pre_tool_security._find_env_path(payload) is None
    assert pre_tool_security._find_protected_git_path(payload) is None


def test_inspects_patch_targets_but_not_patch_body(pre_tool_security) -> None:
    safe_patch = "*** Update File: README.md\n+Never read src/.env or write .git/config."
    blocked_patch = "*** Update File: .github/../.git/config\n+unsafe"

    assert pre_tool_security._find_env_path({"patch": safe_patch}) is None
    assert pre_tool_security._find_protected_git_path({"patch": safe_patch}) is None
    assert (
        pre_tool_security._find_protected_git_path({"patch": blocked_patch})
        == ".github/../.git/config"
    )


@pytest.mark.parametrize(
    ("tool_name", "checked", "mutating"),
    [
        ("Bash", True, False),
        ("Read", True, False),
        ("Edit", True, True),
        ("MultiEdit", True, True),
        ("Write", True, True),
        ("NotebookEdit", True, True),
        ("PowerShell", True, False),
        ("pwsh", True, False),
        ("Glob", False, False),
        ("Grep", False, False),
    ],
)
def test_claude_code_tool_names_are_recognized(
    pre_tool_security, tool_name: str, checked: bool, mutating: bool
) -> None:
    assert pre_tool_security._should_check(tool_name) is checked
    assert pre_tool_security._should_check_git_paths(tool_name) is mutating


def test_notebook_path_is_a_file_target(pre_tool_security, git_internal_path) -> None:
    payload = {"notebook_path": git_internal_path("config"), "new_source": "print(1)"}

    assert pre_tool_security._find_protected_git_path(payload) == git_internal_path("config")


def test_redirect_targets_are_the_only_access_on_a_non_access_segment(pre_tool_security) -> None:
    """``echo ".env" >> .gitignore`` appends to .gitignore. The other name is just text."""
    find = pre_tool_security._find_env_access_in_command
    target = _dot("env")

    assert find(f'echo "{target}" >> .gitignore') is None
    assert find(f"echo LEAK=1 >> {target}") == target
    assert find(f"echo x > {target}") == target


def test_git_is_an_access_verb_only_for_subcommands_that_touch_files(pre_tool_security) -> None:
    find = pre_tool_security._find_env_access_in_command
    target = _dot("env")

    assert find(f'git commit -m "document {target}"') is None
    assert find(f'git log --grep "{target}"') is None
    assert find(f"git add {target}") == target
    assert find(f"git checkout -- {target}") == target


def test_access_is_detected_past_prefixes_and_program_paths(pre_tool_security) -> None:
    find = pre_tool_security._find_env_access_in_command
    target = _dot("env")

    assert find(f"sudo cat {target}") == target
    assert find(f"FOO=bar cat {target}") == target
    assert find(f"/bin/cat {target}") == target
    assert find(f"ls -a; cat {target}") == target
    assert find(f"echo hi | cat {target}") == target


def test_safe_commands_are_not_git_mutations(pre_tool_security, git_internal_path) -> None:
    payloads = [
        {"command": "git status"},
        {"command": f"cat {git_internal_path('HEAD')}"},
        {"command": "rm -rf build"},
    ]

    for payload in payloads:
        assert pre_tool_security._find_protected_git_mutation_command(payload) is None


def test_patch_in_a_command_field_reports_only_its_header_target(
    pre_tool_security, git_internal_path
) -> None:
    """Codex sends apply_patch documents in ``command``, not ``patch``.

    The deny reason must name the file the patch targets, not echo the whole document back.
    """
    target = git_internal_path("config")
    patch = "\n".join(
        [
            "*** Begin Patch",
            f"*** Update File: {target}",
            "@@",
            "+# note",
            "*** End Patch",
        ]
    )

    assert pre_tool_security._find_protected_git_path({"command": patch}) == target


def test_patch_body_mentions_do_not_block_an_unrelated_file(
    pre_tool_security, git_internal_path
) -> None:
    """Editing prose that merely names a protected path is an ordinary edit."""
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: notes.md",
            "@@",
            f"-This project never touches {git_internal_path('config')} directly.",
            f"+This project never touches {git_internal_path('config')} at all.",
            "*** End Patch",
        ]
    )

    assert pre_tool_security._find_protected_git_path({"command": patch}) is None


@pytest.mark.parametrize("tool_name", ["PowerShell", "powershell", "pwsh", "functions.PowerShell"])
def test_powershell_is_gated_as_a_shell_tool(pre_tool_security, tool_name: str) -> None:
    # A PowerShell tool is a full shell. It must be inspected, but through its command rather
    # than as a direct file target, exactly like Bash.
    assert pre_tool_security._should_check(tool_name) is True
    assert pre_tool_security._should_check_git_paths(tool_name) is False
