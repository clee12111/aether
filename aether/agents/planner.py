# aether/agents/planner.py

import json
import logging
import os
import re
import time
import uuid

import anthropic

from aether.config import settings
from aether.models.chunk import Chunk
from aether.models.plan import ExecutionPlan
from aether.models.trace import TraceEvent
from aether.trace.store import TraceStore

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a financial workflow planner. Given a goal and document context, output a JSON ExecutionPlan.

Available tools: load_data, retrieve_context, run_sql, flag_item, write_report

Output this JSON structure (no markdown fences):
{
  "plan_id": "<uuid4>",
  "run_id": "<from input>",
  "goal": "<verbatim goal>",
  "reasoning": "<why these steps>",
  "context_used": ["<one line per chunk used>"],
  "steps": [
    {
      "step_id": "<unique_slug>",
      "name": "<short name>",
      "description": "<what and why>",
      "tool": "<tool name>",
      "tool_args": {},
      "depends_on": [],
      "expected_output": "<specific success criterion>",
      "is_optional": false
    }
  ]
}

Rules:
- step_id: lowercase letters/digits/underscores only, unique within the plan
- depends_on: only reference step_ids defined earlier in the list
- Last step must be write_report
- SQL rule: never use window functions (SUM OVER, ROW_NUMBER OVER, etc.) inside a WHERE clause. Use a CTE or subquery first, then filter in the outer query.

Example:
Goal: "Check Q4 distributions match pro-rata allocations"
{
  "plan_id": "aaaaaaaa-0000-0000-0000-000000000001",
  "run_id": "run-001",
  "goal": "Check Q4 distributions match pro-rata allocations",
  "reasoning": "Load the data, compute expected shares, flag violations, write report.",
  "context_used": ["Q4 capital accounts CSV"],
  "steps": [
    {
      "step_id": "load_accounts",
      "name": "Load capital accounts",
      "description": "Load Q4 CSV into DuckDB table 'accounts'.",
      "tool": "load_data",
      "tool_args": {"file_path": "q4.csv", "table_name": "accounts"},
      "depends_on": [],
      "expected_output": "Table 'accounts' with investor_id, capital_commitment, q4_distribution.",
      "is_optional": false
    },
    {
      "step_id": "find_violations",
      "name": "Find over-allocated investors",
      "description": "SQL to compare actual vs expected pro-rata distribution.",
      "tool": "run_sql",
      "tool_args": {"sql": "SELECT investor_id, q4_distribution, ROUND(capital_commitment * 1.0 / SUM(capital_commitment) OVER () * SUM(q4_distribution) OVER (), 2) AS expected FROM accounts"},
      "depends_on": ["load_accounts"],
      "expected_output": "Rows showing actual vs expected distribution per investor.",
      "is_optional": false
    },
    {
      "step_id": "write_report",
      "name": "Write reconciliation report",
      "description": "Produce the final report.",
      "tool": "write_report",
      "tool_args": {"title": "Q4 Reconciliation", "format": "json"},
      "depends_on": ["find_violations"],
      "expected_output": "JSON report with summary and flagged items.",
      "is_optional": false
    }
  ]
}
"""


class PlannerAgent:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
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

            logger.info("Planner calling API (attempt %d/%d)", attempt, settings.max_retries)
            self._store.write_event(TraceEvent.for_llm_call(
                run_id=run_id,
                agent="planner",
                model=settings.planner_model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                attempt=attempt,
            ))

            t0 = time.time()
            response = self._client.messages.create(
                model=settings.planner_model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            duration_ms = int((time.time() - t0) * 1000)
            raw = response.content[0].text
            logger.debug("Tokens: %d in / %d out", response.usage.input_tokens, response.usage.output_tokens)

            try:
                plan = _parse(raw, run_id)
                logger.info("Plan ready: %d steps", len(plan.steps))
                self._store.write_event(TraceEvent(
                    run_id=run_id,
                    agent="planner",
                    event_type="llm_response",
                    model=settings.planner_model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    duration_ms=duration_ms,
                    payload={"raw_text": raw[:1000]},
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
                    raw_response=raw[:1000],
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
