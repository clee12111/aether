# aether/agents/loop_agent.py

import json
import logging
import re
import time

from aether.agents.llm_client import chat
from aether.config import settings
from aether.models.agent_action import AgentAction, LoopState
from aether.models.trace import TraceEvent
from aether.trace.store import TraceStore

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an autonomous agent. You reason about a goal, pick ONE tool to call, observe the result, then decide the next tool. You continue until the goal is fully satisfied.

RULES:
- Output a single AgentAction as strict JSON. No markdown fences. No extra text.
- Pick exactly one tool per response.
- Set is_final=true ONLY when the goal is completely satisfied. Before finishing:
  * If the goal mentions flagging — you MUST have called flag_item at least once. Do not skip it.
  * You MUST have called write_report to persist the findings.
- Do not repeat a tool call with the same arguments already used.

AVAILABLE TOOLS (use exact tool names and arg keys):

load_data
  {"file_path": "<path or filename>", "table_name": "<name for SQL registry>"}
  Loads a CSV or Excel file. The table_name is used in run_sql queries.

run_sql
  {"sql": "<DuckDB SQL query>"}
  Queries tables loaded by load_data. Rule: never filter on a window function in WHERE — wrap it in a CTE first.

flag_item
  {"item_id": "<identifier e.g. partner name>", "reason": "<why it is flagged>", "severity": "low|medium|high"}
  Flags a single item that violates the goal's criteria. Call once per item that needs flagging.
  REQUIRED when the goal says to flag items — do not skip this.

write_report
  {"title": "<filename without extension>", "format": "json", "results": {<findings dict>}}
  Writes a report file. Call this as the final substantive step before is_final=true.

retrieve_context
  {"query": "<search string>", "top_k": 5}
  Retrieves relevant document chunks from the corpus. Use if you need more context mid-task.

OUTPUT FORMAT — strict JSON, no markdown fences, no extra text:
{
  "reasoning": "<why this action now, given what you have observed>",
  "tool": "<exact tool name, or empty string when is_final is true>",
  "tool_args": {},
  "is_final": false
}"""


def _summarise_observation(output: dict, error: str | None) -> str:
    if error:
        return f"ERROR: {error}"
    s = str(output)
    return s[:200] + ("..." if len(s) > 200 else "")


def _build_prompt(state: LoopState) -> str:
    # Initial context — first chunk up to 400 chars, count the rest
    if state.initial_context:
        first = state.initial_context[0]
        preview = first[:400] + ("..." if len(first) > 400 else "")
        extra = f" [{len(state.initial_context) - 1} more chunk(s)]" if len(state.initial_context) > 1 else ""
        ctx_block = f"INITIAL CONTEXT{extra}:\n{preview}\n"
    else:
        ctx_block = "INITIAL CONTEXT: (none)\n"

    # Prior step history — compact one-block-per-step
    if state.steps:
        lines = ["PRIOR STEPS:"]
        for ls in state.steps:
            reasoning_short = ls.action.reasoning[:80] + ("..." if len(ls.action.reasoning) > 80 else "")
            args_short = str(ls.action.tool_args)[:120] + ("..." if len(str(ls.action.tool_args)) > 120 else "")
            obs_short = _summarise_observation(ls.observation.output, ls.observation.error)
            lines.append(
                f"  [{ls.step_index}] reasoning=\"{reasoning_short}\"\n"
                f"      → {ls.action.tool}({args_short})\n"
                f"      → {obs_short}"
            )
        history_block = "\n".join(lines) + "\n"
    else:
        history_block = "PRIOR STEPS: (none — this is your first action)\n"

    return (
        f"GOAL: {state.goal}\n\n"
        f"{ctx_block}\n"
        f"{history_block}\n"
        f"What is the SINGLE next action? Output AgentAction JSON only."
    )


def _parse_action(raw: str) -> AgentAction:
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc
    return AgentAction.model_validate(data)


class LoopAgent:
    def __init__(self) -> None:
        self.settings = settings
        self._store = TraceStore(settings.db_path)

    def decide(self, state: LoopState) -> tuple[AgentAction, int, int]:
        """Ask the model for the next single AgentAction.

        Returns (AgentAction, input_tokens, output_tokens).
        Retries up to settings.max_retries on validation failure.
        All LLM trace events are written here.
        """
        step_index = len(state.steps)
        step_id = f"rao_step_{step_index}"
        user_prompt = _build_prompt(state)
        last_error: str | None = None

        provider = self.settings.planner_provider
        model = (self.settings.planner_model_local if provider == "ollama"
                 else self.settings.planner_model)

        for attempt in range(1, self.settings.max_retries + 1):
            prompt = user_prompt
            if attempt > 1:
                prompt += (
                    f"\n\nPrevious response failed validation: {last_error}\n"
                    "Fix and return valid AgentAction JSON only."
                )

            logger.info(
                "LoopAgent step %d (attempt %d/%d) provider=%s model=%s",
                step_index, attempt, self.settings.max_retries, provider, model,
            )
            self._store.write_event(TraceEvent.for_llm_call(
                run_id=state.run_id,
                agent="loop_agent",
                model=model,
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=prompt,
                step_id=step_id,
                attempt=attempt,
            ))

            t0 = time.time()
            result = chat(
                provider=provider,
                model=model,
                system=_SYSTEM_PROMPT,
                user=prompt,
                settings=self.settings,
                max_tokens=1024,
            )
            duration_ms = int((time.time() - t0) * 1000)
            raw = result.text

            try:
                action = _parse_action(raw)
                logger.info(
                    "Step %d decision: tool=%r is_final=%s", step_index, action.tool, action.is_final
                )
                self._store.write_event(TraceEvent(
                    run_id=state.run_id,
                    step_id=step_id,
                    agent="loop_agent",
                    event_type="llm_response",
                    model=model,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    duration_ms=duration_ms,
                    payload={"raw_text": raw[:500]},
                    attempt=attempt,
                ))
                return action, result.input_tokens, result.output_tokens

            except (ValueError, KeyError) as exc:
                last_error = str(exc)
                logger.warning("Step %d attempt %d parse failed: %s", step_index, attempt, exc)
                self._store.write_event(TraceEvent.for_validation_error(
                    run_id=state.run_id,
                    agent="loop_agent",
                    attempt=attempt,
                    error=last_error,
                    raw_response=raw[:500],
                    step_id=step_id,
                ))

        raise ValueError(
            f"LoopAgent failed to produce a valid AgentAction after "
            f"{self.settings.max_retries} attempts. Last error: {last_error}"
        )
