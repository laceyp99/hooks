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
        ("apply_patch", True, True),
        ("read_file", True, False),
        ("rename", True, True),
        ("shell", False, False),
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
        "paths": ["README.md", {"target": "config/" + _join_suffix(_dot("env"), "production")}],
        "source": _join_suffix(_dot("env"), "example"),
    }

    assert pre_tool_security._find_env_path(payload) == "config/" + _join_suffix(
        _dot("env"), "production"
    )


def test_ignores_non_string_nested_values(pre_tool_security) -> None:
    payload = {
        "paths": [None, 3, {"nested": [False, {"deep": []}]}],
    }

    assert pre_tool_security._find_env_path(payload) is None
    assert pre_tool_security._find_protected_git_path(payload) is None
