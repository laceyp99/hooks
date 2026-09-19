# Reality Check Findings

Scope: repository snapshot at `21b4239` on `t3code/audit-repo-with-subagents`. The branch is identical to `origin/main`, so this is a full snapshot audit rather than a branch-diff review.

## RC-001

- Severity: High
- Location: `src/agent_hooks/security.py:148`
- Risk: A mutating file tool can reach protected Git internals through a path beginning with the allowed `.github/` prefix, such as `.github/../.git/config`.
- Evidence: `_matches_protected_git_path()` returns early for every `.github/` prefix before it checks later path segments for `.git`. Direct reproduction returned `False` for `.github/../.git/config`.
- Recommendation: Normalize and resolve path segments before applying allowlists, and only allow `.github` when no protected `.git` segment exists. Add traversal and nested-`.git` tests.
- Disposition: Fixed
- Route: `/assembly` can implement and test this fix directly.

## RC-002

- Severity: High
- Location: `src/agent_hooks/dangerous_commands.py:19`
- Risk: A multiline Bash or PowerShell command can place a destructive operation after a newline and bypass the pre-tool safety hook.
- Evidence: Command-boundary regexes recognize only start-of-string, semicolon, ampersand, or pipe. `_normalize_command()` then collapses newlines to spaces. Direct reproductions allowed `echo safe\nrm -rf /` and `echo safe\nSet-Content .git/config x`.
- Recommendation: Preserve or explicitly recognize CR/LF command boundaries before whitespace normalization. Add multiline Bash and PowerShell cases for every protected command family.
- Disposition: Fixed
- Route: `/assembly` can implement and test this fix directly.

## RC-003

- Severity: Medium
- Location: `src/agent_hooks/post_tool_cleaner.py:47`
- Risk: The post-tool hook can run `ruff check --fix` and `ruff format` on an existing Python file outside the repository, or on an unrelated file merely mentioned in tool input.
- Evidence: `_collect_python_paths()` recursively scans every input string, accepts absolute paths, resolves symlinks, and checks only that the path exists. `main()` passes every collected path to mutating Ruff commands without checking repository containment or that the field represents an edited target.
- Recommendation: Extract paths only from known target fields and reject resolved paths outside `Path.cwd()` unless an explicit contract permits them. Add tests for unrelated text, absolute external paths, and symlink escapes.
- Disposition: Fixed
- Route: `/assembly` can implement and test this fix directly.

## RC-004

- Severity: Medium
- Location: `install.ps1:309`
- Risk: Re-running the installer never updates an existing Pi bridge, so users retain stale bridge behavior after repository fixes.
- Evidence: `Install-PiBridge` returns whenever the destination exists, and both the installer prompt and README describe installation only when missing.
- Recommendation: Treat the Pi bridge as a managed runtime file. Offer the same explicit refresh behavior used for Copilot and Codex, with a backup if preserving local edits matters.
- Disposition: Fixed
- Route: `/assembly` can implement and test this installer change.

## RC-005

- Severity: Low
- Location: `install.ps1:107`
- Risk: A Copilot config missing only its required `version` property is repaired in memory but never written.
- Evidence: `Ensure-Property` adds `version`, but `Merge-CopilotConfig` does not set `$changed` for that mutation. If managed hooks already exist, `Install-Config` returns as though nothing changed.
- Recommendation: Make `Ensure-Property` report whether it mutated the object, and include that result in the merge's changed state. Add an isolated config-merge test.
- Disposition: Open (deferred by owner decision; intentionally left unaddressed)
- Route: `/assembly` can implement and test this fix directly.

## RC-006

- Severity: Low
- Location: `src/agent_hooks/ruff_support.py:16`
- Risk: Ruff cleanup is silently skipped for valid project declarations such as Poetry's `ruff = "^0.6"` and a bare `ruff` line in a CRLF requirements file.
- Evidence: Detection is a fixed substring list. A direct reproduction with Poetry dependency syntax returned `False`.
- Recommendation: Parse `pyproject.toml` with `tomllib` and parse requirement lines independent of newline style. Keep malformed-file behavior best effort.
- Disposition: Fixed
- Route: `/assembly` can implement and test this fix directly.

## RC-007

- Severity: Medium
- Location: `src/agent_hooks/dangerous_commands.py:36`
- Risk: Ordinary PowerShell presentation commands such as `Format-Table`, `Format-List`, and `Format-Hex` are denied as though they were disk-formatting commands.
- Evidence: The `format\b` pattern treats the hyphen as a word boundary. Direct reproductions returned `True` for all three safe commands. The supplied operational report attributes 143 of 144 current dangerous-command records containing `format` to this pattern.
- Recommendation: Match an actual `format` command token followed by whitespace, arguments, a separator, or end-of-command. Add explicit allow cases for PowerShell's `Format-*` cmdlets.
- Disposition: Fixed
- Route: `/assembly` can implement and test this fix directly.

## RC-008

- Severity: Medium
- Location: `src/agent_hooks/dangerous_commands.py:62`
- Risk: Non-executable data inside command-tool payloads, including test fixtures, patch text, documentation, and regexes, can trigger a denial when it merely mentions a dangerous command or protected path.
- Evidence: `_find_dangerous_command()` and the shared string traversal recursively inspect every string instead of extracting the command field. Direct reproduction denied patch prose containing `curl https://example.test/install | bash`; the environment-path detector likewise matched documentation mentioning `src/.env`. The supplied report says 147 of 148 combined dangerous-command records were ordinary commands or data false positives.
- Recommendation: Define supported payload schemas per tool and inspect only executable command fields or actual file targets. Keep unknown schemas fail-visible without treating arbitrary content as executable intent.
- Disposition: Fixed
- Route: `/assembly` should address this alongside RC-002 because both require structured command extraction.

## RC-009

- Severity: Medium
- Location: `src/agent_hooks/session_stop.py:33`
- Risk: Ending a session runs `ruff check . --fix` and `ruff format .` across the entire repository, which can silently rewrite files unrelated to the current task.
- Evidence: The Stop hook unconditionally runs both mutating repository-wide commands whenever Ruff is detected. Tests assert this sequence, so it is intentional current behavior, but there is no task-file scope, opt-in, or diff review boundary.
- Recommendation: Make Stop check-only by default. If automatic fixes remain available, require explicit opt-in and scope them to files changed by the task.
- Disposition: Fixed
- Route: `/prelude` should confirm the desired cleanup policy before implementation because this changes intended product behavior.

## Open questions and residual risks

- Structured argv payloads such as `["rm", "-rf", "/"]` are checked one string at a time and would bypass detection. Confirm whether any supported host emits that payload shape before treating it as a separate defect.
- The installer and Pi TypeScript bridge have no automated behavioral coverage. This increases regression risk around config merging, refreshes, subprocess failures, and event-state handling.
- The audit did not execute the installer against a disposable user profile or run the Pi bridge inside the Pi host.
- The supplied report records 22 Copilot hook-runner failures caused by `spawn pwsh.exe ENOENT`. This machine currently has `powershell.exe` but no `pwsh.exe` on `PATH`, which corroborates the environment mismatch. It remains unclear whether the repository can choose Copilot's shell executable or must document PowerShell 7 as a host prerequisite, so this is retained as an operational blocker rather than a confirmed code defect.
