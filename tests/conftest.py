from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
RUNNER_PATH = REPO_ROOT / "bin" / "run_hook.py"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def pre_tool_security():
    from agent_hooks import security

    return security


@pytest.fixture
def pre_tool_dangerous_commands():
    from agent_hooks import dangerous_commands

    return dangerous_commands


@pytest.fixture
def cleaner():
    from agent_hooks import post_tool_cleaner

    return post_tool_cleaner


@pytest.fixture
def run_hook_module():
    """Load ``bin/run_hook.py`` as a module. It is a script, not part of the package."""
    spec = importlib.util.spec_from_file_location("run_hook_under_test", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {RUNNER_PATH}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def git_name() -> str:
    return "." + "git"


@pytest.fixture
def git_internal_path(git_name: str):
    def _build(*parts: str) -> str:
        return "/".join((git_name, *parts))

    return _build
