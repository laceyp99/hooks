import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";
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
const BUNDLE_DIRS = [".codex", ".copilot"] as const;
const pendingToolInputs = new Map<string, Record<string, unknown>>();

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
	signal: AbortSignal | undefined,
): Promise<HookResponse | undefined> {
	const runHookPath = resolveBundlePath("run_hook.py");
	const scriptPath = resolveBundlePath(join("scripts", scriptName));
	if (!runHookPath || !scriptPath) {
		return undefined;
	}

	const [command, extraArgs] = resolvePythonCommand();
	const child = spawn(command, [...extraArgs, runHookPath, scriptPath], {
		cwd: process.cwd(),
		env: { ...process.env },
		stdio: ["pipe", "pipe", "pipe"],
		signal,
	});

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

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", async (event, ctx) => {
		const payload = {
			tool_name: event.toolName,
			tool_input: event.input,
		};

		const [securityResponse, dangerousResponse] = await Promise.all([
			runHook("pre_tool_security.py", payload, ctx.signal),
			runHook("pre_tool_dangerous_commands.py", payload, ctx.signal),
		]);

		const blockedResponse = [securityResponse, dangerousResponse].find(isDenied);
		if (blockedResponse) {
			pendingToolInputs.delete(event.toolCallId);
			const reason = getReason(blockedResponse) ?? "Blocked by agent hooks.";
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

		const response = await runHook("post_tool_cleaner.py", payload, ctx.signal);
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
		const response = await runHook("session_stop.py", {}, ctx.signal);
		if (!response || !isBlockResponse(response)) {
			return;
		}

		const reason = getReason(response) ?? "Ruff reported issues during shutdown cleanup.";
		if (ctx.hasUI) {
			ctx.ui.notify(reason, "warning");
		}
	});
}
