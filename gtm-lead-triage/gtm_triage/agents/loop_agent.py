"""RAO (Reason-Act-Observe) loop agent for GTM lead triage.

Phase 1.5 changes:
- System prompt updated: skip enrichment if CRM has complete profile, skip draft
  for cold/disqualified.
- Token counts and duration recorded in trace events.
- Provider passed to tools so they can use LLM fallback (openai only).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

from gtm_triage.agents.executor import Executor
from gtm_triage.agents.langfuse_wrapper import end_trace, get_trace_span
from gtm_triage.agents.llm_client import chat
from gtm_triage.models.action import AgentAction, LoopStep, Observation, TriageResult
from gtm_triage.models.lead import Lead
from gtm_triage.trace.store import TraceStore

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a GTM lead-triage agent. Given a new lead, you triage it step by step. You reason about each step, pick ONE tool, observe the result, then decide next.

WORKFLOW:
1. crm_lookup — always first. Check for existing CRM record.
2. enrich_lead — SKIP if crm_lookup returned a complete profile (found=true with industry, company_size, seniority all present). Otherwise enrich the lead.
3. score_lead — score the lead using enrichment data (from enrich_lead or CRM).
4. draft_outreach — ONLY for hot and warm tiers. If tier is cold or disqualified, skip this and finalize immediately.
5. Finalize — set is_final=true when all applicable steps are complete.

RULES:
- Output a single AgentAction as strict JSON. No markdown fences. No extra text.
- Pick exactly one tool per response.
- Set is_final=true ONLY when all applicable triage steps are complete.
- Do not repeat a tool call with the same arguments.

AVAILABLE TOOLS:

crm_lookup
  {"email": "<email>"}
  Look up existing CRM record for this email.

enrich_lead
  {"email": "<email>", "company": "<company>", "name": "<name>", "message": "<message>"}
  Enrich the lead with industry, company size, seniority, business email status.

score_lead
  {"email": "<email>", "name": "<name>", "company": "<company>", "message": "<message>", "enrichment": {<enrichment dict>}, "llm_adjustment": 0}
  Score the lead 0-100 using deterministic rules + bounded LLM nudge (-10..+10).
  Returns tier (hot/warm/cold/disqualified) and route.

draft_outreach
  {"email": "<email>", "name": "<name>", "company": "<company>", "enrichment": {<enrichment dict>}, "tier": "<tier>"}
  Draft an outreach email based on the tier. Status is always "draft" — NEVER sends.
  ONLY call for hot or warm leads. Do NOT call for cold or disqualified.

OUTPUT FORMAT — strict JSON, no markdown fences:
{
  "reasoning": "<why this action now>",
  "tool": "<tool name or empty when is_final>",
  "tool_args": {},
  "is_final": false
}"""

_MAX_STEPS = 10


def _build_user_prompt(lead: Lead, steps: list[LoopStep]) -> str:
    lead_block = (
        f"LEAD:\n"
        f"  email: {lead.email}\n"
        f"  name: {lead.name}\n"
        f"  company: {lead.company}\n"
        f"  message: {lead.message}\n"
        f"  source: {lead.source}\n"
    )

    if steps:
        lines = ["PRIOR STEPS:"]
        for s in steps:
            obs_str = json.dumps(s.observation.output)[:400] if s.observation.output else "(empty)"
            if s.observation.error:
                obs_str = f"ERROR: {s.observation.error}"
            lines.append(
                f'  [{s.step_index}] tool="{s.action.tool}" args={json.dumps(s.action.tool_args)[:300]}\n'
                f"      -> {obs_str}"
            )
        history = "\n".join(lines) + "\n"
    else:
        history = "PRIOR STEPS: (none — first action)\n"

    return f"{lead_block}\n{history}\nWhat is the SINGLE next action? Output AgentAction JSON only."


def _parse_action(raw: str) -> AgentAction:
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    text = text[start:i + 1]
                    break
    data = json.loads(text)
    return AgentAction.model_validate(data)


def _inject_context(
    tool_name: str,
    tool_args: dict,
    lead: Lead,
    steps: list[LoopStep],
) -> dict:
    """Inject known context into tool args."""
    args = dict(tool_args)

    if tool_name in ("crm_lookup", "enrich_lead", "score_lead", "draft_outreach"):
        args["email"] = lead.email

    if tool_name == "enrich_lead":
        args["company"] = lead.company
        args["name"] = lead.name
        args["message"] = lead.message

    if tool_name == "score_lead":
        args["name"] = lead.name
        args["company"] = lead.company
        args["message"] = lead.message
        # Inject enrichment from enrich_lead or CRM lookup
        for s in steps:
            if s.action.tool == "enrich_lead" and s.observation.output:
                args["enrichment"] = s.observation.output
                break
        else:
            # No enrich_lead step — check if CRM had the data
            for s in steps:
                if s.action.tool == "crm_lookup" and s.observation.output:
                    crm_data = s.observation.output
                    if crm_data.get("found"):
                        args["enrichment"] = crm_data
                    break

    if tool_name == "draft_outreach":
        args["name"] = lead.name
        args["company"] = lead.company
        for s in steps:
            if s.action.tool == "enrich_lead" and s.observation.output:
                args["enrichment"] = s.observation.output
                break
        else:
            for s in steps:
                if s.action.tool == "crm_lookup" and s.observation.output:
                    crm_data = s.observation.output
                    if crm_data.get("found"):
                        args["enrichment"] = crm_data
                    break
        for s in steps:
            if s.action.tool == "score_lead" and s.observation.output:
                args["tier"] = s.observation.output.get("tier", "cold")
                break

    return args


def run_triage(
    lead: Lead,
    executor: Executor,
    trace: TraceStore,
    provider: str = "mock",
    model: str = "gpt-4o-mini",
) -> TriageResult:
    """Run the full RAO triage loop on a single lead."""
    run_id = str(uuid.uuid4())

    # Initialize Langfuse trace (no-op if keys absent)
    get_trace_span(run_id, metadata={
        "lead_email": lead.email,
        "provider": provider,
        "model": model,
    })

    trace.write(
        run_id=run_id,
        event_type="run_start",
        agent="loop_agent",
        payload={"lead": lead.model_dump()},
    )

    steps: list[LoopStep] = []
    result = TriageResult(run_id=run_id, lead_email=lead.email)

    for step_idx in range(_MAX_STEPS):
        step_id = f"step_{step_idx}"
        user_prompt = _build_user_prompt(lead, steps)

        t0 = time.time()
        chat_result = chat(
            provider=provider,
            model=model,
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=1024,
            run_id=run_id,
            generation_name=f"decide-step-{step_idx}",
        )
        duration_ms = int((time.time() - t0) * 1000)

        trace.write(
            run_id=run_id,
            event_type="llm_call",
            agent="loop_agent",
            payload={"system_len": len(_SYSTEM_PROMPT), "user_len": len(user_prompt)},
            input_tokens=chat_result.input_tokens,
            output_tokens=chat_result.output_tokens,
            duration_ms=duration_ms,
        )

        try:
            action = _parse_action(chat_result.text)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Step %d parse failed: %s", step_idx, exc)
            trace.write(
                run_id=run_id,
                event_type="llm_call",
                agent="loop_agent",
                payload={"parse_error": str(exc), "raw": chat_result.text[:1000]},
                error=str(exc),
            )
            break

        # Handle is_final with no tool
        if action.is_final and not action.tool.strip():
            steps.append(LoopStep(
                step_index=step_idx,
                action=action,
                observation=Observation(),
            ))
            break

        # Dispatch tool
        if action.tool.strip():
            injected_args = _inject_context(action.tool, action.tool_args, lead, steps)
            t_tool = time.time()
            output, error = executor.dispatch(action.tool, injected_args, run_id, step_id)
            tool_duration = int((time.time() - t_tool) * 1000)
            obs = Observation(output=output, error=error)

            # Record tool-internal LLM token usage in trace
            tool_tokens_in = output.get("llm_tokens_in", 0) if output else 0
            tool_tokens_out = output.get("llm_tokens_out", 0) if output else 0
            if tool_tokens_in or tool_tokens_out:
                trace.write(
                    run_id=run_id,
                    event_type="llm_call",
                    agent=f"tool.{action.tool}",
                    payload={"tool": action.tool, "note": "tool-internal LLM call"},
                    input_tokens=tool_tokens_in,
                    output_tokens=tool_tokens_out,
                    duration_ms=tool_duration,
                )

            if action.tool == "enrich_lead" and output:
                result.enrichment = output
            elif action.tool == "score_lead" and output:
                result.score = output
                result.final_tier = output.get("tier")
                result.final_route = output.get("route")
            elif action.tool == "draft_outreach" and output:
                result.outreach = output
            elif action.tool == "crm_lookup" and output and output.get("found"):
                result.enrichment = output
        else:
            obs = Observation(error="Empty tool name with is_final=False")

        steps.append(LoopStep(
            step_index=step_idx,
            action=action,
            observation=obs,
        ))

        if action.is_final:
            break

    result.steps = steps

    trace.write(
        run_id=run_id,
        event_type="run_end",
        agent="loop_agent",
        payload={
            "lead_email": lead.email,
            "final_tier": result.final_tier,
            "final_route": result.final_route,
            "steps_taken": len(steps),
        },
    )

    # End Langfuse trace (no-op if disabled)
    end_trace(run_id, metadata={
        "lead_email": lead.email,
        "final_tier": result.final_tier,
        "final_route": result.final_route,
    })

    return result
