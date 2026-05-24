# aether/agents/executor.py

import logging
import time

from aether.config import settings
from aether.models.plan import ExecutionPlan, PlanStep
from aether.models.trace import TraceEvent
from aether.tools.base import BaseTool
from aether.tools.flag_item import FlagItemTool
from aether.tools.load_data import LoadDataTool
from aether.tools.run_sql import RunSQLTool
from aether.tools.write_report import WriteReportTool
from aether.trace.store import TraceStore

logger = logging.getLogger(__name__)


class ExecutorAgent:
    def __init__(self, extra_tools: dict[str, BaseTool] | None = None) -> None:
        self._tools: dict[str, BaseTool] = {
            "load_data": LoadDataTool(),
            "run_sql": RunSQLTool(),
            "flag_item": FlagItemTool(),
            "write_report": WriteReportTool(),
        }
        if extra_tools:
            self._tools.update(extra_tools)
        self._store = TraceStore(settings.db_path)

    def run(self, plan: ExecutionPlan) -> dict:
        state: dict[str, dict] = {}

        for step in plan.topological_order():
            merged_args = {**step.tool_args}
            if step.depends_on:
                merged_args["prior_results"] = {dep: state[dep] for dep in step.depends_on}

            result, error_msg = self._dispatch(step, merged_args, plan.run_id)
            if result is None:
                raise RuntimeError(f"Step '{step.step_id}' failed: {error_msg}")
            state[step.step_id] = result

        return state

    def reset_tool_state(self) -> None:
        """Reset all per-run accumulated state in every registered tool."""
        for tool in self._tools.values():
            tool.reset()

    def dispatch_one(self, tool_name: str, args: dict, run_id: str, step_id: str) -> tuple[dict, str | None]:
        """Dispatch a single tool by name without building a full plan.

        Used by the RAO loop — one tool call per iteration. A failure does NOT
        raise; it returns ({}, error_msg) so the loop can treat it as an
        observation and let the model reason about the failure on the next step.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            error_msg = f"Unknown tool '{tool_name}'"
            logger.warning("dispatch_one: %s", error_msg)
            return {}, error_msg

        self._store.write_event(TraceEvent.for_tool_call(
            run_id=run_id,
            agent="executor",
            tool=tool_name,
            args=args,
            step_id=step_id,
        ))

        t0 = time.time()
        try:
            result = tool.run(args)
            duration_ms = int((time.time() - t0) * 1000)
            logger.info("dispatch_one '%s' succeeded in %dms", tool_name, duration_ms)
            self._store.write_event(TraceEvent(
                run_id=run_id,
                step_id=step_id,
                agent="executor",
                event_type="tool_response",
                duration_ms=duration_ms,
                payload={"tool": tool_name, "result": str(result)[:1000]},
            ))
            return result, None
        except Exception as exc:
            duration_ms = int((time.time() - t0) * 1000)
            error_msg = str(exc)
            logger.warning("dispatch_one '%s' failed: %s", tool_name, error_msg)
            self._store.write_event(TraceEvent(
                run_id=run_id,
                step_id=step_id,
                agent="executor",
                event_type="tool_response",
                duration_ms=duration_ms,
                payload={"tool": tool_name},
                error=error_msg,
            ))
            return {}, error_msg

    def _dispatch(self, step: PlanStep, args: dict, run_id: str) -> tuple[dict | None, str]:
        tool = self._tools.get(step.tool)
        if tool is None:
            return None, f"Unknown tool '{step.tool}'"

        for attempt in range(1, 3):  # max 2 attempts
            self._store.write_event(TraceEvent.for_tool_call(
                run_id=run_id,
                agent="executor",
                tool=step.tool,
                args=args,
                step_id=step.step_id,
            ))

            t0 = time.time()
            try:
                result = tool.run(args)
                duration_ms = int((time.time() - t0) * 1000)
                logger.info("Step '%s' succeeded in %dms", step.step_id, duration_ms)
                self._store.write_event(TraceEvent(
                    run_id=run_id,
                    step_id=step.step_id,
                    agent="executor",
                    event_type="tool_response",
                    duration_ms=duration_ms,
                    payload={"tool": step.tool, "result": str(result)[:1000]},
                    attempt=attempt,
                ))
                return result, ""
            except Exception as exc:
                duration_ms = int((time.time() - t0) * 1000)
                error_msg = str(exc)
                logger.warning("Step '%s' attempt %d failed: %s", step.step_id, attempt, error_msg)
                if attempt == 2:
                    self._store.write_event(TraceEvent(
                        run_id=run_id,
                        step_id=step.step_id,
                        agent="executor",
                        event_type="tool_response",
                        duration_ms=duration_ms,
                        payload={"tool": step.tool},
                        error=error_msg,
                        attempt=attempt,
                    ))
                    return None, error_msg

        return None, "unreachable"
