# aether/agents/planner.py

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

from aether.agents.llm_client import chat
from aether.config import settings
from aether.models.chunk import Chunk
from aether.models.plan import ExecutionPlan
from aether.models.trace import TraceEvent
from aether.trace.store import TraceStore

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_TEMPLATE = """You are a workflow planner. Given a goal and document context, output a JSON ExecutionPlan.

Available tools: load_data, retrieve_context, run_sql, flag_item, write_report

Output this JSON structure (no markdown fences):
{{
  "plan_id": "<uuid4>",
  "run_id": "<from input>",
  "goal": "<verbatim goal>",
  "reasoning": "<why these steps>",
  "context_used": ["<one line per chunk used>"],
  "steps": [
    {{
      "step_id": "<unique_slug>",
      "name": "<short name>",
      "description": "<what and why>",
      "tool": "<tool name>",
      "tool_args": {{}},
      "depends_on": [],
      "expected_output": "<specific success criterion>",
      "is_optional": false
    }}
  ]
}}

Rules:
- step_id: lowercase letters/digits/underscores only, unique within the plan
- depends_on: only reference step_ids defined earlier in the list
- Last step must be write_report
- SQL rule: never use window functions (SUM OVER, ROW_NUMBER OVER, etc.) inside a WHERE clause. Use a CTE or subquery first, then filter in the outer query.

Example:
{fewshot}
"""


def _load_system_prompt() -> str:
    fewshot_path = Path(settings.prompts_dir) / "planner_fewshots.txt"
    fewshot = fewshot_path.read_text(encoding="utf-8")
    return _SYSTEM_PROMPT_TEMPLATE.format(fewshot=fewshot)


SYSTEM_PROMPT = _load_system_prompt()


class PlannerAgent:
    def __init__(self) -> None:
        self.settings = settings
        self._store = TraceStore(settings.db_path)

    def run(
        self,
        goal: str,
        context_chunks: list[Chunk],
        run_id: str | None = None,
        file_paths: list[str] | None = None,
    ) -> ExecutionPlan:
        run_id = run_id or str(uuid.uuid4())
        schema_block = _build_schema_block(file_paths) if file_paths else ""
        user_prompt = _build_prompt(goal, context_chunks, run_id, schema_block)
        last_error: str | None = None

        for attempt in range(1, settings.max_retries + 1):
            prompt = user_prompt
            if attempt > 1:
                prompt += f"\n\nYour last response failed validation: {last_error}\nFix it and return valid JSON only."

            provider = self.settings.planner_provider
            if provider == "ollama":
                model = self.settings.planner_model_local
            elif provider == "openai":
                model = self.settings.planner_model_openai
            else:
                model = self.settings.planner_model

            logger.info("Planner calling API (attempt %d/%d) provider=%s model=%s",
                        attempt, self.settings.max_retries, provider, model)
            self._store.write_event(TraceEvent.for_llm_call(
                run_id=run_id,
                agent="planner",
                model=model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                attempt=attempt,
            ))

            t0 = time.time()
            result = chat(
                provider=provider,
                model=model,
                system=SYSTEM_PROMPT,
                user=prompt,
                settings=self.settings,
                max_tokens=4096,
            )
            duration_ms = int((time.time() - t0) * 1000)
            raw = result.text
            logger.debug("Tokens: %d in / %d out", result.input_tokens, result.output_tokens)

            try:
                plan = _parse(raw, run_id)
                logger.info("Plan ready: %d steps", len(plan.steps))
                self._store.write_event(TraceEvent(
                    run_id=run_id,
                    agent="planner",
                    event_type="llm_response",
                    model=model,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    duration_ms=duration_ms,
                    payload={"raw_text": raw[:8000]},
                    attempt=attempt,
                ))
                return plan
            except (ValueError, KeyError) as exc:
                last_error = str(exc)
                logger.warning("Attempt %d failed: %s", attempt, exc)
                self._store.write_event(TraceEvent.for_validation_error(
                    run_id=run_id,
                    agent="planner",
                    attempt=attempt,
                    error=last_error,
                    raw_response=raw[:8000],
                ))

        raise ValueError(f"Planner failed after {settings.max_retries} attempts. Last error: {last_error}")


def _build_schema_block(file_paths: list[str]) -> str:
    import pandas as pd

    lines = [
        "CRITICAL: You may ONLY reference files and columns listed below.",
        "Do NOT invent table names, file names, or column names.",
        "",
        "AVAILABLE FILES AND SCHEMAS:",
    ]
    for fp in file_paths:
        name = fp.replace("\\", "/").split("/")[-1]
        ext = os.path.splitext(name)[1].lower()
        if ext == ".csv":
            try:
                cols = list(pd.read_csv(fp, nrows=0).columns)
                lines.append(f"- {name}")
                lines.append(f"  Columns: {', '.join(cols)}")
            except Exception:
                lines.append(f"- {name} (CSV, columns unreadable)")
        elif ext in (".xlsx", ".xls"):
            try:
                cols = list(pd.read_excel(fp, nrows=0).columns)
                lines.append(f"- {name}")
                lines.append(f"  Columns: {', '.join(cols)}")
            except Exception:
                lines.append(f"- {name} (Excel, columns unreadable)")
        else:
            lines.append(f"- {name} (text document)")
    return "\n".join(lines)


def _build_prompt(goal: str, chunks: list[Chunk], run_id: str, schema_block: str = "") -> str:
    parts = []
    for i, chunk in enumerate(chunks, 1):
        fname = chunk.metadata.source_path.replace("\\", "/").split("/")[-1]
        parts.append(f"[Chunk {i}: {fname}]\n{chunk.content}")
    context = "\n---\n".join(parts) if parts else "(no context)"
    schema_section = f"{schema_block}\n\n" if schema_block else ""
    return f"RUN_ID: {run_id}\n\n{schema_section}GOAL: {goal}\n\nCONTEXT:\n{context}\n\nProduce the ExecutionPlan JSON."


def _parse(raw: str, run_id: str) -> ExecutionPlan:
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc
    data["run_id"] = run_id
    return ExecutionPlan.model_validate(data)
