"""Dict-dispatch executor for the GTM triage agent.

Mirrors aether/agents/executor.py: tool lookup by name, try/except, failure
returns ({}, error_msg) so the agent observes and reasons — never raises.
"""

from __future__ import annotations

import logging

from gtm_triage.tools.registry import ToolRegistry
from gtm_triage.trace.store import TraceStore

logger = logging.getLogger(__name__)


class Executor:
    def __init__(self, registry: ToolRegistry, trace: TraceStore) -> None:
        self._registry = registry
        self._trace = trace

    def dispatch(self, tool_name: str, args: dict, run_id: str, step_id: str) -> tuple[dict, str | None]:
        """Dispatch a single tool. Returns (result, error_or_none).

        A failure does NOT raise — it returns ({}, error_msg) so the loop
        treats it as an observation and the model reasons about the next step.
        """
        tool = self._registry.get(tool_name)
        if tool is None:
            error_msg = f"Unknown tool '{tool_name}'"
            logger.warning("dispatch: %s", error_msg)
            return {}, error_msg

        self._trace.write(
            run_id=run_id,
            event_type="tool_call",
            agent="executor",
            payload={"tool": tool_name, "args": args},
        )

        try:
            result = tool.run(args, run_id=run_id)
            self._trace.write(
                run_id=run_id,
                event_type="tool_response",
                agent="executor",
                payload={"tool": tool_name, "result": str(result)[:4000]},
            )
            return result, None
        except Exception as exc:
            error_msg = str(exc)
            logger.warning("dispatch '%s' failed: %s", tool_name, error_msg)
            self._trace.write(
                run_id=run_id,
                event_type="tool_response",
                agent="executor",
                payload={"tool": tool_name},
                error=error_msg,
            )
            return {}, error_msg
