from pathlib import Path

import pytest

from agent_hooks import ruff_support


@pytest.mark.parametrize("name", ["ruff.toml", ".ruff.toml"])
def test_repo_uses_ruff_when_config_file_exists(tmp_path, name: str) -> None:
    (tmp_path / name).write_text("", encoding="utf-8")

    assert ruff_support.repo_uses_ruff(tmp_path) is True


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("pyproject.toml", "[tool.ruff]\nline-length = 100\n"),
        ("requirements.txt", "ruff==0.6.0\n"),
        ("requirements-dev.txt", "ruff>=0.6\n"),
    ],
)
def test_repo_uses_ruff_when_metadata_mentions_ruff(tmp_path, name: str, content: str) -> None:
    (tmp_path / name).write_text(content, encoding="utf-8")

    assert ruff_support.repo_uses_ruff(tmp_path) is True


def test_repo_uses_ruff_returns_false_without_markers(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.black]\nline-length = 88\n", encoding="utf-8")

    assert ruff_support.repo_uses_ruff(tmp_path) is False


def test_file_mentions_ruff_returns_false_for_missing_file(tmp_path) -> None:
    assert ruff_support.file_mentions_ruff(tmp_path / "requirements.txt") is False


def test_file_mentions_ruff_returns_false_on_read_error(monkeypatch, tmp_path) -> None:
    path = tmp_path / "requirements.txt"
    path.write_text("ruff==0.6.0\n", encoding="utf-8")

    def _raise(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(Path, "read_text", _raise)

    assert ruff_support.file_mentions_ruff(path) is False


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("pyproject.toml", '[tool.poetry.dependencies]\npython = "^3.10"\nruff = "^0.6"\n'),
        ("pyproject.toml", '[tool.poetry.group.dev.dependencies]\nruff = { version = "^0.6" }\n'),
        ("pyproject.toml", '[tool.poetry.dev-dependencies]\nruff = "*"\n'),
        ("pyproject.toml", '[project]\ndependencies = ["ruff"]\n'),
        ("pyproject.toml", '[project.optional-dependencies]\ndev = ["pytest", "ruff[all]>=0.6"]\n'),
        ("pyproject.toml", "[dependency-groups]\nlint = [\"ruff ; python_version >= '3.10'\"]\n"),
        ("pyproject.toml", "[tool.ruff.lint]\nselect = ['E']\n"),
        ("requirements.txt", "pytest\r\nruff\r\n"),
        ("requirements.txt", "ruff\n"),
        ("requirements.txt", "ruff  # linter\n"),
        ("requirements.txt", "Ruff[all] @ https://example.test/ruff.whl\n"),
        ("requirements-dev.txt", "-r requirements.txt\nruff ~= 0.6\n"),
    ],
)
def test_repo_uses_ruff_parses_project_declarations(tmp_path, name: str, content: str) -> None:
    (tmp_path / name).write_text(content, encoding="utf-8", newline="")

    assert ruff_support.repo_uses_ruff(tmp_path) is True


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("pyproject.toml", '[project]\nname = "ruffian"\ndependencies = ["ruffles"]\n'),
        ("pyproject.toml", '[project]\ndescription = "we like ruff"\n'),
        ("pyproject.toml", '[tool.other]\nnote = "ruff==0.6"\n'),
        ("pyproject.toml", '[tool.poetry.dependencies]\nruffles = "^1.0"\n'),
        ("requirements.txt", "# ruff\nruffles\n"),
        ("requirements.txt", "-r ruff\n"),
        ("requirements.txt", "flake8\r\nblack\r\n"),
    ],
)
def test_repo_uses_ruff_ignores_lookalikes_and_prose(tmp_path, name: str, content: str) -> None:
    (tmp_path / name).write_text(content, encoding="utf-8", newline="")

    assert ruff_support.repo_uses_ruff(tmp_path) is False


def test_malformed_pyproject_falls_back_to_markers(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = \n[broken", encoding="utf-8"
    )

    assert ruff_support.repo_uses_ruff(tmp_path) is True


# ---------------------------------------------------------------------------
# ruff_command: which Ruff the post-tool and stop hooks launch.
# ---------------------------------------------------------------------------


def test_venv_paths_use_windows_layout(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ruff_support.sys, "platform", "win32")

    assert ruff_support._venv_ruff_path(tmp_path) == tmp_path / "Scripts" / "ruff.exe"


def test_venv_paths_use_posix_layout(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ruff_support.sys, "platform", "linux")

    assert ruff_support._venv_ruff_path(tmp_path) == tmp_path / "bin" / "ruff"


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def test_ruff_command_prefers_the_first_virtualenv_ruff(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ruff_support.sys, "platform", "win32")
    preferred = _touch(tmp_path / ".venv" / "Scripts" / "ruff.exe")
    _touch(tmp_path / "venv" / "Scripts" / "ruff.exe")

    assert ruff_support.ruff_command(tmp_path) == [str(preferred)]


def test_ruff_command_skips_a_virtualenv_without_ruff(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ruff_support.sys, "platform", "win32")
    _touch(tmp_path / ".venv" / "Scripts" / "python.exe")
    fallback = _touch(tmp_path / "env" / "Scripts" / "ruff.exe")

    assert ruff_support.ruff_command(tmp_path) == [str(fallback)]


def test_ruff_command_never_launches_the_project_interpreter(monkeypatch, tmp_path) -> None:
    # Ruff installed in the project but without its executable. Running it as
    # ``<project>/.venv/python -m ruff`` would execute that environment's sitecustomize and .pth
    # files, so the environment is skipped and the hook's own Ruff runs instead.
    monkeypatch.setattr(ruff_support.sys, "platform", "win32")
    monkeypatch.setattr(ruff_support.sys, "executable", "C:/Python/python.exe")
    monkeypatch.setattr(ruff_support.importlib.util, "find_spec", lambda name: object())
    venv_python = _touch(tmp_path / ".venv" / "Scripts" / "python.exe")
    (tmp_path / ".venv" / "Lib" / "site-packages" / "ruff").mkdir(parents=True)

    command = ruff_support.ruff_command(tmp_path)

    assert command == ["C:/Python/python.exe", "-m", "ruff"]
    assert str(venv_python) not in command


def test_ruff_command_falls_back_to_the_current_interpreter(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ruff_support.sys, "executable", "C:/Python/python.exe")
    monkeypatch.setattr(ruff_support.importlib.util, "find_spec", lambda name: object())

    assert ruff_support.ruff_command(tmp_path) == ["C:/Python/python.exe", "-m", "ruff"]


def test_ruff_command_falls_back_to_ruff_on_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ruff_support.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(ruff_support.shutil, "which", lambda name: "C:/tools/ruff.exe")

    assert ruff_support.ruff_command(tmp_path) == ["C:/tools/ruff.exe"]


def test_ruff_command_without_any_ruff_still_names_one(monkeypatch, tmp_path) -> None:
    # The missing-module error then reaches the hook output instead of a silent skip.
    monkeypatch.setattr(ruff_support.sys, "executable", "C:/Python/python.exe")
    monkeypatch.setattr(ruff_support.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(ruff_support.shutil, "which", lambda name: None)

    assert ruff_support.ruff_command(tmp_path) == ["C:/Python/python.exe", "-m", "ruff"]
