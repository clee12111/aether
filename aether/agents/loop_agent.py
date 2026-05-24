# aether/agents/loop_agent.py

import json
import logging
import re
import time

from aether.agents.llm_client import chat
from aether.agents.planner import _build_schema_block
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

JSON RULES — your output is machine-parsed; these cause hard failures:
- No markdown fences (no ```json), no prose before or after the JSON object.
- No trailing commas inside objects or arrays.
- Never abbreviate arrays with "..." or ellipsis. If a list has 10 items, write all 10.
  WRONG: {"ids": ["TXN-001", "TXN-002", ...]}
  RIGHT: {"ids": ["TXN-001", "TXN-002", "TXN-003"]}
- All string values must use double quotes, not single quotes.
- Boolean values are lowercase: true / false (not True / False).

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


def _build_prompt(state: LoopState, file_paths: list[str] | None = None) -> str:
    # Schema block — exact file paths + column headers so the model can call
    # load_data correctly. Sourced from planner._build_schema_block (same logic).
    if file_paths:
        raw_schema = _build_schema_block(file_paths)
        schema_block = (
            f"{raw_schema}\n"
            "Note: load_data requires BOTH 'file_path' (exact path from the list above)"
            " AND 'table_name' (any short name you choose; used in run_sql queries).\n"
        )
    else:
        schema_block = ""

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
        f"{schema_block}\n"
        f"{ctx_block}\n"
        f"{history_block}\n"
        f"What is the SINGLE next action? Output AgentAction JSON only."
    )


def _extract_json_object(text: str) -> str:
    """Find the outermost {...} block in text, handling nested braces."""
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    # Unclosed brace — return from start to end
    return text[start:]


def _repair_json(text: str) -> str:
    """Best-effort repair of common local-model JSON defects before parsing.

    Repairs applied (in order):
    1. Ellipsis abbreviation: ["a", "b", ...] — strip bare ... tokens so
       the array parses. Mistral emits this instead of enumerating all items.
    2. Trailing commas before ] or } — invalid in JSON, valid in JS/Python.
    3. Truncated boolean/null literals at end of string — model cut off
       mid-token (e.g. "tru" → "true", "fals" → "false", "nul" → "null").
    4. Unclosed braces / brackets — model stopped generating before closing
       the outer object (e.g. output ends with "is_final": true, missing }).
    5. Single-quoted strings → double-quoted (rare but seen).

    Returns the repaired string. If repair leaves the string unchanged, the
    caller will receive the original parse error.
    """
    # 1. Ellipsis: bare ... inside arrays/objects
    #    Handles: , ...] / , ..., / [..., ...] / [... "x"]
    text = re.sub(r",\s*\.\.\.\s*([,\]\}])", r"\1", text)  # ", ..." followed by , ] }
    text = re.sub(r"\[\s*\.\.\.\s*,",        r"[",  text)  # "[..." at array start
    text = re.sub(r",\s*\.\.\.\s*\]",        r"]",  text)  # remaining ", ...]"

    # 2. Trailing commas before ] or }
    text = re.sub(r",\s*(\]|\})", r"\1", text)

    # 3. Truncated literals at end of string (model cut off mid-token)
    text = text.rstrip()
    for full, prefixes in [
        ("true",  ["tru", "tr", "t"]),
        ("false", ["fals", "fal", "fa", "f"]),
        ("null",  ["nul", "nu", "n"]),
    ]:
        for prefix in prefixes:
            if text.endswith(prefix):
                text = text[: -len(prefix)] + full
                break

    # 4. Close unclosed braces/brackets — walk outside strings tracking depth
    _OPENERS = {"{": "}", "[": "]"}
    _CLOSERS = {"}", "]"}
    stack: list[str] = []
    in_str = False
    skip   = False
    for ch in text:
        if skip:
            skip = False
            continue
        if ch == "\\" and in_str:
            skip = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if not in_str:
            if ch in _OPENERS:
                stack.append(_OPENERS[ch])
            elif ch in _CLOSERS and stack and stack[-1] == ch:
                stack.pop()
    text += "".join(reversed(stack))

    # 5. Single-quoted strings → double-quoted (narrow: no internal single quotes)
    text = re.sub(r"'([^']*)'", r'"\1"', text)

    return text


def _parse_action(raw: str) -> AgentAction:
    text = raw.strip()
    # 1. Strip markdown fences
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1).strip()
    # 2. Extract outermost JSON object (discards preamble prose)
    text = _extract_json_object(text)
    # 3. Attempt parse; on failure apply repairs and retry once
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        repaired = _repair_json(text)
        try:
            data = json.loads(repaired)
            logger.info("_parse_action: repaired malformed JSON (len %d→%d)", len(text), len(repaired))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Not valid JSON: {exc}") from exc
    return AgentAction.model_validate(data)


class LoopAgent:
    def __init__(self) -> None:
        self.settings = settings
        self._store = TraceStore(settings.db_path)

    def decide(
        self,
        state: LoopState,
        file_paths: list[str] | None = None,
    ) -> tuple[AgentAction, int, int]:
        """Ask the model for the next single AgentAction.

        Returns (AgentAction, input_tokens, output_tokens).
        Retries up to settings.max_retries on both JSON parse failures and
        empty-tool responses (is_final=False with tool="").
        All LLM trace events are written here.
        """
        step_index = len(state.steps)
        step_id = f"rao_step_{step_index}"
        user_prompt = _build_prompt(state, file_paths=file_paths)
        last_error: str | None = None
        last_bad_action: AgentAction | None = None
        last_in_tok, last_out_tok = 0, 0

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
            last_in_tok, last_out_tok = result.input_tokens, result.output_tokens

            try:
                action = _parse_action(raw)

                # Empty-tool guard: is_final=False with no tool name is not a valid
                # decision. Feed the error back so the model can self-correct.
                if not action.is_final and not action.tool.strip():
                    last_error = (
                        "Your last response had an empty 'tool' field with is_final=false. "
                        "You MUST either name a valid tool "
                        "(load_data, run_sql, flag_item, write_report, retrieve_context) "
                        "or set is_final=true."
                    )
                    last_bad_action = action
                    logger.warning(
                        "Step %d attempt %d: empty tool with is_final=False — retrying",
                        step_index, attempt,
                    )
                    self._store.write_event(TraceEvent.for_validation_error(
                        run_id=state.run_id,
                        agent="loop_agent",
                        attempt=attempt,
                        error=last_error,
                        raw_response=raw[:8000],
                        step_id=step_id,
                    ))
                    continue

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
                    payload={"raw_text": raw[:8000]},
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
                    raw_response=raw[:8000],
                    step_id=step_id,
                ))

        # Retries exhausted. If it was an empty-tool spin, return the last bad
        # action so the loop logs it as a failed step rather than crashing.
        if last_bad_action is not None:
            logger.warning(
                "Step %d: returning empty-tool action after %d retries — loop will log as failed step",
                step_index, self.settings.max_retries,
            )
            return last_bad_action, last_in_tok, last_out_tok

        raise ValueError(
            f"LoopAgent failed to produce a valid AgentAction after "
            f"{self.settings.max_retries} attempts. Last error: {last_error}"
        )
