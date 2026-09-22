"""Single entry point for every hook event: ``run_hook.py <event>`` or ``python -m agent_hooks``.

Each harness launches one process per event. The payload is read from stdin once, the event's
rules run in this process, and at most one JSON response is written to stdout. An empty stdout
means allow, which is what every harness and bridge expects.
"""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable, Sequence
from typing import Any

from agent_hooks import dangerous_commands, post_tool_cleaner, security, session_stop
from agent_hooks.common import emit_response, load_stdin_payload

Response = dict[str, Any]
Rule = Callable[[Response], "Response | None"]

# Pre-tool rule sets in the order they are consulted. The first deny wins, so a call that trips
# both reports the secret-file or Git-internals reason, as the security hook used to run first.
PRE_TOOL_RULES: tuple[Rule, ...] = (
    security.evaluate,
    dangerous_commands.evaluate,
)

USAGE = "Usage: run_hook.py {pre-tool|post-tool|stop}\n"


def _is_deny(response: Response | None) -> bool:
    if not response:
        return False
    output = response.get("hookSpecificOutput")
    return isinstance(output, dict) and output.get("permissionDecision") == "deny"


def pre_tool(payload: Response) -> tuple[Response | None, bool]:
    """Run every pre-tool rule set and return the first deny, plus whether a rule set crashed.

    Each rule set used to run in its own process, so a crash in one never disabled the other.
    Catching per rule keeps that: a broken rule is reported on stderr and the next one still
    runs. A crash with no deny is surfaced to the caller as a failed exit rather than hidden.
    """
    failed = False
    for rule in PRE_TOOL_RULES:
        try:
            response = rule(payload)
        except Exception:
            failed = True
            sys.stderr.write(f"agent-hooks: {rule.__module__} raised an error:\n")
            traceback.print_exc(file=sys.stderr)
            continue

        if _is_deny(response):
            return response, failed

    return None, failed


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in ("pre-tool", "post-tool", "stop"):
        sys.stderr.write(USAGE)
        return 2

    event = args[0]
    payload = load_stdin_payload()
    if event == "pre-tool":
        response, failed = pre_tool(payload)
        emit_response(response)
        return 1 if failed and response is None else 0

    if event == "post-tool":
        emit_response(post_tool_cleaner.evaluate(payload))
    else:
        emit_response(session_stop.evaluate(payload))
    return 0
