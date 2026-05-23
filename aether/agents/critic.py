# aether/agents/critic.py

import json
import logging
import re
import time
import uuid
from pathlib import Path

from aether.agents.llm_client import chat
from aether.config import settings
from aether.models.critique import CritiqueResult
from aether.models.plan import ExecutionPlan
from aether.models.trace import TraceEvent
from aether.trace.store import TraceStore

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_TEMPLATE = """You are a financial workflow critic. Compare the executor output against the original goal. Return a JSON CritiqueResult — no markdown fences.

Output this exact JSON structure:
{{
  "run_id": "<from input>",
  "goal": "<verbatim goal from input>",
  "overall_verdict": "pass|partial|fail",
  "confidence": 0.0-1.0,
  "flags": [
    {{
      "severity": "critical|warning|info",
      "category": "allocation_mismatch|reconciliation_gap|calculation_error|missing_data|policy_violation|data_quality|other",
      "description": "<specific description of the issue, at least 10 characters>",
      "evidence": "<verbatim excerpt or quantitative discrepancy, at least 5 characters>",
      "step_ref": "<step_id that produced this issue, or null>",
      "suggested_fix": "<optional fix suggestion, or null>"
    }}
  ],
  "summary": "<concise paragraph summarising the critique outcome, at least 20 characters>",
  "recommendations": ["<action 1>", "<action 2>"],
  "steps_reviewed": ["<step_id_1>", "<step_id_2>"]
}}

Rules:
- overall_verdict must be "pass" if there are zero critical flags and the goal is fully met
- overall_verdict must be "fail" if any critical flags exist or the goal is clearly not met
- overall_verdict must be "partial" if the goal is partially met or only warning/info flags exist
- A "pass" verdict with critical flags is invalid — use "partial" or "fail" instead
- confidence reflects certainty in the verdict (0.0 = uncertain, 1.0 = certain)
- flags may be an empty list for a clean run
- summary must be at least 20 characters
- evidence must be a concrete data point, not a vague statement

Example:
{fewshot}
"""


def _load_system_prompt() -> str:
    fewshot_path = Path(settings.prompts_dir) / "critic_fewshots.txt"
    fewshot = fewshot_path.read_text(encoding="utf-8")
    return _SYSTEM_PROMPT_TEMPLATE.format(fewshot=fewshot)


SYSTEM_PROMPT = _load_system_prompt()


class CriticAgent:
    def __init__(self) -> None:
        self.settings = settings
        self._store = TraceStore(settings.db_path)

    def run(
        self,
        goal: str,
        plan: ExecutionPlan,
        executor_output: dict,
        run_id: str,
    ) -> CritiqueResult:
        step_names = [s.name for s in plan.steps]
        user_prompt = (
            f"RUN_ID: {run_id}\n"
            f"GOAL: {goal}\n"
            f"PLAN STEPS: {step_names}\n"
            f"EXECUTOR OUTPUT: {json.dumps(executor_output, default=str)[:2000]}\n\n"
            "Return the CritiqueResult JSON."
        )
        last_error: str | None = None

        for attempt in range(1, settings.max_retries + 1):
            prompt = user_prompt if attempt == 1 else (
                user_prompt + f"\n\nPrevious response failed validation: {last_error}\nFix and return valid JSON only."
            )
            provider = self.settings.critic_provider
            model = (self.settings.critic_model_local if provider == "ollama"
                     else self.settings.critic_model)

            logger.info("Critic calling API (attempt %d/%d) provider=%s model=%s",
                        attempt, self.settings.max_retries, provider, model)
            self._store.write_event(TraceEvent.for_llm_call(
                run_id=run_id,
                agent="critic",
                model=model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                attempt=attempt,
            ))

            t0 = time.time()
            llm_result = chat(
                provider=provider,
                model=model,
                system=SYSTEM_PROMPT,
                user=prompt,
                settings=self.settings,
                max_tokens=1500,
            )
            duration_ms = int((time.time() - t0) * 1000)
            raw = llm_result.text

            try:
                text = raw.strip()
                m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
                data = json.loads(m.group(1) if m else text)
                data["run_id"] = run_id
                data.setdefault("goal", goal)  # required field; model may omit it
                result = CritiqueResult.model_validate(data)
                self._store.write_event(TraceEvent(
                    run_id=run_id,
                    agent="critic",
                    event_type="llm_response",
                    model=model,
                    input_tokens=llm_result.input_tokens,
                    output_tokens=llm_result.output_tokens,
                    duration_ms=duration_ms,
                    payload={"raw_text": raw[:1000]},
                    attempt=attempt,
                ))
                return result
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                logger.warning("Attempt %d failed: %s", attempt, last_error)
                self._store.write_event(TraceEvent.for_validation_error(
                    run_id=run_id,
                    agent="critic",
                    attempt=attempt,
                    error=last_error,
                    raw_response=raw[:1000],
                ))

        raise ValueError(f"Critic failed after {settings.max_retries} attempts. Last error: {last_error}")
