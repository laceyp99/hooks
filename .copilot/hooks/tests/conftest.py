import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
SRC_DIR = ROOT.parents[1] / "src"

for path in (SRC_DIR, SCRIPTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def load_script_module():
    def _load(relative_path: str, module_name: str | None = None):
        path = ROOT / relative_path
        resolved_name = module_name or path.stem
        return _load_module(resolved_name, path)

    return _load


@pytest.fixture
def pre_tool_security(load_script_module):
    return load_script_module("scripts/pre_tool_security.py", "pre_tool_security")


@pytest.fixture
def pre_tool_dangerous_commands(load_script_module):
    return load_script_module(
        "scripts/pre_tool_dangerous_commands.py", "pre_tool_dangerous_commands"
    )


@pytest.fixture
def git_name() -> str:
    return "." + "git"


@pytest.fixture
def git_internal_path(git_name: str):
    def _build(*parts: str) -> str:
        return "/".join((git_name, *parts))

    return _build
