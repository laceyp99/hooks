"""Tests for ``bin/run_hook.py``, the one runner every harness launches."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
RUNNER_PATH = REPO_ROOT / "bin" / "run_hook.py"


def test_fallback_command_stays_in_process_when_supported(run_hook_module) -> None:
    assert run_hook_module._fallback_command((3, 11, 0), "win32") == []
    assert run_hook_module._fallback_command((3, 10, 0), "linux") == []


def test_fallback_command_uses_py_310_when_current_is_too_old(run_hook_module) -> None:
    assert run_hook_module._fallback_command((3, 9, 9), "win32") == ["py", "-3.10"]


def test_fallback_command_gives_up_when_too_old_and_no_windows_fallback(run_hook_module) -> None:
    assert run_hook_module._fallback_command((3, 9, 9), "linux") is None


def test_main_reports_an_unusable_interpreter(run_hook_module, monkeypatch) -> None:
    stderr = io.StringIO()
    monkeypatch.setattr(run_hook_module.sys, "version_info", (3, 9, 9))
    monkeypatch.setattr(run_hook_module.sys, "platform", "linux")
    monkeypatch.setattr(run_hook_module.sys, "stderr", stderr)

    assert run_hook_module.main() == 2
    assert "no compatible fallback" in stderr.getvalue()


def test_main_hands_off_to_py_310_with_passthrough_stdio(run_hook_module, monkeypatch) -> None:
    recorded = {}
    monkeypatch.setattr(run_hook_module.sys, "version_info", (3, 9, 9))
    monkeypatch.setattr(run_hook_module.sys, "platform", "win32")
    monkeypatch.setattr(run_hook_module.sys, "argv", ["run_hook.py", "pre-tool"])

    def _fake_run(command, check, **kwargs):
        recorded["command"] = command
        recorded["check"] = check
        recorded["kwargs"] = kwargs
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(run_hook_module.subprocess, "run", _fake_run)

    assert run_hook_module.main() == 7
    assert recorded["command"] == ["py", "-3.10", str(RUNNER_PATH.resolve()), "pre-tool"]
    assert recorded["check"] is False
    # No stdin/stdout/stderr arguments: the child inherits this process's streams, so the
    # payload reaches it without the old interpreter reading or re-encoding anything.
    assert recorded["kwargs"] == {}


def test_main_returns_error_when_python_launch_fails(run_hook_module, monkeypatch) -> None:
    stderr = io.StringIO()
    monkeypatch.setattr(run_hook_module.sys, "version_info", (3, 9, 9))
    monkeypatch.setattr(run_hook_module.sys, "platform", "win32")
    monkeypatch.setattr(run_hook_module.sys, "argv", ["run_hook.py", "pre-tool"])
    monkeypatch.setattr(run_hook_module.sys, "stderr", stderr)

    def _raise(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(run_hook_module.subprocess, "run", _raise)

    assert run_hook_module.main() == 2
    assert "Unable to launch hook interpreter" in stderr.getvalue()


def test_main_dispatches_in_process_without_a_subprocess(run_hook_module, monkeypatch) -> None:
    """A supported interpreter runs the hook itself: no re-exec, no second interpreter."""
    import agent_hooks.dispatch as dispatch

    recorded = {}
    monkeypatch.setattr(run_hook_module.sys, "argv", ["run_hook.py", "stop"])
    monkeypatch.setattr(
        run_hook_module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not re-exec")),
    )

    def _fake_main(argv):
        recorded["argv"] = argv
        return 5

    monkeypatch.setattr(dispatch, "main", _fake_main)

    assert run_hook_module.main() == 5
    assert recorded["argv"] == ["stop"]


def test_find_src_dir_uses_the_checkout_layout(run_hook_module) -> None:
    assert run_hook_module._find_src_dir() == SRC_DIR


def _install_runner(profile: Path, harness_dir: str) -> Path:
    """Lay the runner and package out the way install.ps1 does under a user profile."""
    hooks_dir = profile / harness_dir / "hooks"
    hooks_dir.mkdir(parents=True)
    shutil.copy2(RUNNER_PATH, hooks_dir / "run_hook.py")
    shutil.copytree(
        SRC_DIR / "agent_hooks",
        profile / "src" / "agent_hooks",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return hooks_dir / "run_hook.py"


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def test_installed_runner_finds_the_package_in_the_profile(tmp_path) -> None:
    """The installed layout: ``~/.claude/hooks/run_hook.py`` and ``~/src/agent_hooks``."""
    profile = tmp_path / "profile"
    runner = _install_runner(profile, ".claude")
    project = tmp_path / "project"
    project.mkdir()
    payload = {"tool_name": "Bash", "tool_input": {"command": "cat " + "." + "env"}}

    completed = subprocess.run(
        [sys.executable, str(runner), "pre-tool"],
        cwd=project,
        env=_clean_env(),
        input=json.dumps(payload),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr
    message = json.loads(completed.stdout)
    assert message["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_runner_ignores_an_agent_hooks_package_in_the_project(tmp_path) -> None:
    """The package is found next to the runner, never in the project being guarded."""
    profile = tmp_path / "profile"
    runner = _install_runner(profile, ".codex")
    project = tmp_path / "project"
    impostor = project / "src" / "agent_hooks"
    impostor.mkdir(parents=True)
    (impostor / "__init__.py").write_text("raise SystemExit('impostor imported')\n", "utf-8")
    (project / "agent_hooks").mkdir()
    (project / "agent_hooks" / "__init__.py").write_text(
        "raise SystemExit('impostor imported')\n", "utf-8"
    )
    payload = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}

    completed = subprocess.run(
        [sys.executable, str(runner), "pre-tool"],
        cwd=project,
        env=_clean_env(),
        input=json.dumps(payload),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "impostor" not in completed.stderr
    assert json.loads(completed.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_checkout_runner_matches_what_the_bridges_launch() -> None:
    # The Pi and OpenCode bridges run bin/run_hook.py from the checkout directly.
    for bridge in (".pi/agent/extensions/agent-hooks.ts", ".opencode/agent-hooks.example.ts"):
        source = (REPO_ROOT / bridge).read_text(encoding="utf-8")
        assert '"bin", "run_hook.py"' in source, bridge
