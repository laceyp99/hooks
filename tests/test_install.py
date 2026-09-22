"""End-to-end tests for install.ps1 against a throwaway user profile.

The installer is interactive, so each run shadows ``Read-Host`` with a function that answers
yes; PowerShell resolves functions before cmdlets. ``USERPROFILE`` and ``XDG_CONFIG_HOME`` point
into ``tmp_path`` for the child process only, so the real profile is never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "install.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or POWERSHELL is None,
    reason="install.ps1 is exercised only on Windows with PowerShell available",
)

LEGACY_SCRIPTS = (
    "pre_tool_security.py",
    "pre_tool_dangerous_commands.py",
    "post_tool_cleaner.py",
    "session_stop.py",
    "ruff_support.py",
)


def _legacy_claude_hook(script: str, timeout: int = 30) -> dict:
    return {
        "type": "command",
        "command": (
            f'python "$HOME/.claude/hooks/run_hook.py" "$HOME/.claude/hooks/scripts/{script}"'
        ),
        "timeout": timeout,
    }


def _legacy_codex_hook(script: str, status: str, timeout: int = 30) -> dict:
    runner = '"$HOME/.codex/hooks/run_hook.py"'
    target = f'"$HOME/.codex/hooks/scripts/{script}"'
    return {
        "type": "command",
        "command": f"python3 {runner} {target}",
        "commandWindows": f"python {runner} {target}",
        "timeout": timeout,
        "statusMessage": status,
    }


USER_HOOK = {"type": "command", "command": "echo my own hook", "timeout": 5}


def _legacy_claude_settings() -> dict:
    """What the installer wrote before the single runner, plus a user's own settings."""
    return {
        "model": "keep-me",
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash|PowerShell|Edit|MultiEdit|Write|NotebookEdit|Read",
                    "hooks": [
                        _legacy_claude_hook("pre_tool_security.py"),
                        _legacy_claude_hook("pre_tool_dangerous_commands.py"),
                    ],
                },
                {"matcher": "WebFetch", "hooks": [USER_HOOK]},
            ],
            "PostToolUse": [
                {
                    "matcher": "Edit|MultiEdit|Write|NotebookEdit",
                    "hooks": [_legacy_claude_hook("post_tool_cleaner.py")],
                }
            ],
            "Stop": [{"hooks": [_legacy_claude_hook("session_stop.py", 120), USER_HOOK]}],
        },
    }


def _legacy_codex_config() -> dict:
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": ".*",
                    "hooks": [
                        _legacy_codex_hook("pre_tool_security.py", "Checking protected paths"),
                        USER_HOOK,
                    ],
                },
                # A second container holding the other legacy pre-tool entry on its own. The
                # migration must remove the entry and the container it leaves empty.
                {
                    "matcher": "Bash",
                    "hooks": [
                        _legacy_codex_hook(
                            "pre_tool_dangerous_commands.py", "Checking dangerous commands"
                        )
                    ],
                },
            ],
            "PostToolUse": [
                {
                    "matcher": ".*",
                    "hooks": [
                        _legacy_codex_hook("post_tool_cleaner.py", "Cleaning edited Python files")
                    ],
                }
            ],
            "Stop": [
                {
                    "hooks": [
                        _legacy_codex_hook("session_stop.py", "Running session stop checks", 120)
                    ]
                }
            ],
        }
    }


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_legacy_bundle(hooks_dir: Path) -> Path:
    scripts = hooks_dir / "scripts"
    (scripts / "__pycache__").mkdir(parents=True)
    for name in LEGACY_SCRIPTS:
        (scripts / name).write_text("# legacy\n", encoding="utf-8")
        stem = name.removesuffix(".py")
        (scripts / "__pycache__" / f"{stem}.cpython-312.pyc").write_bytes(b"")
    (hooks_dir / "run_hook.py").write_text("# legacy runner\n", encoding="utf-8")
    return scripts


def _run_installer(profile: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["USERPROFILE"] = str(profile)
    env["XDG_CONFIG_HOME"] = str(profile / "xdg")
    script = (
        f"function global:Read-Host {{ param($Prompt) 'y' }}; & '{INSTALLER}'; exit $LASTEXITCODE"
    )
    completed = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=env,
        cwd=profile,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed


def _all_hooks(config: dict, event: str) -> list[dict]:
    return [hook for container in config["hooks"][event] for hook in container["hooks"]]


def _backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(path.name + ".bak-*"))


@pytest.fixture
def legacy_profile(tmp_path) -> Path:
    profile = tmp_path / "profile"
    _write_json(profile / ".claude" / "settings.json", _legacy_claude_settings())
    _write_json(profile / ".codex" / "hooks.json", _legacy_codex_config())
    _seed_legacy_bundle(profile / ".claude" / "hooks")
    codex_scripts = _seed_legacy_bundle(profile / ".codex" / "hooks")
    (codex_scripts / "my_notes.txt").write_text("mine\n", encoding="utf-8")
    package = profile / "src" / "agent_hooks"
    package.mkdir(parents=True)
    (package / "bootstrap.py").write_text("# legacy\n", encoding="utf-8")
    return profile


def test_migrates_a_legacy_claude_install_to_one_pre_tool_entry(legacy_profile) -> None:
    settings_path = legacy_profile / ".claude" / "settings.json"
    template = _read_json(REPO_ROOT / ".claude" / "settings.example.json")

    _run_installer(legacy_profile)
    settings = _read_json(settings_path)

    assert settings["model"] == "keep-me"
    assert len(_backups(settings_path)) == 1
    assert "scripts" not in json.dumps(settings["hooks"])

    pre_tool = settings["hooks"]["PreToolUse"]
    managed = [container for container in pre_tool if container["matcher"] != "WebFetch"]
    assert len(managed) == 1
    assert managed[0]["matcher"] == template["hooks"]["PreToolUse"][0]["matcher"]
    assert managed[0]["hooks"] == template["hooks"]["PreToolUse"][0]["hooks"]
    assert {"matcher": "WebFetch", "hooks": [USER_HOOK]} in pre_tool

    for event in ("PostToolUse", "Stop"):
        managed_hooks = [hook for hook in _all_hooks(settings, event) if hook != USER_HOOK]
        assert managed_hooks == template["hooks"][event][0]["hooks"], event
    assert USER_HOOK in _all_hooks(settings, "Stop")


def test_migrates_a_legacy_codex_install_and_drops_the_emptied_container(legacy_profile) -> None:
    config_path = legacy_profile / ".codex" / "hooks.json"
    template = _read_json(REPO_ROOT / ".codex" / "hooks.example.json")

    completed = _run_installer(legacy_profile)
    config = _read_json(config_path)

    assert len(_backups(config_path)) == 1
    assert "trust them in the Codex TUI" in completed.stdout
    assert "scripts" not in json.dumps(config["hooks"])

    pre_tool = config["hooks"]["PreToolUse"]
    assert len(pre_tool) == 1, pre_tool
    assert pre_tool[0]["matcher"] == ".*"
    assert pre_tool[0]["hooks"] == [template["hooks"]["PreToolUse"][0]["hooks"][0], USER_HOOK]

    for event in ("PostToolUse", "Stop"):
        assert _all_hooks(config, event) == template["hooks"][event][0]["hooks"], event


def test_replaces_the_runner_and_removes_the_stale_scripts(legacy_profile) -> None:
    _run_installer(legacy_profile)

    runner = (REPO_ROOT / "bin" / "run_hook.py").read_bytes()
    for harness in (".claude", ".codex"):
        assert (legacy_profile / harness / "hooks" / "run_hook.py").read_bytes() == runner

    assert not (legacy_profile / ".claude" / "hooks" / "scripts").exists()

    # A file the installer did not put there keeps the directory, and only that file remains.
    codex_scripts = legacy_profile / ".codex" / "hooks" / "scripts"
    assert sorted(path.name for path in codex_scripts.iterdir()) == ["my_notes.txt"]

    package = legacy_profile / "src" / "agent_hooks"
    assert (package / "dispatch.py").is_file()
    assert not (package / "bootstrap.py").exists()


def test_a_second_run_changes_nothing(legacy_profile) -> None:
    _run_installer(legacy_profile)
    settings_path = legacy_profile / ".claude" / "settings.json"
    config_path = legacy_profile / ".codex" / "hooks.json"
    first = (settings_path.read_text("utf-8"), config_path.read_text("utf-8"))

    completed = _run_installer(legacy_profile)

    assert (settings_path.read_text("utf-8"), config_path.read_text("utf-8")) == first
    assert "Claude Code config already has the Agent Hooks entries." in completed.stdout
    assert "Codex config already has the Agent Hooks entries." in completed.stdout
    assert len(_backups(settings_path)) == 1
    assert len(_backups(config_path)) == 1


def test_a_fresh_profile_gets_the_templates(tmp_path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()

    _run_installer(profile)

    assert _read_json(profile / ".claude" / "settings.json") == _read_json(
        REPO_ROOT / ".claude" / "settings.example.json"
    )
    assert _read_json(profile / ".codex" / "hooks.json") == _read_json(
        REPO_ROOT / ".codex" / "hooks.example.json"
    )
    assert (profile / ".claude" / "hooks" / "run_hook.py").is_file()
    assert not (profile / ".claude" / "hooks" / "scripts").exists()


def test_the_installed_runner_denies_from_a_project_directory(legacy_profile, tmp_path) -> None:
    _run_installer(legacy_profile)
    project = tmp_path / "project"
    project.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    payload = {"tool_name": "Bash", "tool_input": {"command": "cat " + "." + "env"}}

    completed = subprocess.run(
        [sys.executable, str(legacy_profile / ".codex" / "hooks" / "run_hook.py"), "pre-tool"],
        cwd=project,
        env=env,
        input=json.dumps(payload),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
