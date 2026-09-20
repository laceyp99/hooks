# Agent Hooks

Local guardrails for coding agents. The hooks block a small set of genuinely dangerous actions, keep secrets out of an agent's reach, and tidy Python as you go.

One set of Python hook logic is shared by three harnesses: **Claude Code**, **Codex**, and **Pi**. Each harness gets a thin wrapper; the behavior lives once, under `src/agent_hooks/`.

## Quick start

### 1. Check the prerequisites

| Requirement | Why | Check |
|---|---|---|
| Windows with PowerShell 5.1+ | `install.ps1` is a PowerShell script | `$PSVersionTable.PSVersion` |
| Python 3.10 or newer on `PATH` | The hooks are Python | `python --version` |
| Git for Windows (includes Git Bash) | Claude Code runs hook commands through a shell and expands `$HOME` there | `git --version` |
| Ruff *(optional)* | Only needed in projects that opt in; see [Ruff opt-in](#ruff-opt-in) | `python -m ruff --version` |

No administrator rights are needed. The installer writes only inside your user profile.

### 2. Get the repository

```powershell
git clone https://github.com/laceyp99/hooks.git "$env:USERPROFILE\code\agent-hooks"
cd "$env:USERPROFILE\code\agent-hooks"
```

Any folder works, with one exception: the **Pi** bridge looks for the checkout at `%USERPROFILE%\code\agent-hooks` unless you set `AGENT_HOOKS_ROOT`. If you only use Claude Code and Codex, put it wherever you like.

### 3. Run the installer

```powershell
.\install.ps1
```

It asks before each step, so you can install only the harnesses you use. Answer `y` to the ones you want.

Every write is backed up first to a timestamped `.bak-*` file next to the original. For Claude Code it touches **only** the `hooks` key of `settings.json`; every other setting you have is left exactly as it was.

### 4. Trust the hooks in Codex

**If you use Codex, do not skip this.** Codex keeps a trust hash for each hook in `config.toml`, and it will **silently skip a hook it does not trust** — the session runs normally, with nothing in the output to tell you a guard was bypassed.

Open the Codex TUI after installing and approve the hooks when prompted. The installer prints a reminder whenever it writes `hooks.json`.

Claude Code and Pi have no equivalent step.

### 5. Verify it works

Run this from Git Bash, or from PowerShell with the equivalent quoting:

```bash
runner="$HOME/.claude/hooks/run_hook.py"
security="$HOME/.claude/hooks/scripts/pre_tool_security.py"

# Expect a JSON deny payload:
echo '{"tool_name":"Bash","tool_input":{"command":"cat .env"}}' | python "$runner" "$security"

# Expect no output at all, because this only mentions the file:
echo '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"document .env\""}}' | python "$runner" "$security"
```

The first prints a `permissionDecision: deny` payload naming `.env`. The second prints nothing, which means allowed. If both print nothing, the hooks are not wired up — see [Troubleshooting](#troubleshooting).

Swap `.claude` for `.codex` to check the Codex bundle the same way.

## What the hooks do

### Secret files

Access to secret-bearing files is denied: `.env`, `.env.local`, `.env.production`, `.envrc`, `*.env`, `*.secret`, `*.secrets`, and anything under `.direnv/`. Templates such as `.env.example` and `.env.sample` stay allowed.

How the check applies depends on how the tool names its target:

- **Tools with a file field** (`Read`, `Edit`, `Write`, `NotebookEdit`, `apply_patch`) are denied on the **name alone**.
- **Shell commands** are denied only when the command actually **reads or writes** the file. A command that merely mentions the name is allowed, so these all work normally:

  ```bash
  git commit -m "fix .env loading"
  gh pr create --body "Adds .env support"
  grep -rn ".env" src/
  echo ".env" >> .gitignore        # writes .gitignore, not .env
  ```

  while these are denied:

  ```bash
  cat .env
  cp .env /tmp/elsewhere
  echo LEAK=1 >> .env              # writes .env
  git add .env
  bash -c "cat .env"
  ```

### Git internals

Mutating operations against `.git/` are denied: refs, objects, hooks, worktrees, and control files such as `HEAD`, `index`, and `config`. Reading them is allowed. Ordinary project files — `.gitignore`, `.gitattributes`, anything under `.github/` — are unaffected.

### Dangerous commands

A small set of high-signal destructive patterns is denied:

- forceful recursive deletes aimed at root-like locations
- destructive raw-device writes and filesystem formatting
- network-to-shell execution such as `curl ... | bash`
- broad recursive permission or ownership changes (`chmod -R`, `chown -R`)

Deliberately conservative. It stops obvious foot-guns; it does not model every unsafe shell construct. See [Known limits](#known-limits).

### Python cleanup after edits

After a write, `ruff check --fix` → `ruff format` → `ruff check` runs against the edited Python files, but only in repos that [opt in to Ruff](#ruff-opt-in).

Paths come only from the tool's target fields and from `apply_patch` file headers. Paths mentioned in free text are ignored, and anything resolving outside the working directory — including through a symlink — is skipped, so the cleaner never rewrites files outside the repo.

### End-of-session sweep

At session end the hook runs a repo-wide Ruff check, skipping repos that have not opted in.

By default it first runs `ruff check --fix` and `ruff format` on the Python files `git status` reports as changed under the current directory. **That includes edits you made yourself**, not only the agent's — a deliberate trade-off. Set `AGENT_HOOKS_STOP_FIX=0` (or `false`, `no`, `off`) to make the hook check-only; the block message then says fixes are disabled.

It then runs `ruff check .` and `ruff format --check .` across the repo and blocks the stop with any findings.

If the harness reports it is already continuing because this hook blocked the previous stop (Claude Code sets `stop_hook_active`), the hook still fixes and checks but **reports** rather than blocks, so a repo with unfixable lint cannot trap the session in a loop.

### Ruff opt-in

A repo opts in via any of:

- `ruff.toml` or `.ruff.toml`
- `pyproject.toml` with a `[tool.ruff]` table, or Ruff under `project.dependencies`, `project.optional-dependencies`, `dependency-groups`, or Poetry's dependency tables
- `requirements.txt` or `requirements-dev.txt` listing `ruff` (any newline style; comments ignored)

### Which tools are covered

| Event | Tools matched |
|---|---|
| PreToolUse | `Bash`, `PowerShell`, `Edit`, `MultiEdit`, `Write`, `NotebookEdit`, `Read` |
| PostToolUse | `Edit`, `MultiEdit`, `Write`, `NotebookEdit` |
| Stop | every stop |

### Interpreter selection

Each hook launch goes through a bootstrapper that picks a Python in a predictable order: the project virtual environment first, then the current interpreter, with a Windows fallback to `py -3.10` if the active interpreter is too old.

## Known limits

Worth knowing before you rely on these:

- **Commands hidden inside interpreter arguments are not parsed.** `python -c "import shutil; shutil.rmtree('.git')"` and `bash -c "rm -rf /"` are **not** caught by the dangerous-command rules. Tracked in [issue #2](https://github.com/laceyp99/hooks/issues/2). Secret-file and Git-internals rules *do* apply to interpreters, but bluntly: they match anywhere in the command, so `bash -c "echo .env"` is denied even though it is harmless.
- **Bulk staging is not covered.** `git add .`, `git add -A`, and `git commit -a` will happily stage a secret file, because the command never names it. The guard cannot see this without consulting repository state. **Put the file in `.gitignore`** — that is the reliable fix, and a project-level `pre-commit` hook is the right place to enforce it for repos that need it.
- **The shell-command check uses a list of access verbs.** An unusual reader not on that list will pass. Tools that name a file in a dedicated field have no such gap.
- **A secret committed once stays in history.** Deleting it in a later commit does not remove it from earlier commits. If it was pushed, rotate the secret; cleaning history needs a rewrite.
- **These are guardrails, not a security boundary.** They exist to stop an agent's honest mistakes, not to withstand a determined adversary.

## Updating

Pull and re-run the installer:

```powershell
cd "$env:USERPROFILE\code\agent-hooks"
git pull
.\install.ps1
```

Re-running is safe and idempotent. It reports "already has the Agent Hooks entries" when nothing needs changing.

Two things to know:

- The installer **reconciles managed hook entries** with the template, so a corrected command or matcher reaches an existing install. If you hand-edited a managed hook's command, that edit is overwritten. Your own unmanaged hooks are never touched, and a backup is written first.
- **Codex re-trust.** Any change to a hook command invalidates Codex's trust hash, so re-approve the hooks in the Codex TUI after an update. Until you do, Codex skips them silently.

## Configuration

| What | Where |
|---|---|
| Claude Code registration | the `hooks` key of `%USERPROFILE%\.claude\settings.json` |
| Codex registration | `%USERPROFILE%\.codex\hooks.json` |
| Pi bridge | `%USERPROFILE%\.pi\agent\extensions\agent-hooks.ts` |
| Checked-in templates | `.claude/settings.example.json`, `.codex/hooks.example.json` |
| Shared logic, installed copy | `%USERPROFILE%\src\agent_hooks\` |

- Keep `cwd` set to `"."` so repo-aware hooks operate on the active project rather than the hooks bundle.
- `command` targets POSIX shells and uses `python3`; `commandWindows` runs under PowerShell. Both templates use the same `"$HOME/..."` path form.
- The Claude Code and Codex files differ where each harness needs different paths or command syntax. That is expected.

### Pi-specific environment variables

| Variable | Purpose |
|---|---|
| `AGENT_HOOKS_ROOT` | Path to this checkout, if it is not at `%USERPROFILE%\code\agent-hooks` |
| `AGENT_HOOKS_PYTHON` | Python executable to use, if `python.exe` is not on `PATH` |

Unlike the other two harnesses, the installed Pi extension is only a TypeScript bridge; it calls back into this source checkout to run the Python hooks. Keep the checkout in place.

## Manual installation

Use this only if `install.ps1` will not run. It does the same thing by hand, but performs **no backups** — copy your existing config files first.

<details>
<summary>Claude Code</summary>

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\hooks\scripts" | Out-Null
if (-not (Test-Path "$env:USERPROFILE\.claude\settings.json")) {
  Copy-Item ".claude\settings.example.json" "$env:USERPROFILE\.claude\settings.json"
}
Copy-Item -Force ".claude\hooks\run_hook.py" "$env:USERPROFILE\.claude\hooks\run_hook.py"
Copy-Item -Recurse -Force ".claude\hooks\scripts\*" "$env:USERPROFILE\.claude\hooks\scripts"
Copy-Item -Recurse -Force "src" "$env:USERPROFILE\"
```

If you already have a `settings.json`, do **not** overwrite it — copy the `hooks` block out of `.claude\settings.example.json` into it by hand.
</details>

<details>
<summary>Codex</summary>

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\hooks\scripts" | Out-Null
if (-not (Test-Path "$env:USERPROFILE\.codex\hooks.json")) {
  Copy-Item ".codex\hooks.example.json" "$env:USERPROFILE\.codex\hooks.json"
}
Copy-Item -Force ".codex\hooks\run_hook.py" "$env:USERPROFILE\.codex\hooks\run_hook.py"
Copy-Item -Recurse -Force ".codex\hooks\scripts\*" "$env:USERPROFILE\.codex\hooks\scripts"
Copy-Item -Recurse -Force "src" "$env:USERPROFILE\"
```

Then approve the hooks in the Codex TUI.
</details>

<details>
<summary>Pi</summary>

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.pi\agent\extensions" | Out-Null
Copy-Item -Force ".pi\agent\extensions\agent-hooks.ts" "$env:USERPROFILE\.pi\agent\extensions\agent-hooks.ts"
```

Back up any existing bridge first if you have local edits.
</details>

## Troubleshooting

### The hooks never fire

1. **Codex:** almost always the trust prompt. Open the TUI and approve the hooks. There is no warning when Codex skips an untrusted hook.
2. **Claude Code:** confirm the `hooks` key exists in `%USERPROFILE%\.claude\settings.json` and that `%USERPROFILE%\.claude\hooks\run_hook.py` is present. Start with `claude --debug` and look for "Hook script not found" or a spawn failure.
3. Confirm `python --version` reports 3.10 or newer *in the shell the agent uses*, which may not be the shell you tested in.
4. Run the [verification snippets](#5-verify-it-works). If they deny correctly, the hooks work and the problem is registration, not logic.

### "Hook script not found"

The installed bundle is incomplete. Re-run `.\install.ps1` and answer `y` to the "Refresh managed runtime files" prompts. All four of these must exist:

```powershell
Test-Path "$env:USERPROFILE\.claude\hooks\run_hook.py"
Test-Path "$env:USERPROFILE\.claude\hooks\scripts\pre_tool_security.py"
Test-Path "$env:USERPROFILE\.codex\hooks\run_hook.py"
Test-Path "$env:USERPROFILE\src\agent_hooks\common.py"
```

### The installer stops on a prompt

`install.ps1` is interactive by design and cannot run under `powershell -NonInteractive`; `Read-Host` throws instead of taking the default. Run it in a normal PowerShell window.

### The hook reports that it could not parse the payload

If a hook prints this on stderr, it saw something it could not read and **allowed the tool call unchecked**:

```
agent-hooks: could not parse the hook payload on stdin; allowing the tool call unchecked.
```

A hook that cannot see the tool call has no grounds to deny it, so this is deliberately fail-open — failing closed would break every session the moment a host changed its wire format. The warning exists so the decision is visible rather than silent. If you see it during normal use, the host is sending something unexpected and it is worth reporting.

A leading byte order mark is **not** a cause of this. PowerShell prepends one when piping a string into a native program, and the payload reader consumes it, so hand-testing from either shell gives the real answer.

### A legitimate command was denied

Check it against [What the hooks do](#what-the-hooks-do). If a command that only *mentions* a protected name was blocked, or an ordinary edit was refused, that is a bug worth reporting — include the exact command and the full deny message.

### Turning off the end-of-session rewrites

```powershell
$env:AGENT_HOOKS_STOP_FIX = "0"
```

Set it in the environment the agent runs in. Checks still run; nothing is rewritten.

## Uninstall

Remove the `hooks` key from `%USERPROFILE%\.claude\settings.json`, delete `%USERPROFILE%\.codex\hooks.json`, and delete the installed bundles:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.claude\hooks"
Remove-Item -Recurse -Force "$env:USERPROFILE\.codex\hooks"
Remove-Item -Recurse -Force "$env:USERPROFILE\src\agent_hooks"
Remove-Item -Force "$env:USERPROFILE\.pi\agent\extensions\agent-hooks.ts"
```

Your timestamped `.bak-*` files are left in place; delete them once you are satisfied.
