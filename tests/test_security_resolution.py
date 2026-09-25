"""Tests for resolving file targets on disk, and for Grep and MCP coverage.

Every link here is built for real in ``tmp_path``: symlinks, NTFS junctions, and hard links.
Protected names are assembled at runtime (``_dot("env")``) so this file never spells a secret
file name that a guard running over the repo itself would trip on.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agent_hooks import security  # noqa: E402

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="NTFS-specific behavior")


def _dot(name: str) -> str:
    return "." + name


ENV = _dot("env")
TEMPLATE = _dot("env") + ".example"
GIT = _dot("git")


def _decide(payload: dict[str, Any], monkeypatch) -> str | None:
    """Run the hook on ``payload`` and return ``"deny"`` or None for an allowed call.

    Prefers a pure ``evaluate(payload)`` when the module has one, and otherwise drives ``main``
    through stdin and stdout, so these tests survive the entry point being refactored.
    """
    evaluate = getattr(security, "evaluate", None)
    if callable(evaluate):
        result = evaluate(payload)
        if not result:
            return None
        if isinstance(result, str):
            result = json.loads(result)
        return result["hookSpecificOutput"]["permissionDecision"]

    stdout = io.StringIO()
    monkeypatch.setattr(security.sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(security.sys, "stdout", stdout)
    assert security.main() == 0
    output = stdout.getvalue().strip()
    if not output:
        return None
    return json.loads(output)["hookSpecificOutput"]["permissionDecision"]


def _call(tool_name: str, tool_input: dict[str, Any], cwd: Path) -> dict[str, Any]:
    return {"tool_name": tool_name, "tool_input": tool_input, "cwd": str(cwd)}


def _symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlinks unavailable (Developer Mode off?): {error}")


def _junction(link: Path, target: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("junctions are Windows-only")
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, AttributeError, OSError):
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip("could not create a junction")


def _short_path(path: Path) -> str:
    """Return the 8.3 short form of ``path``, or skip when the volume has none."""
    if sys.platform != "win32":
        pytest.skip("8.3 short names are Windows-only")
    import ctypes

    buffer = ctypes.create_unicode_buffer(1024)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    if not length or Path(buffer.value).name.lower() == path.name.lower():
        pytest.skip("8.3 short names are disabled on this volume")
    return buffer.value


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with a secret, a template, a .direnv dir, and Git internals."""
    (tmp_path / ENV).write_text("TOKEN=1\n", encoding="utf-8")
    (tmp_path / TEMPLATE).write_text("TOKEN=\n", encoding="utf-8")
    (tmp_path / "readme.md").write_text("hello\n", encoding="utf-8")
    (tmp_path / _dot("direnv")).mkdir()
    (tmp_path / _dot("direnv") / "cache").write_text("x\n", encoding="utf-8")
    (tmp_path / GIT / "hooks").mkdir(parents=True)
    (tmp_path / GIT / "config").write_text("[core]\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Symlinks, junctions, and the payload cwd
# ---------------------------------------------------------------------------


def test_read_through_a_file_symlink_to_a_secret_is_denied(project, monkeypatch) -> None:
    _symlink(project / "notes.txt", project / ENV)

    decision = _decide(_call("Read", {"file_path": "notes.txt"}, project), monkeypatch)

    assert decision == "deny"


def test_shell_reader_through_a_file_symlink_to_a_secret_is_denied(project, monkeypatch) -> None:
    _symlink(project / "notes.txt", project / ENV)

    payload = _call("Bash", {"command": "wc -c notes.txt"}, project)

    assert _decide(payload, monkeypatch) == "deny"


def test_read_through_a_file_symlink_to_a_template_is_allowed(project, monkeypatch) -> None:
    _symlink(project / "notes.txt", project / TEMPLATE)

    decision = _decide(_call("Read", {"file_path": "notes.txt"}, project), monkeypatch)

    assert decision is None


def test_relative_target_resolves_against_payload_cwd_not_process_cwd(
    project, tmp_path_factory, monkeypatch
) -> None:
    _symlink(project / "notes.txt", project / ENV)
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))

    decision = _decide(_call("Read", {"file_path": "notes.txt"}, project), monkeypatch)

    assert decision == "deny"


def test_missing_cwd_falls_back_to_process_cwd(project, monkeypatch) -> None:
    _symlink(project / "notes.txt", project / ENV)
    monkeypatch.chdir(project)

    decision = _decide({"tool_name": "Read", "tool_input": {"file_path": "notes.txt"}}, monkeypatch)

    assert decision == "deny"


def test_read_through_a_junction_into_direnv_is_denied(project, monkeypatch) -> None:
    _junction(project / "cachedir", project / _dot("direnv"))

    payload = _call("Read", {"file_path": str(project / "cachedir" / "cache")}, project)

    assert _decide(payload, monkeypatch) == "deny"


def test_write_of_a_new_file_through_a_junction_into_git_is_denied(project, monkeypatch) -> None:
    """The target does not exist yet; its linked parent still resolves into .git/hooks."""
    _junction(project / "tools", project / GIT / "hooks")

    payload = _call("Write", {"file_path": "tools/pre-commit", "content": "x"}, project)

    assert _decide(payload, monkeypatch) == "deny"


def test_read_through_a_junction_into_git_is_allowed(project, monkeypatch) -> None:
    """Reading Git internals is allowed, however the path gets there."""
    _junction(project / "gitdir", project / GIT)

    payload = _call("Read", {"file_path": "gitdir/config"}, project)

    assert _decide(payload, monkeypatch) is None


def test_junction_to_a_template_directory_is_allowed(project, monkeypatch) -> None:
    (project / "templates").mkdir()
    (project / "templates" / TEMPLATE).write_text("A=\n", encoding="utf-8")
    _junction(project / "tpl", project / "templates")

    payload = _call("Read", {"file_path": f"tpl/{TEMPLATE}"}, project)

    assert _decide(payload, monkeypatch) is None


def test_edit_through_a_symlinked_directory_into_git_is_denied(project, monkeypatch) -> None:
    _symlink(project / "cfg", project / GIT)

    payload = _call(
        "Edit", {"file_path": "cfg/config", "old_string": "a", "new_string": "b"}, project
    )

    assert _decide(payload, monkeypatch) == "deny"


def test_apply_patch_header_through_a_symlink_is_denied(project, monkeypatch) -> None:
    _symlink(project / "notes.txt", project / ENV)
    patch = "*** Begin Patch\n*** Update File: notes.txt\n@@\n-a\n+b\n*** End Patch\n"

    payload = _call("apply_patch", {"command": patch}, project)

    assert _decide(payload, monkeypatch) == "deny"


# ---------------------------------------------------------------------------
# Hard links
# ---------------------------------------------------------------------------


def test_hard_link_to_a_secret_in_the_same_directory_is_denied(project, monkeypatch) -> None:
    os.link(project / ENV, project / "notes.txt")

    decision = _decide(_call("Read", {"file_path": "notes.txt"}, project), monkeypatch)

    assert decision == "deny"


def test_shell_reader_through_a_hard_link_to_a_secret_is_denied(project, monkeypatch) -> None:
    os.link(project / ENV, project / "notes.txt")

    payload = _call("PowerShell", {"command": "(Get-Item notes.txt).Length"}, project)

    assert _decide(payload, monkeypatch) == "deny"


def test_hard_link_elsewhere_to_a_secret_in_cwd_is_denied(project, monkeypatch) -> None:
    os.link(project / ENV, project / "src" / "notes.txt")

    decision = _decide(_call("Read", {"file_path": "src/notes.txt"}, project), monkeypatch)

    assert decision == "deny"


def test_hard_link_to_a_template_is_allowed(project, monkeypatch) -> None:
    os.link(project / TEMPLATE, project / "notes.txt")

    decision = _decide(_call("Read", {"file_path": "notes.txt"}, project), monkeypatch)

    assert decision is None


def test_hard_link_between_ordinary_files_is_allowed(project, monkeypatch) -> None:
    os.link(project / "readme.md", project / "src" / "copy.md")

    decision = _decide(_call("Read", {"file_path": "src/copy.md"}, project), monkeypatch)

    assert decision is None


# ---------------------------------------------------------------------------
# NTFS streams, extended-length prefixes, and 8.3 short names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", [":foo", "::$DATA", ":foo:$DATA"])
def test_stream_suffix_on_a_secret_is_denied_by_name(suffix: str) -> None:
    assert security._matches_env_path(ENV + suffix) is True
    assert security._matches_env_path("C:\\repo\\" + ENV + suffix) is True


@pytest.mark.parametrize("suffix", [":foo", "::$DATA"])
def test_stream_suffix_on_a_template_is_allowed(suffix: str) -> None:
    assert security._matches_env_path(TEMPLATE + suffix) is False
    assert security._matches_env_path("C:\\repo\\" + TEMPLATE + suffix) is False


def test_drive_letter_is_not_mistaken_for_a_stream() -> None:
    assert security._matches_env_path("C:\\repo\\readme.md") is False
    assert security._matches_env_path("C:") is False
    assert security._strip_stream_suffixes("C:\\repo\\" + ENV + "::$DATA") == "C:\\repo\\" + ENV


def test_shell_token_with_a_stream_suffix_is_denied() -> None:
    assert security._find_env_access_in_command(f"cat {ENV}::$DATA") == f"{ENV}::$DATA"


def test_git_directory_stream_form_is_protected() -> None:
    assert security._matches_protected_git_path(f"{GIT}::$INDEX_ALLOCATION/config") is True


@windows_only
def test_stream_suffix_on_a_symlink_to_a_secret_is_denied(project, monkeypatch) -> None:
    _symlink(project / "notes.txt", project / ENV)

    decision = _decide(_call("Read", {"file_path": "notes.txt::$DATA"}, project), monkeypatch)

    assert decision == "deny"


@windows_only
@pytest.mark.parametrize("prefix", ["\\\\?\\", "\\\\.\\"])
def test_extended_prefix_is_stripped_before_resolving(project, monkeypatch, prefix: str) -> None:
    os.link(project / ENV, project / "notes.txt")

    payload = _call("Read", {"file_path": prefix + str(project / "notes.txt")}, project)

    assert _decide(payload, monkeypatch) == "deny"


@windows_only
def test_extended_prefix_on_a_template_is_allowed(project, monkeypatch) -> None:
    payload = _call("Read", {"file_path": "\\\\?\\" + str(project / TEMPLATE)}, project)

    assert _decide(payload, monkeypatch) is None


def test_extended_unc_prefix_is_unwrapped() -> None:
    assert security._strip_extended_prefix("\\\\?\\UNC\\srv\\share\\x") == "\\\\srv\\share\\x"
    assert security._strip_extended_prefix("\\\\?\\C:\\x") == "C:\\x"
    # Other device paths are not drive paths and are left alone.
    assert security._strip_extended_prefix("\\\\?\\GLOBALROOT\\x") == "\\\\?\\GLOBALROOT\\x"


def test_short_name_of_a_secret_is_denied(project, monkeypatch) -> None:
    secret = project / "production-credentials.secrets"
    secret.write_text("A=1\n", encoding="utf-8")
    short = _short_path(secret)
    assert security._matches_env_path(short) is False, "short form should hide the name"

    decision = _decide(_call("Read", {"file_path": short}, project), monkeypatch)

    assert decision == "deny"


def test_short_name_of_a_template_is_allowed(project, monkeypatch) -> None:
    short = _short_path(project / TEMPLATE)

    decision = _decide(_call("Read", {"file_path": short}, project), monkeypatch)

    assert decision is None


# ---------------------------------------------------------------------------
# Resolution never raises and never denies on its own
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "a\0b",
        pytest.param("x" * 40000, id="very-long"),
        "C:\\con\\..\\nul",
        "\\\\?\\GLOBALROOT\\Device\\x",
        "<>|?*",
        "\\\\nonexistent-host-for-tests\\share\\x",
        "does/not/exist.txt",
    ],
)
def test_resolution_never_raises(tmp_path, value: str) -> None:
    security._resolve_target(value, str(tmp_path))
    assert security._find_resolved_env_target(value, str(tmp_path)) is None
    assert security._find_resolved_git_target(value, str(tmp_path)) is None


def test_ordinary_nonexistent_target_is_allowed(project, monkeypatch) -> None:
    payload = _call("Write", {"file_path": "src/new_module.py", "content": "x"}, project)

    assert _decide(payload, monkeypatch) is None


def test_existing_name_only_denials_still_hold(project, monkeypatch) -> None:
    for tool_input in ({"file_path": ENV}, {"file_path": f"nested/{ENV}.local"}):
        assert _decide(_call("Read", tool_input, project), monkeypatch) == "deny"
    assert _decide(_call("Read", {"file_path": TEMPLATE}, project), monkeypatch) is None


# ---------------------------------------------------------------------------
# Grep: Claude Code's Grep and OpenCode's grep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", ["Grep", "grep"])
def test_grep_path_naming_a_secret_is_denied(project, monkeypatch, tool_name: str) -> None:
    payload = _call(tool_name, {"pattern": "TOKEN", "path": ENV}, project)

    assert _decide(payload, monkeypatch) == "deny"


@pytest.mark.parametrize("tool_name", ["Grep", "grep"])
def test_grep_path_resolving_to_a_secret_is_denied(project, monkeypatch, tool_name: str) -> None:
    os.link(project / ENV, project / "notes.txt")

    payload = _call(tool_name, {"pattern": "TOKEN", "path": "notes.txt"}, project)

    assert _decide(payload, monkeypatch) == "deny"


@pytest.mark.parametrize("tool_name", ["Grep", "grep"])
def test_grep_path_into_direnv_is_denied(project, monkeypatch, tool_name: str) -> None:
    payload = _call(tool_name, {"pattern": "x", "path": _dot("direnv")}, project)

    assert _decide(payload, monkeypatch) == "deny"


@pytest.mark.parametrize("tool_name", ["Grep", "grep"])
def test_grep_pattern_naming_a_secret_is_allowed(project, monkeypatch, tool_name: str) -> None:
    payload = _call(tool_name, {"pattern": ENV, "path": "src"}, project)

    assert _decide(payload, monkeypatch) is None


@pytest.mark.parametrize("tool_name", ["Grep", "grep"])
def test_grep_on_a_template_is_allowed(project, monkeypatch, tool_name: str) -> None:
    payload = _call(tool_name, {"pattern": "TOKEN", "path": TEMPLATE}, project)

    assert _decide(payload, monkeypatch) is None


@pytest.mark.parametrize(
    "glob",
    [
        ENV,
        f"**/{ENV}",
        f"{ENV}*",
        f"**/{ENV}.*",
        f"./{ENV}",
        f"config/{ENV}",
        "*" + ENV,
        f"{{{ENV},*.py}}",
        "*.{env,py}",
        "**/" + _dot("envrc"),
        "**/" + _dot("env") + ".development",
        "**/" + _dot("envrc") + ".local",
        "**/.secrets.local",
        "*.secret",
        "*.secrets",
        "*.pem",
        "**/credentials.json",
        "**/id_rsa",
        "**/" + _dot("direnv") + "/**",
        ENV.upper(),
    ],
)
def test_grep_glob_selecting_a_secret_is_denied(project, monkeypatch, glob: str) -> None:
    for payload in (
        _call("Grep", {"pattern": "x", "glob": glob}, project),
        _call("grep", {"pattern": "x", "include": glob}, project),
    ):
        assert _decide(payload, monkeypatch) == "deny", payload


@pytest.mark.parametrize(
    "glob",
    [
        "*",
        "**/*",
        "*.*",
        "*.py",
        "**/*.{ts,tsx}",
        "src/**/*.ts",
        TEMPLATE,
        f"**/{TEMPLATE}",
        ENV + ".{example,sample}",
        "!*.env",
    ],
)
def test_grep_glob_not_aimed_at_a_secret_is_allowed(project, monkeypatch, glob: str) -> None:
    for payload in (
        _call("Grep", {"pattern": "x", "glob": glob}, project),
        _call("grep", {"pattern": "x", "include": glob}, project),
    ):
        assert _decide(payload, monkeypatch) is None, payload


def test_glob_tool_is_still_unchecked(project, monkeypatch) -> None:
    """Glob returns paths, not contents."""
    payload = _call("Glob", {"pattern": f"**/{ENV}", "path": ENV}, project)

    assert _decide(payload, monkeypatch) is None


def test_brace_expansion_is_bounded() -> None:
    glob = "{a,b,c,d}" * 10

    assert len(security._expand_braces(glob)) <= security.MAX_GLOB_ALTERNATIVES


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


def test_mcp_tools_are_checked_and_only_mutating_names_get_the_git_rule() -> None:
    assert security._should_check("mcp__filesystem__read_file") is True
    assert security._should_check_git_paths("mcp__filesystem__read_file") is False
    for tool in ("write_file", "edit_file", "create_directory", "move_file", "delete", "rename"):
        assert security._should_check_git_paths(f"mcp__filesystem__{tool}") is True, tool
    # The server name does not count, only the tool part.
    assert security._should_check_git_paths("mcp__editor__read_file") is False


def test_mcp_read_of_a_secret_is_denied(project, monkeypatch) -> None:
    payload = _call("mcp__filesystem__read_file", {"path": str(project / ENV)}, project)

    assert _decide(payload, monkeypatch) == "deny"


@pytest.mark.parametrize("field", ["file", "filePath", "local_file", "localPath", "upload_path"])
def test_mcp_upload_of_a_secret_is_denied(project, monkeypatch, field: str) -> None:
    payload = _call("mcp__browser__file_upload", {field: str(project / ENV)}, project)

    assert _decide(payload, monkeypatch) == "deny"


def test_mcp_read_of_several_files_including_a_secret_is_denied(project, monkeypatch) -> None:
    tool_input = {"paths": ["readme.md", ENV]}

    assert (
        _decide(_call("mcp__fs__read_multiple_files", tool_input, project), monkeypatch) == "deny"
    )


def test_mcp_read_through_a_hard_link_is_denied(project, monkeypatch) -> None:
    os.link(project / ENV, project / "notes.txt")

    payload = _call("mcp__filesystem__read_text_file", {"path": "notes.txt"}, project)

    assert _decide(payload, monkeypatch) == "deny"


def test_mcp_read_of_a_template_is_allowed(project, monkeypatch) -> None:
    payload = _call("mcp__filesystem__read_file", {"path": TEMPLATE}, project)

    assert _decide(payload, monkeypatch) is None


def test_mcp_write_into_git_is_denied_and_read_is_allowed(project, monkeypatch) -> None:
    target = f"{GIT}/config"

    write = _call("mcp__filesystem__write_file", {"path": target, "content": "x"}, project)
    read = _call("mcp__filesystem__read_file", {"path": target}, project)

    assert _decide(write, monkeypatch) == "deny"
    assert _decide(read, monkeypatch) is None


def test_mcp_move_through_a_junction_into_git_is_denied(project, monkeypatch) -> None:
    _junction(project / "tools", project / GIT / "hooks")

    tool_input = {"source": "readme.md", "destination": "tools/pre-commit"}

    assert _decide(_call("mcp__filesystem__move_file", tool_input, project), monkeypatch) == "deny"


def test_mcp_string_fields_are_not_commands(project, monkeypatch) -> None:
    """Only file-target fields count; a body, query, or even ``command`` field is data."""
    payloads = [
        _call("mcp__github__create_issue", {"title": "x", "body": f"fix {ENV} loading"}, project),
        _call("mcp__db__query", {"sql": f"select '{ENV}'"}, project),
        _call("mcp__runner__exec", {"command": f"cat {ENV}"}, project),
        _call("mcp__memory__store", {}, project),
    ]

    for payload in payloads:
        assert _decide(payload, monkeypatch) is None, payload


# ---------------------------------------------------------------------------
# Shell parsing: unchanged semantics, and the misses that stay out of scope
# ---------------------------------------------------------------------------


def test_interpreter_argument_naming_a_secret_is_denied() -> None:
    """Pinned rather than xfail: the interpreter gate already denies this literal form."""
    assert security._find_env_access_in_command(f"python -c \"open('{ENV}')\"") == ENV


def test_printf_constructed_path_is_denied() -> None:
    command = "cat \"$(printf '.%s' env)\""

    assert security._find_env_access_in_command(command) is not None


def test_interpreter_concatenated_name_is_denied() -> None:
    command = "python -c \"open('.e' + 'nv').read()\""

    assert security._find_env_access_in_command(command) is not None


@pytest.mark.parametrize("command", ["git add -A", "git add .", "git commit -am wip"])
@pytest.mark.xfail(strict=True, reason="bulk staging never names the file; needs repo state")
def test_bulk_staging_is_denied(command: str) -> None:
    assert security._find_env_access_in_command(command) is not None


# ---------------------------------------------------------------------------
# A session whose own working directory sits inside a protected directory. The ancestor rules
# judge the target, so they must not fire on the part of the path the session simply lives in.
# ---------------------------------------------------------------------------


def test_ordinary_file_is_allowed_when_cwd_is_inside_direnv(project, monkeypatch) -> None:
    workdir = project / _dot("direnv") / "proj"
    workdir.mkdir()
    (workdir / "app.py").write_text("x = 1\n", encoding="utf-8")

    payload = _call("Read", {"file_path": "app.py"}, workdir)

    assert _decide(payload, monkeypatch) is None


def test_ordinary_file_is_allowed_when_cwd_is_inside_git(project, monkeypatch) -> None:
    workdir = project / GIT / "hooks"
    (workdir / "notes.md").write_text("x\n", encoding="utf-8")

    payload = _call("Write", {"file_path": "notes.md"}, workdir)

    assert _decide(payload, monkeypatch) is None


def test_secret_is_still_denied_when_cwd_is_inside_direnv(project, monkeypatch) -> None:
    workdir = project / _dot("direnv") / "proj"
    workdir.mkdir()
    (workdir / ENV).write_text("TOKEN=1\n", encoding="utf-8")

    payload = _call("Read", {"file_path": ENV}, workdir)

    assert _decide(payload, monkeypatch) == "deny"


def test_target_outside_cwd_still_sees_its_protected_ancestor(project, monkeypatch) -> None:
    workdir = project / "src"
    target = project / _dot("direnv") / "cache"

    payload = _call("Read", {"file_path": str(target)}, workdir)

    assert _decide(payload, monkeypatch) == "deny"


def test_git_internals_under_cwd_are_still_denied(project, monkeypatch) -> None:
    payload = _call("Write", {"file_path": f"{GIT}/config"}, project)

    assert _decide(payload, monkeypatch) == "deny"
