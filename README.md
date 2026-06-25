# Agent Hooks

This repository contains my project-specific GitHub Copilot hook scripts for local safety and repo hygiene. It is set up as a private Python utility repo: the code is versioned, tested, and linted, but the hooks are still local guardrails rather than a security boundary.

## Repository Layout

- `hooks.json` contains the hook registration used by Copilot.
- `run_hook.py` is the shared bootstrap that resolves the local Python interpreter and executes an individual hook script.
- `scripts/pre_tool_security.py` blocks env-like secret paths and mutating access to protected Git internals.
- `scripts/pre_tool_dangerous_commands.py` blocks a conservative set of high-risk shell command patterns.
- `scripts/post_tool_cleaner.py` runs Ruff formatting and lint cleanup on edited Python files when the repo advertises Ruff support.
- `scripts/session_stop.py` runs repo-wide Ruff cleanup at session stop and blocks if issues remain.
- `scripts/ruff_support.py` centralizes the logic that decides whether the current repo opts into Ruff.
- `tests/` contains the pytest coverage for the bootstrap and hook scripts.

## Hook Behavior

### PreToolUse Security

The security hook denies access to secret-bearing env files such as `.env`, `.env.local`, `.env.production`, `.envrc`, `*.env`, `*.secret`, `*.secrets`, and anything under `.direnv/`. Template files such as `.env.example` and `.env.sample` stay allowed.

It also blocks mutating operations against protected Git internals inside `.git/`, including refs, objects, hooks, worktrees, and control files such as `HEAD`, `index`, and `config`. Normal project files such as `.gitignore`, `.gitattributes`, and anything inside `.github/` remain allowed.

### PreToolUse Dangerous Commands

The dangerous-command hook inspects shell-style tool payloads and denies a small set of high-signal destructive patterns:

- forceful recursive deletes aimed at root-like locations
- destructive raw-device writes and filesystem formatting commands
- network-to-shell execution such as `curl ... | bash`
- broad recursive permission or ownership changes such as `chmod -R` and `chown -R`

This is intentionally conservative. It is meant to stop obvious foot-guns, not to perfectly model all unsafe shell behavior.

### PostToolUse Cleaner

The post-tool cleaner runs `ruff format`, then `ruff check --fix`, then `ruff check` against edited Python files. It only does this when the current repo advertises Ruff support.

### Session Stop Ruff Sweep

The session-stop hook performs repo-wide Ruff cleanup at the end of a session. It first checks whether the repo advertises Ruff support and silently skips repos that do not.

The Ruff opt-in markers are:

- `ruff.toml`
- `.ruff.toml`
- `pyproject.toml` containing Ruff configuration or dependency references
- `requirements.txt` or `requirements-dev.txt` containing Ruff dependency references

## Local Setup

1. Install this hooks repo at `%USERPROFILE%\.copilot\hooks` so the configured hook commands can always find `run_hook.py` and the scripts under `scripts/`.
2. Create a virtual environment in the active project root using `.venv`, `venv`, or `env` when you want the hooks to reuse that project's dependencies.
3. Install the development tools with `python -m pip install -r requirements-dev.txt`.
4. Run the test suite with `pytest`.
5. Run lint and format checks with `python -m ruff check .` and `python -m ruff format .`.

The bootstrap in `run_hook.py` always resolves the hook script from the installed hooks directory. It resolves the Python interpreter from the active project working directory in this order: `.venv`, `venv`, `env`. If none exists, it falls back to the Python executable that launched the bootstrap.

## Local Configuration Notes

- Keep `cwd`: `"."` so repo-aware hooks such as the Ruff cleanup still operate on the active project rather than the hooks repo.
- `hooks.json` is still a local installation artifact. If this repo is cloned onto another machine, update the hook command paths to match that machine.
- `hooks.example.json` shows the same fixed hooks location using `%USERPROFILE%` for Command Prompt and `$env:USERPROFILE` for PowerShell.

## Validation

- `pytest`
- `python -m ruff check .`
- `python -m ruff format --check .`