# Agent Hooks

This repository contains local hook scripts for coding agents. The hooks act as guardrails for safety and repo hygiene while you work.

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

### Session Stop Ruff Sweep

The session-stop hook performs repo-wide Ruff cleanup at the end of a session. It first checks whether the repo advertises Ruff support and silently skips repos that do not.

The Ruff opt-in markers are:

- `ruff.toml`
- `.ruff.toml`
- `pyproject.toml` containing Ruff configuration or dependency references
- `requirements.txt` containing Ruff dependency references

## Local Setup

Think of setup as two separate pieces for Copilot and Codex:

- your **source repo**, which can live anywhere convenient
- your **installed hook bundle**, which must live in your Windows user profile so Copilot or Codex can find it

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

   The installer prompts before merging into existing Copilot or Codex hook config files. It only adds this repo's missing hook entries and preserves existing matching hook entries as-is. If it needs to write an existing config file, it first creates a timestamped `.bak-*` backup next to that file.

   The installer refreshes the managed runtime files for Copilot and Codex when you confirm those prompts. Those managed files are `run_hook.py`, the wrapper `scripts/` folders, and the shared `src/` folder copied into your Windows user profile.

   The installer can also install the Pi bridge extension. It creates `%USERPROFILE%\.pi\agent\extensions\` if needed and copies `agent-hooks.ts` only when that destination file does not already exist.

3. Manual fallback: install the Copilot bundle into your user profile, then create your local `hooks.json` from the example file only if one does not already exist.

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.copilot\hooks" | Out-Null
   if (-not (Test-Path "$env:USERPROFILE\.copilot\hooks\hooks.json")) {
     Copy-Item ".copilot\hooks\hooks.example.json" "$env:USERPROFILE\.copilot\hooks\hooks.json"
   }
   Copy-Item -Force ".copilot\hooks\run_hook.py" "$env:USERPROFILE\.copilot\hooks\run_hook.py"
   Copy-Item -Recurse -Force ".copilot\hooks\scripts" "$env:USERPROFILE\.copilot\hooks\scripts"
   Copy-Item -Recurse -Force "src" "$env:USERPROFILE\"
   ```

   - If the destination folder does not exist yet, the command creates it.
   - On Windows, `%USERPROFILE%` means your personal home folder, such as `C:\Users\YourName`.
   - `hooks.example.json` is the checked-in template; `hooks.json` is your local copy that you can edit.
   - This gives Copilot the hook registration, the bootstrap script, the wrapper scripts, and the shared `src` folder it needs.

4. Manual fallback: install the Codex bundle into your user profile, then create your local `hooks.json` from the example file only if one does not already exist.

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\hooks" | Out-Null
   if (-not (Test-Path "$env:USERPROFILE\.codex\hooks.json")) {
     Copy-Item ".codex\hooks.example.json" "$env:USERPROFILE\.codex\hooks.json"
   }
   Copy-Item -Force ".codex\hooks\run_hook.py" "$env:USERPROFILE\.codex\hooks\run_hook.py"
   Copy-Item -Recurse -Force ".codex\hooks\scripts" "$env:USERPROFILE\.codex\hooks\scripts"
   Copy-Item -Recurse -Force "src" "$env:USERPROFILE\"
   ```

   - If the destination folder does not exist yet, the command creates it.
   - On Windows, `%USERPROFILE%` means your personal home folder, such as `C:\Users\YourName`.
   - `hooks.example.json` is the checked-in template; `hooks.json` is your local copy that Codex reads from `%USERPROFILE%\.codex\hooks.json`.
   - This gives Codex the hook registration, the bootstrap script, the wrapper scripts, and the shared `src` folder it needs.

5. Manual fallback: install the Pi bridge into your user profile. Create the Pi extension directory and copy the bridge into place only if one does not already exist.

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.pi\agent\extensions" | Out-Null
   if (-not (Test-Path "$env:USERPROFILE\.pi\agent\extensions\agent-hooks.ts")) {
     Copy-Item ".pi\agent\extensions\agent-hooks.ts" "$env:USERPROFILE\.pi\agent\extensions\agent-hooks.ts"
   }
   ```

   - The Pi bridge expects this source checkout to remain available at `%USERPROFILE%\code\agent-hooks` by default.
   - If your checkout lives somewhere else, set `AGENT_HOOKS_ROOT` to the checkout path before launching Pi.
   - If `python.exe` is not on your Windows `PATH`, set `AGENT_HOOKS_PYTHON` to the Python executable Pi should use.
   - The bridge looks for the checked-in `.codex/hooks/` or `.copilot/hooks/` wrapper bundle inside the source checkout, then runs the shared Python hook logic from there.

6. The Copilot and Codex wrapper scripts load the shared hook logic from the copied `src/agent_hooks/` folder next to your user-profile bundles.
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
- The repo includes checked-in example configs at `.copilot/hooks/hooks.example.json` and `.codex/hooks.example.json`.
- The installed local Codex config should live at `%USERPROFILE%\.codex\hooks.json`.
- The Copilot and Codex JSON files intentionally differ where each harness needs different paths or command syntax.
- If you clone this repo onto another machine, update the absolute paths in the harness JSON files for that machine.

## Repository Layout

The repo is organized as a small local bundle plus shared logic. Each harness gets its own thin wrapper folder, while the actual hook behavior lives once under `src/agent_hooks/`.

The installed Copilot and Codex bundles also expect a copied `src/` folder beside `.copilot/hooks/` or `.codex/hooks/` in your Windows user profile so the wrapper scripts can bootstrap themselves before importing the shared hook logic. Pi is different: its installed bridge lives in `~/.pi/agent/extensions/`, but it points back at this source checkout. By default, the bridge expects the checkout at `~/code/agent-hooks`; set `AGENT_HOOKS_ROOT` if you keep it somewhere else. The layout below shows the source checkout; the installed user-profile copy mirrors the same `hooks/` and `src/` structure for Copilot and Codex, except that Codex reads its local config from `%USERPROFILE%\.codex\hooks.json`.

```text
Hooks
├─ .copilot/
│  └─ hooks/
│     ├─ hooks.json                 # Local hook registration used by Copilot
│     ├─ hooks.example.json         # Shareable sample config with user-profile placeholders
│     ├─ run_hook.py                # Bootstrap that finds a compatible Python and launches one hook script
│     ├─ scripts/                   # Thin wrappers around shared hook logic
│     └─ tests/                     # Tests for the Copilot bundle
├─ .codex/
│  └─ hooks/
│     ├─ hooks.example.json         # Shareable sample config with user-profile placeholders
│     ├─ run_hook.py                # Bootstrap that finds a compatible Python and launches one hook script
│     ├─ scripts/                   # Thin wrappers around shared hook logic
│     └─ tests/                     # Tests for the Codex bundle
├─ .pi/agent/extensions/
│  └─ agent-hooks.ts                # Global Pi extension bridge that calls the Python hooks
├─ src/agent_hooks/                 # Shared hook logic used by both bundles
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

The Copilot and Codex bundles do not need to be perfectly symmetric. They just need to work with the command formats and install locations required by each harness.

## Validation

- `pytest -q --import-mode=importlib`
- `python -m ruff check .`
- `python -m ruff format --check .`
