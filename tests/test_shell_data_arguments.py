"""Shell words that name a secret as data pass; the same names as file arguments do not.

Commands are written with ``{env}`` so the protected name is spelled in one place.
"""

import pytest

ENV = "." + "env"


def _cmd(template: str) -> str:
    return template.replace("{env}", ENV)


@pytest.mark.parametrize(
    "command",
    [
        # Messages
        'git commit -m "docs: explain copying {env}.example to {env}"',
        'git commit -am "chore: stop tracking id_rsa fixtures"',
        "git commit -m \"$(cat <<'EOF'\nfix: load {env} once\n\n* add form\nEOF\n)\"",
        'git tag -a v1 -m "drop {env} support"',
        'git log --grep "{env}"',
        'gh pr create --title "fix config" --body "Reads DATABASE_URL from {env}.local"',
        'git commit -m "fix: avoid Invoke-Expression in installer"',
        # Search patterns
        'rg -n "\\{env}" src',
        "rg -n \"load_dotenv\\('{env}'\\)\" src",
        'rg -n "\\.pem" src',
        'grep -rn -e "{env}" src',
        'git grep -n "{env}" -- src',
        'Select-String -Pattern "{env}" -Path src/*.py',
        'rg -n "iex|Invoke-Expression" scripts',
        # Exclusions
        "rg --glob '!**/{env}' DATABASE_URL",
        "grep -rn API_KEY --exclude={env} .",
        "tar --exclude={env} -czf release.tgz .",
        "rsync -a --exclude {env} ./ deploy/",
        "zip -r out.zip . -x {env} id_rsa",
        "robocopy src dst /E /XF {env} /XD node_modules",
        "Copy-Item -Recurse src dst -Exclude {env}",
        # Ignore files
        "echo {env} >> .gitignore",
        "printf '%s\\n' {env} {env}.local >> .dockerignore",
        "Add-Content .gitignore '{env}.local'",
        "Add-Content -Path .gitignore -Value '{env}'",
        # Broad wildcards
        "ls *",
        "rm -rf build/*",
        "Remove-Item -Recurse -Force dist\\*",
        "cp -r public/* dist/",
        "find . -name '*' -newer README.md",
        'sqlite3 app.db "SELECT * FROM users LIMIT 5"',
        "echo $((6 * 7))",
        "sed -i 's/.*/x/' notes.txt",
        'npx prettier --write "src/**/*"',
        "ls */README.md",
        # Start-Process with a literal program, iex as data
        "Start-Process npm -ArgumentList 'run dev'",
        "Start-Process -Wait -FilePath msiexec -ArgumentList '/i app.msi'",
        "Start-Process http://localhost:3000",
        "Get-Help Start-Process",
        "iex -S mix",
        # Templates and vendored CA bundles
        "cat {env}.template",
        "cat {env}.dist",
        "cat .venv/Lib/site-packages/certifi/cacert.pem",
        "ls node_modules/some-lib/certs/root.pem",
    ],
)
def test_data_and_broad_words_are_allowed(pre_tool_security, command: str) -> None:
    assert pre_tool_security._find_env_access_in_command(_cmd(command)) is None, command


@pytest.mark.parametrize(
    "command",
    [
        # A substitution or variable can run code, so it is never data.
        'git commit -m "$(cat {env})"',
        'git commit -m "$(cat <<EOF\n$(cat {env})\nEOF\n)"',
        'gh pr create --body "`cat {env}`"',
        'echo "$(cat {env})" >> .gitignore',
        # File arguments next to data arguments are still files.
        "git commit -F {env}",
        'git commit -m "x" -- {env}',
        "gh pr create --title x --body-file {env}",
        "rg -f {env} src",
        "grep -e TOKEN {env}",
        "rg TOKEN {env}",
        "Select-String -Pattern KEY -Path {env}",
        "Select-String KEY {env}",
        "rg --glob '{env}' KEY",
        "tar --exclude=x -czf out.tgz {env}",
        "zip -r out.zip {env} -x build",
        # Writing the secret itself, or reading it into an ignore file.
        "echo x > {env}",
        "cat {env} >> .gitignore",
        "Add-Content {env} 'X=1'",
        "Add-Content -Path .gitignore -Value (Get-Content {env})",
        # Globs aimed at secrets.
        "cat .e*",
        "ls *{env}",
        "cat {.e,}nv",
        # Opaque launches.
        "Start-Process pwsh -ArgumentList $script",
        "Start-Process $exe",
        "Start-Process notepad {env}",
        "iex (Get-Content run.ps1)",
        "Get-Content run.ps1 | iex",
        "Invoke-Expression -Command $code",
        'pwsh -NoProfile -Command "iex (gc run.ps1)"',
        "powershell -c iex (gc run.ps1)",
        # Still out of scope for the exemptions.
        "cat {env}.test",
        "cat tests/fixtures/credentials.json",
        "cat node_modules/app/{env}",
    ],
)
def test_access_beside_data_arguments_is_still_denied(pre_tool_security, command: str) -> None:
    assert pre_tool_security._find_env_access_in_command(_cmd(command)) is not None, command


def _reason(pre_tool_security, tool_name: str, tool_input: dict, cwd: str = "") -> str:
    response = pre_tool_security.evaluate(
        {"tool_name": tool_name, "tool_input": tool_input, "cwd": cwd}
    )
    assert response is not None, tool_input
    return response["hookSpecificOutput"]["permissionDecisionReason"]


def test_deny_reason_names_the_rule_that_fired(pre_tool_security, tmp_path) -> None:
    glob = _reason(pre_tool_security, "Bash", {"command": "cat .e*"})
    assert "This pattern selects env-like secret files" in glob
    assert "Blocked pattern: .e*" in glob

    opaque = _reason(pre_tool_security, "PowerShell", {"command": "Start-Process $exe"})
    assert "Run the command directly with literal arguments" in opaque

    git = _reason(pre_tool_security, "Write", {"file_path": ".git/config", "content": "x"})
    assert "Git internals" in git and "Use git commands instead" in git


def test_direct_access_offers_no_workaround_beyond_the_template(
    pre_tool_security, tmp_path
) -> None:
    plain = _reason(pre_tool_security, "Read", {"file_path": ENV}, str(tmp_path))
    assert plain.startswith("Human must handle env-like secret files manually")
    assert "example" not in plain

    (tmp_path / (ENV + ".example")).write_text("KEY=\n", encoding="utf-8")
    hinted = _reason(pre_tool_security, "Read", {"file_path": ENV}, str(tmp_path))
    assert hinted.endswith("read .env.example instead.")

    key = _reason(pre_tool_security, "Bash", {"command": "cat id_rsa"}, str(tmp_path))
    assert "example" not in key


def test_template_and_vendored_names_are_not_protected(pre_tool_security) -> None:
    match = pre_tool_security._matches_env_path
    assert match(ENV + ".template") is False
    assert match(ENV + ".dist") is False
    assert match(ENV + ".defaults") is False
    assert match("node_modules/pkg/cacert.pem") is False
    assert match("node_modules/pkg/" + ENV) is True
    assert match("certs/ca.pem") is True
