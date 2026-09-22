import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
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

// The events bin/run_hook.py dispatches on, one Python process per event.
type HookEvent = "pre-tool" | "post-tool" | "stop";

const DEFAULT_HOOKS_ROOT = join(homedir(), "code", "agent-hooks");
const pendingToolInputs = new Map<string, Record<string, unknown>>();

function resolveHooksRoot(): string | undefined {
	const candidate = process.env.AGENT_HOOKS_ROOT?.trim() || DEFAULT_HOOKS_ROOT;
	return existsSync(candidate) ? candidate : undefined;
}

// The runner lives at bin/run_hook.py in the checkout. It runs every event in the interpreter
// this bridge launches, never in a project virtualenv, and finds src/agent_hooks next to itself.
function resolveRunnerPath(): string | undefined {
	const root = resolveHooksRoot();
	if (!root) {
		return undefined;
	}

	const candidate = join(root, "bin", "run_hook.py");
	return existsSync(candidate) ? candidate : undefined;
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
	event: HookEvent,
	payload: Record<string, unknown>,
	signal: AbortSignal | undefined,
): Promise<HookResponse | undefined> {
	const runHookPath = resolveRunnerPath();
	if (!runHookPath) {
		return undefined;
	}

	const [command, extraArgs] = resolvePythonCommand();
	let child: ChildProcessWithoutNullStreams;
	try {
		child = spawn(command, [...extraArgs, runHookPath, event], {
			cwd: process.cwd(),
			env: { ...process.env },
			stdio: ["pipe", "pipe", "pipe"],
			signal,
		});
	} catch (error) {
		console.warn(`[agent-hooks] ${event} could not start: ${String(error)}`);
		return undefined;
	}

	// If the interpreter is missing or the child dies before reading its payload, writing to
	// stdin fails with EPIPE. Without a listener that error is uncaught and takes down the host
	// process instead of just skipping this hook. The exit code already reports the failure.
	child.stdin.on("error", () => {});

	let stdout = "";
	let stderr = "";

	child.stdout.setEncoding("utf-8");
	child.stderr.setEncoding("utf-8");
	child.stdout.on("data", (chunk: string) => {
		stdout += chunk;
	});
	child.stderr.on("data", (chunk: string) => {
		stderr += chunk;
	});

	const exitCode = await new Promise<number>((resolve) => {
		child.on("error", () => resolve(-1));
		child.on("close", (code) => resolve(code ?? -1));

		child.stdin.end(`${JSON.stringify(payload)}\n`);
	});

	if (exitCode !== 0) {
		const message = stderr.trim();
		if (message) {
			console.warn(`[agent-hooks] ${event} failed: ${message}`);
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

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", async (event, ctx) => {
		const payload = {
			tool_name: event.toolName,
			tool_input: event.input,
		};

		// One process runs both the secret-file/Git-internals rules and the dangerous-command
		// rules, and reports at most one decision.
		const response = await runHook("pre-tool", payload, ctx.signal);
		if (isDenied(response)) {
			pendingToolInputs.delete(event.toolCallId);
			const reason = getReason(response) ?? "Blocked by agent hooks.";
			if (ctx.hasUI) {
				ctx.ui.notify(reason, "warning");
			}

			return { block: true, reason };
		}

		pendingToolInputs.set(event.toolCallId, event.input);
	});

	pi.on("tool_execution_end", async (event, ctx) => {
		const toolInput = pendingToolInputs.get(event.toolCallId);
		pendingToolInputs.delete(event.toolCallId);

		const payload = {
			tool_name: event.toolName,
			tool_input: toolInput ?? {},
		};

		const response = await runHook("post-tool", payload, ctx.signal);
		const additionalContext = response?.hookSpecificOutput?.additionalContext?.trim();
		if (additionalContext) {
			pi.sendMessage(
				{
					customType: "agent-hooks:post-tool-cleaner",
					content: additionalContext,
					display: false,
				},
				{ deliverAs: "followUp" },
			);
		}
	});

	pi.on("session_shutdown", async (_event, ctx) => {
		const response = await runHook("stop", {}, ctx.signal);
		if (!response || !isBlockResponse(response)) {
			return;
		}

		const reason = getReason(response) ?? "Ruff reported issues during shutdown cleanup.";
		if (ctx.hasUI) {
			ctx.ui.notify(reason, "warning");
		}
	});
}
