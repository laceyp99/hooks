# Agent Hooks

This repository contains local hook scripts for coding agents. The hooks act as guardrails for safety and repo hygiene while you work. The same Python hook logic is shared by three harnesses: Claude Code, Codex, and Pi.

## Hook Behavior

### Interpreter Selection

Each hook launch goes through a small bootstrapper that picks a compatible Python in a predictable order: project virtual environment first, then the current interpreter, with a Windows fallback to `py -3.10` when the active interpreter is too old.

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

The post-tool cleaner runs `ruff check --fix`, then `ruff format`, then `ruff check` against edited Python files. It only does this when the current repo advertises Ruff support.

The cleaner only reads file paths from the tool's target fields (`path`, `file_path`, `destination`, and similar) and from `apply_patch` file headers. Paths mentioned in free text are ignored, and any path that resolves outside the current working directory, including through a symlink, is skipped so the cleaner never rewrites files outside the repo.

### Session Stop Ruff Sweep

The session-stop hook runs a repo-wide Ruff check at the end of a session. It first checks whether the repo advertises Ruff support and silently skips repos that do not.

By default the hook first runs `ruff check --fix` and `ruff format` on the Python files that `git status` reports as changed in the working tree under the current directory. That covers every uncommitted Python change, including edits you made yourself before or during the session, not only files the agent touched; this is a deliberate trade-off. Set `AGENT_HOOKS_STOP_FIX=0` if you keep unfinished work in the tree that Ruff should not rewrite. The hook then runs `ruff check .` and `ruff format --check .` across the repo and blocks the stop with the findings if either still reports problems.

When the harness reports that it is already continuing because this hook blocked the previous stop (Claude Code sets `stop_hook_active` in the Stop payload), the hook still runs its fixes and checks but reports any remaining findings as an informational message instead of blocking again. This prevents a repository with lint errors the agent cannot fix from trapping the session in a block loop.

To make the hook check-only, set `AGENT_HOOKS_STOP_FIX=0` (or `false`, `no`, `off`) in the environment the agent runs in. In that mode the hook never rewrites files and the block reason tells the agent that automatic fixes are disabled.

The Ruff opt-in markers are:

- `ruff.toml`
- `.ruff.toml`
- `pyproject.toml` with a `[tool.ruff]` table, or Ruff listed under `project.dependencies`, `project.optional-dependencies`, `dependency-groups`, or Poetry's dependency tables
- `requirements.txt` or `requirements-dev.txt` listing `ruff` as a requirement (any newline style; comments are ignored)

## Local Setup

Think of setup as two separate pieces for Claude Code and Codex:

- your **source repo**, which can live anywhere convenient
- your **installed hook bundle**, which must live in your Windows user profile so Claude Code or Codex can find it

The repo copy is where you edit files. The installed bundle is what the hook system actually reads when it runs.

Pi is slightly different: its installed extension is only a TypeScript bridge. The bridge calls back into this source checkout to run the shared Python hooks.

1. Clone or copy this repository to a normal work folder on your machine.

   ```powershell
   git clone https://github.com/laceyp99/hooks.git "$env:USERPROFILE\code\agent-hooks"
   cd "$env:USERPROFILE\code\agent-hooks"
   ```

   If you already have the repo checked out, just open a terminal in that folder.

2. Run the Windows installer from the repo checkout.

   ```powershell
   .\install.ps1
   ```

   The installer prompts before merging into existing Claude Code or Codex hook config files. It only adds this repo's missing hook entries and preserves existing matching hook entries as-is. For Claude Code it touches only the `hooks` key of `settings.json`; every other setting is left alone. If it needs to write an existing config file, it first creates a timestamped `.bak-*` backup next to that file.

   The installer refreshes the managed runtime files for Claude Code and Codex when you confirm those prompts. Those managed files are `run_hook.py`, the wrapper `scripts/` folders, and the shared `src/` folder copied into your Windows user profile.

   The installer also treats the Pi bridge extension as a managed runtime file. When you confirm that prompt it creates `%USERPROFILE%\.pi\agent\extensions\` if needed and installs `agent-hooks.ts`. If a bridge already exists and differs from the checked-in copy, the installer backs it up to a timestamped `.bak-*` file and replaces it; an identical bridge is left untouched.

3. Manual fallback: install the Claude Code bundle into your user profile, then create your local `settings.json` from the example file only if one does not already exist. If you already have a `%USERPROFILE%\.claude\settings.json`, copy the `hooks` block from `.claude\settings.example.json` into it instead of overwriting the file.

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\hooks" | Out-Null
   if (-not (Test-Path "$env:USERPROFILE\.claude\settings.json")) {
     Copy-Item ".claude\settings.example.json" "$env:USERPROFILE\.claude\settings.json"
   }
   Copy-Item -Force ".claude\hooks\run_hook.py" "$env:USERPROFILE\.claude\hooks\run_hook.py"
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\hooks\scripts" | Out-Null
   Copy-Item -Recurse -Force ".claude\hooks\scripts\*" "$env:USERPROFILE\.claude\hooks\scripts"
   Copy-Item -Recurse -Force "src" "$env:USERPROFILE\"
   ```

   - If the destination folder does not exist yet, the command creates it.
   - On Windows, `%USERPROFILE%` means your personal home folder, such as `C:\Users\YourName`.
   - `settings.example.json` is the checked-in template; `settings.json` is Claude Code's user settings file, which you can edit.
   - Claude Code runs hook commands through a shell, so the example commands reference `$HOME`. On Windows that resolves to your user profile under Git Bash. The Codex template uses the same `python "$HOME/..."` form for its Windows commands, so both templates read alike.
   - The PreToolUse hooks match `Bash`, `Edit`, `MultiEdit`, `Write`, `NotebookEdit`, and `Read`; the PostToolUse cleaner matches the editing tools; the Stop hook runs on every stop.
   - This gives Claude Code the hook registration, the bootstrap script, the wrapper scripts, and the shared `src` folder it needs.

4. Manual fallback: install the Codex bundle into your user profile, then create your local `hooks.json` from the example file only if one does not already exist.

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\hooks" | Out-Null
   if (-not (Test-Path "$env:USERPROFILE\.codex\hooks.json")) {
     Copy-Item ".codex\hooks.example.json" "$env:USERPROFILE\.codex\hooks.json"
   }
   Copy-Item -Force ".codex\hooks\run_hook.py" "$env:USERPROFILE\.codex\hooks\run_hook.py"
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\hooks\scripts" | Out-Null
   Copy-Item -Recurse -Force ".codex\hooks\scripts\*" "$env:USERPROFILE\.codex\hooks\scripts"
   Copy-Item -Recurse -Force "src" "$env:USERPROFILE\"
   ```

   - If the destination folder does not exist yet, the command creates it.
   - On Windows, `%USERPROFILE%` means your personal home folder, such as `C:\Users\YourName`.
   - `hooks.example.json` is the checked-in template; `hooks.json` is your local copy that Codex reads from `%USERPROFILE%\.codex\hooks.json`.
   - `command` targets POSIX shells with `python3`; `commandWindows` runs under PowerShell, where `$HOME` is your user profile, and uses `python` like the Claude Code template.
   - This gives Codex the hook registration, the bootstrap script, the wrapper scripts, and the shared `src` folder it needs.

5. Manual fallback: install or refresh the Pi bridge in your user profile. Create the Pi extension directory and copy the checked-in bridge over any existing copy. Back the existing file up first if you have local edits you want to keep.

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.pi\agent\extensions" | Out-Null
   Copy-Item -Force ".pi\agent\extensions\agent-hooks.ts" "$env:USERPROFILE\.pi\agent\extensions\agent-hooks.ts"
   ```

   - The Pi bridge expects this source checkout to remain available at `%USERPROFILE%\code\agent-hooks` by default.
   - If your checkout lives somewhere else, set `AGENT_HOOKS_ROOT` to the checkout path before launching Pi.
   - If `python.exe` is not on your Windows `PATH`, set `AGENT_HOOKS_PYTHON` to the Python executable Pi should use.
   - The bridge looks for the checked-in `.codex/hooks/` or `.claude/hooks/` wrapper bundle inside the source checkout, then runs the shared Python hook logic from there.

6. The Claude Code and Codex wrapper scripts load the shared hook logic from the copied `src/agent_hooks/` folder next to your user-profile bundles.
   - You do not need a repo install or manual `PYTHONPATH` for normal hook execution.
   - Keep `src/agent_hooks/` in the source repo so you can refresh the installed copy when you update the hooks.

7. If you want the hooks to reuse a project’s dependencies, create a virtual environment in that project.

   ```bash
   python -m venv venv
   ```

8. Optional for development: install the repo’s test and lint tools in a working checkout.

   ```bash
   python -m pip install -e ".[dev]"
   ```

9. Run the test suite from the repo checkout.

   ```bash
   pytest -q --import-mode=importlib
   ```

10. Run lint and format checks when you are changing code.

   ```bash
   python -m ruff check .
   python -m ruff format .
   ```

## Local Configuration

- Keep `cwd` set to `"."` so repo-aware hooks such as the Ruff cleanup still operate on the active project rather than the hooks bundle.
- The repo includes checked-in example configs at `.claude/settings.example.json` and `.codex/hooks.example.json`.
- The installed local Claude Code config is the `hooks` key of `%USERPROFILE%\.claude\settings.json`.
- The installed local Codex config should live at `%USERPROFILE%\.codex\hooks.json`.
- The Claude Code and Codex JSON files intentionally differ where each harness needs different paths or command syntax.
- If you clone this repo onto another machine, update the absolute paths in the harness JSON files for that machine.

## Repository Layout

The repo is organized as a small local bundle plus shared logic. Each harness gets its own thin wrapper folder, while the actual hook behavior lives once under `src/agent_hooks/`.

The installed Claude Code and Codex bundles also expect a copied `src/` folder beside `.claude/hooks/` or `.codex/hooks/` in your Windows user profile so the wrapper scripts can bootstrap themselves before importing the shared hook logic. Pi is different: its installed bridge lives in `~/.pi/agent/extensions/`, but it points back at this source checkout. By default, the bridge expects the checkout at `~/code/agent-hooks`; set `AGENT_HOOKS_ROOT` if you keep it somewhere else. The layout below shows the source checkout; the installed user-profile copy mirrors the same `hooks/` and `src/` structure for Claude Code and Codex. Claude Code reads its hook registration from `%USERPROFILE%\.claude\settings.json`, and Codex reads its local config from `%USERPROFILE%\.codex\hooks.json`.

```text
Hooks
├─ .claude/
│  ├─ settings.example.json         # Shareable sample Claude Code settings with the hooks block
│  └─ hooks/
│     ├─ run_hook.py                # Bootstrap that finds a compatible Python and launches one hook script
│     ├─ scripts/                   # Thin wrappers around shared hook logic
│     └─ tests/                     # Tests for the Claude Code bundle
├─ .codex/
│  └─ hooks/
│     ├─ hooks.example.json         # Shareable sample config with user-profile placeholders
│     ├─ run_hook.py                # Bootstrap that finds a compatible Python and launches one hook script
│     ├─ scripts/                   # Thin wrappers around shared hook logic
│     └─ tests/                     # Tests for the Codex bundle
├─ .pi/agent/extensions/
│  └─ agent-hooks.ts                # Global Pi extension bridge that calls the Python hooks
├─ src/agent_hooks/                 # Shared hook logic used by every bundle
│  ├─ bootstrap.py                  # Shared bootstrap helpers and interpreter selection
│  ├─ common.py                    # Shared utility helpers
│  ├─ dangerous_commands.py        # Dangerous-command detection
│  ├─ post_tool_cleaner.py          # Post-tool cleanup logic
│  ├─ ruff_support.py               # Ruff opt-in detection
│  ├─ security.py                  # Protected-path and secret-file checks
│  └─ session_stop.py              # End-of-session cleanup logic
├─ pyproject.toml                  # Packaging, test, and Ruff settings
└─ README.md                       # This guide
```

The Claude Code and Codex bundles do not need to be perfectly symmetric. They just need to work with the command formats and install locations required by each harness.

## Validation

- `pytest -q --import-mode=importlib`
- `python -m ruff check .`
- `python -m ruff format --check .`
