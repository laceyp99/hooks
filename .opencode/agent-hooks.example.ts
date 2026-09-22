import type { Plugin } from "@opencode-ai/plugin";
import { type ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir, platform } from "node:os";
import { join } from "node:path";

type HookSpecificOutput = {
	hookEventName?: string;
	permissionDecision?: "deny" | "allow";
	permissionDecisionReason?: string;
	additionalContext?: string;
	decision?: "block" | string;
	reason?: string;
};

type HookResponse = {
	systemMessage?: string;
	hookSpecificOutput?: HookSpecificOutput;
};

const DEFAULT_HOOKS_ROOT = join(homedir(), "code", "agent-hooks");
const BUNDLE_DIRS = [".codex", ".claude"] as const;

// OpenCode has no AbortSignal on its hooks, unlike Pi's ctx.signal. A wedged Python process
// would otherwise hang the session forever, so each guard gets a hard timeout instead: short
// for the pre/post-tool guards, longer for the shutdown cleanup, mirroring the 30s/120s split
// in .claude/settings.example.json.
const GUARD_TIMEOUT_MS = 30_000;
const STOP_TIMEOUT_MS = 120_000;

// Every hook launch costs two interpreter startups (run_hook.py re-execs the resolved Python),
// a couple of hundred milliseconds on Windows. The Claude Code config avoids paying that on
// every tool call with a matcher; these gates are this bridge's equivalent. They are only an
// optimization: the Python side still decides everything for the calls that reach it.
//
// The pre-tool gate is a skip list rather than an allow list on purpose. These are OpenCode
// built-ins that neither touch a file by name nor run a command, so skipping them loses
// nothing. Any tool not listed, including MCP and plugin tools and any built-in added later,
// still goes to the guards, so a stale list costs latency rather than coverage.
const INERT_TOOLS = new Set([
	"glob",
	"grep",
	"invalid",
	"lsp",
	"plan_exit",
	"question",
	"skill",
	"task",
	"todowrite",
	"webfetch",
	"websearch",
]);

// The cleaner only lints tools whose names carry one of these markers. This mirrors
// WRITE_TOOL_MARKERS and _should_lint in src/agent_hooks/post_tool_cleaner.py exactly; every
// entry in its WRITE_TOOL_NAMES contains one of them. A drift here only skips linting, never a
// guard, which is why an allow list is acceptable for this hook and not for the one above.
const WRITE_TOOL_MARKERS = [
	"apply_patch",
	"create",
	"edit",
	"insert_edit",
	"move",
	"patch",
	"rename",
	"replace_string",
	"write",
] as const;

function isInertTool(toolName: string): boolean {
	return INERT_TOOLS.has(toolName.toLowerCase());
}

function isWriteTool(toolName: string): boolean {
	const name = toolName.toLowerCase();
	return WRITE_TOOL_MARKERS.some((marker) => name.includes(marker));
}

function resolveHooksRoot(): string | undefined {
	const candidate = process.env.AGENT_HOOKS_ROOT?.trim() || DEFAULT_HOOKS_ROOT;
	return existsSync(candidate) ? candidate : undefined;
}

function resolveBundlePath(relativePath: string): string | undefined {
	const root = resolveHooksRoot();
	if (!root) {
		return undefined;
	}

	for (const bundleDir of BUNDLE_DIRS) {
		const candidate = join(root, bundleDir, "hooks", relativePath);
		if (existsSync(candidate)) {
			return candidate;
		}
	}

	return undefined;
}

function resolvePythonCommand(): [string, string[]] {
	const explicit = process.env.AGENT_HOOKS_PYTHON?.trim();
	if (explicit) {
		return [explicit, []];
	}

	return platform() === "win32" ? ["python.exe", []] : ["python3", []];
}

function parseHookResponse(stdout: string): HookResponse | undefined {
	const lines = stdout
		.split(/\r?\n/)
		.map((line) => line.trim())
		.filter(Boolean);

	for (let index = lines.length - 1; index >= 0; index -= 1) {
		try {
			const parsed = JSON.parse(lines[index]) as unknown;
			if (parsed && typeof parsed === "object") {
				return parsed as HookResponse;
			}
		} catch {
			// Ignore non-JSON lines and keep looking for the last JSON payload.
		}
	}

	return undefined;
}

async function runHook(
	scriptName: string,
	payload: Record<string, unknown>,
	timeoutMs: number,
	cwd: string,
): Promise<HookResponse | undefined> {
	const runHookPath = resolveBundlePath("run_hook.py");
	const scriptPath = resolveBundlePath(join("scripts", scriptName));
	if (!runHookPath || !scriptPath) {
		return undefined;
	}

	const [command, extraArgs] = resolvePythonCommand();
	let child: ChildProcessWithoutNullStreams;
	try {
		child = spawn(command, [...extraArgs, runHookPath, scriptPath], {
			cwd,
			env: { ...process.env },
			stdio: ["pipe", "pipe", "pipe"],
		});
	} catch (error) {
		console.warn(`[agent-hooks] ${scriptName} could not start: ${String(error)}`);
		return undefined;
	}

	// If the interpreter is missing or the child dies before reading its payload, writing to
	// stdin fails with EPIPE. Without a listener that error is uncaught and takes down the host
	// process instead of just skipping this hook. The exit code already reports the failure.
	child.stdin.on("error", () => {});

	let stdout = "";
	let stderr = "";
	let timedOut = false;

	child.stdout.setEncoding("utf-8");
	child.stderr.setEncoding("utf-8");
	child.stdout.on("data", (chunk: string) => {
		stdout += chunk;
	});
	child.stderr.on("data", (chunk: string) => {
		stderr += chunk;
	});

	const exitCode = await new Promise<number>((resolve) => {
		// The timer settles the wait itself instead of waiting for the kill to produce a close
		// event. A child that ignores SIGTERM on POSIX never closes, and waiting on close would
		// hang the session exactly as if there were no timeout. Whichever of these fires first
		// wins; a promise ignores every later resolve.
		const timer = setTimeout(() => {
			timedOut = true;
			child.kill();
			resolve(-1);
		}, timeoutMs);

		child.on("error", () => {
			clearTimeout(timer);
			resolve(-1);
		});
		child.on("close", (code) => {
			clearTimeout(timer);
			resolve(code ?? -1);
		});

		child.stdin.end(`${JSON.stringify(payload)}\n`);
	});

	if (timedOut) {
		console.warn(`[agent-hooks] ${scriptName} timed out after ${timeoutMs}ms; allowing the call.`);
		return undefined;
	}

	if (exitCode !== 0) {
		const message = stderr.trim();
		if (message) {
			console.warn(`[agent-hooks] ${scriptName} failed: ${message}`);
		}
		return undefined;
	}

	return parseHookResponse(stdout);
}

function isDenied(response: HookResponse | undefined): response is HookResponse {
	return response?.hookSpecificOutput?.permissionDecision === "deny";
}

function isBlockResponse(response: HookResponse | undefined): response is HookResponse {
	return response?.hookSpecificOutput?.decision === "block";
}

function getReason(response: HookResponse | undefined): string | undefined {
	return (
		response?.hookSpecificOutput?.permissionDecisionReason ?? response?.hookSpecificOutput?.reason
	);
}

// OpenCode loads this file through its legacy plugin path, which treats every export as a
// plugin function and refuses to load the file if any export is not a function. Keep this the
// only export.
export const AgentHooks: Plugin = async ({ directory }) => {
	// The guards are repo-aware: session_stop.py shells out to git and Ruff relative to the
	// working directory, and post_tool_cleaner.py resolves edited paths against it. OpenCode
	// hands the plugin the project directory, which is what these need; the server's own
	// process.cwd() is not guaranteed to be it. This is the counterpart of keeping "cwd": "."
	// in the Claude Code and Codex hook entries.
	const hookCwd = directory || process.cwd();

	// OpenCode dispatches event hooks fire-and-forget and never awaits the previous call, so a
	// session.idle that lands while a sweep is still running would start a second one over the
	// same working tree. One sweep at a time; an idle that arrives mid-sweep is dropped, and the
	// next idle after it finishes picks up anything that changed.
	let stopSweepInFlight = false;

	return {
		"tool.execute.before": async (input, output) => {
			if (isInertTool(input.tool)) {
				return;
			}

			// OpenCode's tool names (bash, edit, write, read, apply_patch) and argument shapes
			// (edit/write/read's filePath) already match what normalize_tool_name and the
			// FILE_TARGET_FIELD_NAMES/COMMAND_FIELD_NAMES lookups on the Python side recognize
			// case-insensitively, so no name-translation table is needed here. apply_patch
			// carries its patch in patchText, which the Python side recognizes by the patch's
			// opening marker rather than by field name.
			const payload = {
				tool_name: input.tool,
				tool_input: output.args,
			};

			const [securityResponse, dangerousResponse] = await Promise.all([
				runHook("pre_tool_security.py", payload, GUARD_TIMEOUT_MS, hookCwd),
				runHook("pre_tool_dangerous_commands.py", payload, GUARD_TIMEOUT_MS, hookCwd),
			]);

			const blockedResponse = [securityResponse, dangerousResponse].find(isDenied);
			if (blockedResponse) {
				const reason = getReason(blockedResponse) ?? "Blocked by agent hooks.";
				// Throwing is the only way OpenCode lets a tool.execute.before hook block the
				// call; the message surfaces to the model as the failed tool call.
				throw new Error(reason);
			}
		},

		"tool.execute.after": async (input, output) => {
			if (!isWriteTool(input.tool)) {
				return;
			}

			const payload = {
				tool_name: input.tool,
				tool_input: input.args,
			};

			const response = await runHook("post_tool_cleaner.py", payload, GUARD_TIMEOUT_MS, hookCwd);
			const additionalContext = response?.hookSpecificOutput?.additionalContext?.trim();
			if (additionalContext) {
				output.output = typeof output.output === "string" ? output.output : "";
				output.output += `\n\n${additionalContext}`;
			}
		},

		event: async ({ event }) => {
			if (event.type !== "session.idle") {
				return;
			}

			if (stopSweepInFlight) {
				return;
			}

			// OpenCode has no blocking session-end hook the way Claude Code's Stop hook can
			// block, so a Ruff failure at shutdown is reported rather than enforced.
			stopSweepInFlight = true;
			let response: HookResponse | undefined;
			try {
				response = await runHook("session_stop.py", {}, STOP_TIMEOUT_MS, hookCwd);
			} finally {
				stopSweepInFlight = false;
			}
			if (!response || !isBlockResponse(response)) {
				return;
			}

			const reason = getReason(response) ?? "Ruff reported issues during shutdown cleanup.";
			console.warn(`[agent-hooks] ${reason}`);
		},
	};
};
