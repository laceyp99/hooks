from pathlib import Path

import pytest


@pytest.mark.parametrize("name", ["ruff.toml", ".ruff.toml"])
def test_repo_uses_ruff_when_config_file_exists(load_script_module, tmp_path, name: str) -> None:
    ruff_support = load_script_module("scripts/ruff_support.py", "ruff_support_config")
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
def test_repo_uses_ruff_when_metadata_mentions_ruff(
    load_script_module, tmp_path, name: str, content: str
) -> None:
    ruff_support = load_script_module("scripts/ruff_support.py", "ruff_support_metadata")
    (tmp_path / name).write_text(content, encoding="utf-8")

    assert ruff_support.repo_uses_ruff(tmp_path) is True


def test_repo_uses_ruff_returns_false_without_markers(load_script_module, tmp_path) -> None:
    ruff_support = load_script_module("scripts/ruff_support.py", "ruff_support_none")
    (tmp_path / "pyproject.toml").write_text("[tool.black]\nline-length = 88\n", encoding="utf-8")

    assert ruff_support.repo_uses_ruff(tmp_path) is False


def test_file_mentions_ruff_returns_false_for_missing_file(load_script_module, tmp_path) -> None:
    ruff_support = load_script_module("scripts/ruff_support.py", "ruff_support_missing")

    assert ruff_support.file_mentions_ruff(tmp_path / "requirements.txt") is False


def test_file_mentions_ruff_returns_false_on_read_error(
    load_script_module, monkeypatch, tmp_path
) -> None:
    ruff_support = load_script_module("scripts/ruff_support.py", "ruff_support_read_error")
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
def test_repo_uses_ruff_parses_project_declarations(
    load_script_module, tmp_path, name: str, content: str
) -> None:
    ruff_support = load_script_module("scripts/ruff_support.py", "ruff_support_declarations")
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
def test_repo_uses_ruff_ignores_lookalikes_and_prose(
    load_script_module, tmp_path, name: str, content: str
) -> None:
    ruff_support = load_script_module("scripts/ruff_support.py", "ruff_support_lookalikes")
    (tmp_path / name).write_text(content, encoding="utf-8", newline="")

    assert ruff_support.repo_uses_ruff(tmp_path) is False


def test_malformed_pyproject_falls_back_to_markers(load_script_module, tmp_path) -> None:
    ruff_support = load_script_module("scripts/ruff_support.py", "ruff_support_malformed")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = \n[broken", encoding="utf-8"
    )

    assert ruff_support.repo_uses_ruff(tmp_path) is True
