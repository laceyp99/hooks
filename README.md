# Agent Hooks

This repository contains local hook scripts for coding agents. The hooks act as guardrails for safety and repo hygiene while you work. The checked-in Copilot and Codex configs show how to wire the scripts into each harness on your machine.

If you are new to hooks: they are small scripts that run automatically at specific moments, such as before a tool runs, after a tool runs, or when a session ends.

## Hook Behavior

### PreToolUse Security

The security hook blocks access to secret-bearing env files such as `.env`, `.env.local`, `.env.production`, `.envrc`, `*.env`, `*.secret`, `*.secrets`, and anything under `.direnv/`. Template files such as `.env.example` and `.env.sample` stay allowed.

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
- `requirements.txt` containing Ruff dependency references

## Local Setup

Think of setup as two separate pieces:

- your **source repo**, which can live anywhere convenient
- your **installed hook bundle**, which must live in your Windows user profile so Copilot or Codex can find it

The repo copy is where you edit files. The installed bundle is what the hook system actually reads when it runs.

1. Clone or copy this repository to a normal work folder on your machine.

   ```powershell
   git clone https://github.com/laceyp99/hooks.git "$env:USERPROFILE\code\agent-hooks"
   cd "$env:USERPROFILE\code\agent-hooks"
   ```

   If you already have the repo checked out, just open a terminal in that folder.

2. Install the Copilot bundle into your user profile.

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.copilot\hooks" | Out-Null
   Copy-Item -Recurse -Force ".copilot\hooks\*" "$env:USERPROFILE\.copilot\hooks\"
   Copy-Item -Recurse -Force "src" "$env:USERPROFILE\"
   ```

   - If the destination folder does not exist yet, the command creates it.
   - On Windows, `%USERPROFILE%` means your personal home folder, such as `C:\Users\YourName`.
   - This gives Copilot the hook registration, the example file, the bootstrap script, the wrapper scripts, and the shared `src` folder it needs.

3. Install the Codex bundle into your user profile.

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\hooks" | Out-Null
   Copy-Item -Recurse -Force ".codex\hooks\*" "$env:USERPROFILE\.codex\hooks\"
   Copy-Item -Recurse -Force "src" "$env:USERPROFILE\"
   ```

   - If the destination folder does not exist yet, the command creates it.
   - On Windows, `%USERPROFILE%` means your personal home folder, such as `C:\Users\YourName`.
   - If you already copied `src` in step 2, running this again is harmless.

4. The wrapper scripts load the shared hook logic from the copied `src/agent_hooks/` folder next to your user-profile bundles.
   - You do not need a repo install or manual `PYTHONPATH` for normal hook execution.
   - Keep `src/agent_hooks/` in the source repo so you can refresh the installed copy when you update the hooks.

5. If you want the hooks to reuse a project’s dependencies, create a virtual environment in that project.

   ```bash
   python -m venv venv
   ```

6. Optional for development: install the repo’s test and lint tools in a working checkout.

   ```bash
   python -m pip install -e ".[dev]"
   ```

7. Run the test suite from the repo checkout.

   ```bash
   pytest -q --import-mode=importlib
   ```

8. Run lint and format checks when you are changing code.

   ```bash
   python -m ruff check .
   python -m ruff format .
   ```

## Local Configuration

- Keep `cwd` set to `"."` so repo-aware hooks such as the Ruff cleanup still operate on the active project rather than the hooks bundle.
- `.copilot/hooks/hooks.json` and `.codex/hooks.json` are the checked-in harness configs for this repo.
- The Copilot and Codex JSON files intentionally differ where each harness needs different paths or command syntax.
- If you clone this repo onto another machine, update the absolute paths in the harness JSON files for that machine.

## Repository Layout

The repo is organized as a small local bundle plus shared logic. Each harness gets its own thin wrapper folder, while the actual hook behavior lives once under `src/agent_hooks/`.

The installed bundles also expect a copied `src/` folder beside `.copilot/hooks/` or `.codex/hooks/` in your Windows user profile so the wrapper scripts can bootstrap themselves before importing the shared hook logic.

```text
Hooks
├─ .copilot/hooks/                 # Copilot-specific local bundle
│  ├─ hooks.json                  # Local hook registration used by Copilot
│  ├─ hooks.example.json          # Shareable sample config with user-profile placeholders
│  ├─ run_hook.py                 # Bootstrap that finds Python and launches one hook script
│  ├─ scripts/                    # Thin wrappers around shared hook logic
│  └─ tests/                      # Tests for the Copilot bundle
├─ .codex/hooks/                   # Codex-specific local bundle
│  ├─ hooks.json                  # Local hook registration used by Codex
│  ├─ hooks.example.json          # Shareable sample config with user-profile placeholders
│  ├─ run_hook.py                 # Bootstrap that finds Python and launches one hook script
│  ├─ scripts/                    # Thin wrappers around shared hook logic
│  └─ tests/                      # Tests for the Codex bundle
├─ src/agent_hooks/                # Shared hook logic used by both bundles
│  ├─ bootstrap.py                # Shared bootstrap helpers
│  ├─ common.py                   # Shared utility helpers
│  ├─ dangerous_commands.py       # Dangerous-command detection
│  ├─ post_tool_cleaner.py        # Post-tool cleanup logic
│  ├─ ruff_support.py             # Ruff opt-in detection
│  ├─ security.py                 # Protected-path and secret-file checks
│  └─ session_stop.py             # End-of-session cleanup logic
├─ pyproject.toml                 # Packaging, test, and Ruff settings
└─ README.md                      # This guide
```

The Copilot and Codex bundles do not need to be perfectly symmetric. They just need to work with the command formats and install locations required by each harness.

## Validation

- `pytest -q --import-mode=importlib`
- `python -m ruff check .`
- `python -m ruff format --check .`
