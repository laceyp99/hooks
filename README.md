# Agent Hooks

Local guardrails for coding agents. The hooks block a small set of genuinely dangerous actions, keep secrets out of an agent's reach, and tidy Python as you go.

One set of Python hook logic is shared by four harnesses: **Claude Code**, **Codex**, **Pi**, and **OpenCode**. Each harness gets a thin wrapper; the behavior lives once, under `src/agent_hooks/`.

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

Any folder works, with one exception: the **Pi** and **OpenCode** bridges look for the checkout at `%USERPROFILE%\code\agent-hooks` unless you set `AGENT_HOOKS_ROOT`. If you only use Claude Code and Codex, put it wherever you like.

### 3. Run the installer

```powershell
.\install.ps1
```

It asks before each step, so you can install only the harnesses you use. Answer `y` to the ones you want.

Every write is backed up first to a timestamped `.bak-*` file next to the original. For Claude Code it touches **only** the `hooks` key of `settings.json`; every other setting you have is left exactly as it was.

### 4. Trust the hooks in Codex

**If you use Codex, do not skip this.** Codex keeps a trust hash for each hook in `config.toml`, and it will **silently skip a hook it does not trust** — the session runs normally, with nothing in the output to tell you a guard was bypassed.

Open the Codex TUI after installing and approve the hooks when prompted. The installer prints a reminder whenever it writes `hooks.json`.

Claude Code, Pi, and OpenCode have no equivalent step. OpenCode loads every file in its plugin directory automatically, with nothing to approve.

### 5. Verify it works

Run this from Git Bash, or from PowerShell with the equivalent quoting:

```bash
runner="$HOME/.claude/hooks/run_hook.py"

# Expect a JSON deny payload:
echo '{"tool_name":"Bash","tool_input":{"command":"cat .env"}}' | python "$runner" pre-tool

# Expect no output at all, because this only mentions the file:
echo '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"document .env\""}}' | python "$runner" pre-tool
```

The first prints a `permissionDecision: deny` payload naming `.env`. The second prints nothing, which means allowed. If both print nothing, the hooks are not wired up — see [Troubleshooting](#troubleshooting).

Swap `.claude` for `.codex` to check the Codex bundle the same way.

## What the hooks do

### Secret files

Access to secret-bearing files is denied: `.env`, common `.env` variants, `.envrc`, `*.env`, `*.secret`, `*.secrets`, `*.pem`, `id_rsa`, `credentials.json`, and anything under `.direnv/`. Direct access to templates such as `.env.example` and `.env.sample` stays allowed.

How the check applies depends on how the tool names its target:

- **Tools with a file field** (`Read`, `Edit`, `Write`, `NotebookEdit`, `apply_patch`, and MCP tools) are denied on the name, and on the **path it resolves to**. Relative paths are resolved against the session's working directory, then through symlinks, NTFS junctions, 8.3 short names, and `\\?\` prefixes; alternate data streams (`.env::$DATA`) count as the file itself; and a hard link to a protected file in the same directory or the working directory is caught too.
- **`Grep`** returns file contents, so it is denied when its `path` targets a protected file or its `glob` (OpenCode: `include`) would select one, such as `**/.env*`. The search `pattern` is never checked, so searching *for* the text `.env` is fine.
- **Shell commands** are denied whenever their text contains a protected path or a wildcard that can select one, whatever program is named. This catches commands such as `wc`, PowerShell reads and writes, interpreter code, redirects, and pipelines without relying on a reader-command list. It also blocks harmless mentions in shell text, including commit messages and search patterns.

  ```bash
  git commit -m "tighten secret-file guard"
  gh pr create --body "Adds secret-file checks"
  grep -rn "secret-file guard" src/
  ```

  while these are denied:

  ```bash
  cat .env
  wc -c .env
  Get-Content -LiteralPath .env -Raw
  cp .env /tmp/elsewhere
  cat .e*
  git add .env
  git commit -m "fix .env loading"  # even a text-only mention is blocked
  ```

Static checks also expand simple literal assignments, string joins, printf escapes, and base64
arguments. PowerShell `-EncodedCommand`, `Invoke-Expression`/`iex`, and `Start-Process` are denied
because their command text can hide the path from inspection.

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
| PreToolUse | `Bash`, `PowerShell`, `Edit`, `MultiEdit`, `Write`, `NotebookEdit`, `Read`, `Grep`, `mcp__*` |
| PostToolUse | `Edit`, `MultiEdit`, `Write`, `NotebookEdit` |
| Stop | every stop |

Those are Claude Code's tool names. Every harness is matched against the same sets, case-insensitively, so OpenCode's lowercase `bash`, `edit`, `write`, `read`, and `apply_patch` land on the same rules; `glob` returns only paths and is not checked. Argument field names are matched the same way, which is why OpenCode's camelCase `filePath` is recognized alongside Claude Code's `file_path`. `apply_patch` sends its patch in `patchText`; the patch is recognized by its opening `*** Begin Patch` marker rather than by the field name, so only its file headers are inspected, as with Codex.

### Interpreter selection

Each harness event is one command, `run_hook.py pre-tool | post-tool | stop`, and one Python process. `pre-tool` reads the payload once and runs the secret-file, Git-internals, and dangerous-command rules together, returning at most one decision.

The hooks run in the Python the harness launched, never in the project's virtual environment, so a repository cannot swap in the interpreter that checks it. The only re-exec is a Windows fallback to `py -3.10` when the launching Python is too old.

One thing is still taken from the project: **the Ruff executable**, because a project pins the version its config is written for. The two Ruff hooks look for `ruff` in `.venv`, `venv`, or `env`, then fall back to the current interpreter and `PATH`, and always run it as a separate process. So a repository you have opted in to Ruff supplies a binary that runs after an edit to a Python file and at session end. The project's *interpreter* is never launched: `<project>/.venv/python -m ruff` would execute that environment's `sitecustomize` and `.pth` files, so an environment holding Ruff without its executable is skipped instead.

## Known limits

Worth knowing before you rely on these:

- **Dangerous-command checks do not parse code passed to interpreters.** `python -c "import shutil; shutil.rmtree('.git')"` and `bash -c "rm -rf /"` are **not** caught by those rules. Tracked in [issue #2](https://github.com/laceyp99/hooks/issues/2). The secret-file check scans protected path mentions throughout the shell command, including nested interpreter text.
- **Arbitrary runtime path construction remains outside text inspection.** The hook folds simple literal assignments and joins, printf escapes, and base64 command text, and blocks PowerShell encoded commands and dynamic evaluation. A custom script can still compute a file name in a way the hook cannot recognize. Claude Code also gets `Read` and `Edit` deny rules from the checked-in template; for enforcement across arbitrary subprocesses, keep secrets outside the agent's workspace or deny access through the agent account's OS ACL.
- **Hard-link detection is local.** A hard link to a secret is caught only when the secret sits in the same directory as the link or in the working directory.
- **A broad search glob is allowed even when a secret is under it.** `Grep` is denied for a glob aimed at a secret, such as `**/.env*` or `*.env`, but a glob that also selects ordinary files is treated as a broad search and allowed. `config/*` therefore passes even though `config/.env` is among the files it selects. Naming the file, in `path` or in the glob itself, is what the rule catches.
- **Bulk staging is not covered.** `git add .`, `git add -A`, and `git commit -a` will happily stage a secret file, because the command never names it. The guard cannot see this without consulting repository state. **Put the file in `.gitignore`** — that is the reliable fix, and a project-level `pre-commit` hook is the right place to enforce it for repos that need it.
- **Shell text can produce false positives.** A harmless command that mentions a protected path, or uses a broad wildcard that could select one, is denied. Use a description that does not spell the protected path when writing commit messages or search patterns from the shell.
- **A secret committed once stays in history.** Deleting it in a later commit does not remove it from earlier commits. If it was pushed, rotate the secret; cleaning history needs a rewrite.
- **OpenCode cannot block at the end of a session.** Its only session-completion signal is the `session.idle` event, which fires after every turn the agent finishes and has no way to refuse. The Ruff sweep therefore *reports* on OpenCode where Claude Code's `Stop` hook would block, so `AGENT_HOOKS_STOP_FIX` still governs whether files are rewritten but a finding never holds the session open.
- **These are guardrails, not a security boundary.** Claude Code's path rules cover its built-in file tools and shell commands it recognizes, but not arbitrary subprocesses. See [Claude Code permission rules](https://code.claude.com/docs/en/permissions#read-and-edit). To keep a secret from any process the agent can start, keep it outside the workspace or deny the agent account access with an ACL.

## Updating

Pull and re-run the installer:

```powershell
cd "$env:USERPROFILE\code\agent-hooks"
git pull
.\install.ps1
```

Re-running is safe and idempotent. It reports "already has the Agent Hooks entries" when nothing needs changing.

Two things to know:

- The installer **reconciles managed hook entries and protected-path permission rules** with the template, so a corrected command, matcher, or deny rule reaches an existing install. If you hand-edited a managed hook's command, that edit is overwritten. Your own unmanaged hooks are never touched, and a backup is written first.
- **Codex re-trust.** Any change to a hook command invalidates Codex's trust hash, so re-approve the hooks in the Codex TUI after an update. Until you do, Codex skips them silently.

### Updating from a per-script install

Installs made before the single runner registered one hook per script: `pre_tool_security.py` and `pre_tool_dangerous_commands.py` for PreToolUse, and a script path for each of the other events. The installer migrates those to one entry per event, so there is nothing to edit by hand. It helps to know exactly what it changes.

**What it rewrites**

- The two PreToolUse entries collapse into one `run_hook.py pre-tool`, and the post-tool and stop entries are rewritten to `post-tool` and `stop`. A container left empty by the collapse is removed.
- The PreToolUse matcher becomes `Bash|PowerShell|Edit|MultiEdit|Write|NotebookEdit|Read|Grep|mcp__.*`. **The matcher belongs to the container**, so one of your own hooks sharing that container starts firing on `Grep` and MCP tools too. Move it to its own container first if you do not want that.
- Any entry whose command mentions `run_hook.py` counts as managed, and its command, timeout, and status message are reset to the template's. A hand-tuned timeout does not survive.
- `settings.json` is rewritten whole, so indentation is normalized and non-ASCII characters come back as `\uXXXX` escapes. Only the `hooks` key changes in substance; the file is written as UTF-8 with no byte order mark.

**What it deletes**

- The five per-script files and their `.pyc` files under each harness's `hooks\scripts`, and `bootstrap.py` from `%USERPROFILE%\src\agent_hooks`. A directory holding anything else keeps that file and stays.

**Before and after**

Every write is backed up to a timestamped `.bak-*` beside the original, so comparing the two is the check that matters:

```powershell
$settings = "$env:USERPROFILE\.claude\settings.json"
$backup = Get-ChildItem "$settings.bak-*" | Sort-Object LastWriteTime | Select-Object -Last 1
git diff --no-index $backup.FullName $settings
```

Then confirm the hooks still fire, with the [verification snippets](#5-verify-it-works). Restoring is a copy: `Copy-Item $backup.FullName $settings -Force`.

## Configuration

| What | Where |
|---|---|
| Claude Code registration | the `hooks` key of `%USERPROFILE%\.claude\settings.json` |
| Codex registration | `%USERPROFILE%\.codex\hooks.json` |
| Pi bridge | `%USERPROFILE%\.pi\agent\extensions\agent-hooks.ts` |
| OpenCode plugin | `%USERPROFILE%\.config\opencode\plugins\agent-hooks.ts`, or under `%XDG_CONFIG_HOME%\opencode\plugins\` when that is set |
| Checked-in templates | `.claude/settings.example.json`, `.codex/hooks.example.json`, `.opencode/agent-hooks.example.ts` |
| Shared logic, installed copy | `%USERPROFILE%\src\agent_hooks\` |

- Keep `cwd` set to `"."` so repo-aware hooks operate on the active project rather than the hooks bundle.
- `command` targets POSIX shells and uses `python3`; `commandWindows` runs under PowerShell. Both templates use the same `"$HOME/..."` path form.
- The Claude Code and Codex files differ where each harness needs different paths or command syntax. That is expected.

### Bridge environment variables

These apply to the two TypeScript bridges, **Pi** and **OpenCode**.

| Variable | Purpose |
|---|---|
| `AGENT_HOOKS_ROOT` | Path to this checkout, if it is not at `%USERPROFILE%\code\agent-hooks` |
| `AGENT_HOOKS_PYTHON` | Python executable to use, if `python.exe` is not on `PATH` |

Unlike Claude Code and Codex, these two install only a TypeScript bridge; it calls back into this source checkout to run the Python hooks. Keep the checkout in place.

The OpenCode plugin is a single auto-loaded file, so installing it is the whole registration step. It runs the same three hook events, mapped onto OpenCode's own hooks:

| OpenCode hook | Event | Notes |
|---|---|---|
| `tool.execute.before` | `pre-tool` | Denies by throwing; the reason reaches the model as the failed tool call |
| `tool.execute.after` | `post-tool` | Appends any Ruff summary to the tool's output |
| `event` (`session.idle`) | `stop` | Reports only, one sweep at a time; see [Known limits](#known-limits) |

The plugin skips the guards for OpenCode built-ins that neither name a file nor run a command, such as `glob`, `todowrite`, and `webfetch`, and runs the cleaner only for tools that write. Each hook costs a Python startup, so this keeps inert tool calls fast. `grep` is not skipped: it returns file contents, so it goes through the guards. Any tool it does not recognize, including MCP tools, still goes through the guards.

The checked-in plugin is `.opencode/agent-hooks.example.ts`, deliberately outside `.opencode/plugins/`. OpenCode scans that directory in whatever project it runs in, so a copy there would load a second time whenever OpenCode runs inside this repo.

## Manual installation

Use this only if `install.ps1` will not run. It does the same thing by hand, but performs **no backups** — copy your existing config files first.

<details>
<summary>Claude Code</summary>

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\hooks" | Out-Null
if (-not (Test-Path "$env:USERPROFILE\.claude\settings.json")) {
  Copy-Item ".claude\settings.example.json" "$env:USERPROFILE\.claude\settings.json"
}
Copy-Item -Force "bin\run_hook.py" "$env:USERPROFILE\.claude\hooks\run_hook.py"
Copy-Item -Recurse -Force "src" "$env:USERPROFILE\"
```

If you already have a `settings.json`, do **not** overwrite it — copy both the `hooks` and `permissions` blocks from `.claude\settings.example.json` into it. The installer merges these deny rules into existing Claude Code settings and keeps the rules already there.
</details>

<details>
<summary>Codex</summary>

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\hooks" | Out-Null
if (-not (Test-Path "$env:USERPROFILE\.codex\hooks.json")) {
  Copy-Item ".codex\hooks.example.json" "$env:USERPROFILE\.codex\hooks.json"
}
Copy-Item -Force "bin\run_hook.py" "$env:USERPROFILE\.codex\hooks\run_hook.py"
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

<details>
<summary>OpenCode</summary>

```powershell
$configRoot = if ($env:XDG_CONFIG_HOME) { $env:XDG_CONFIG_HOME } else { "$env:USERPROFILE\.config" }
New-Item -ItemType Directory -Force "$configRoot\opencode\plugins" | Out-Null
Copy-Item -Force ".opencode\agent-hooks.example.ts" "$configRoot\opencode\plugins\agent-hooks.ts"
```

That is the whole registration: OpenCode loads every file in the plugin directory at startup, so there is no config entry to add. Copy it to `.opencode/plugins/agent-hooks.ts` inside a project instead if you want the guards in that project only. Back up any existing plugin first if you have local edits.
</details>

## Troubleshooting

### The hooks never fire

1. **Codex:** almost always the trust prompt. Open the TUI and approve the hooks. There is no warning when Codex skips an untrusted hook.
2. **Claude Code:** confirm the `hooks` key exists in `%USERPROFILE%\.claude\settings.json` and that `%USERPROFILE%\.claude\hooks\run_hook.py` is present. Start with `claude --debug` and look for "agent_hooks package not found" or a spawn failure.
3. Confirm `python --version` reports 3.10 or newer *in the shell the agent uses*, which may not be the shell you tested in.
4. Run the [verification snippets](#5-verify-it-works). If they deny correctly, the hooks work and the problem is registration, not logic.

### "agent_hooks package not found"

The installed bundle is incomplete. Re-run `.\install.ps1` and answer `y` to the "Refresh managed runtime files" prompts. All three of these must exist:

```powershell
Test-Path "$env:USERPROFILE\.claude\hooks\run_hook.py"
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
Remove-Item -Force "$(if ($env:XDG_CONFIG_HOME) { $env:XDG_CONFIG_HOME } else { "$env:USERPROFILE\.config" })\opencode\plugins\agent-hooks.ts"
```

Your timestamped `.bak-*` files are left in place; delete them once you are satisfied.
