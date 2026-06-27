"""RAO (Reason-Act-Observe) loop agent for GTM lead triage.

Phase D: signal-driven branching. Pre-loop checks (email validity, extraction)
determine the trace path. The loop branches on observations — different leads
produce different trace shapes.

Trace paths:
  SHORT_CIRCUIT_INVALID  — invalid/disposable email → disqualify, <=2 steps
  SHORT_CIRCUIT_INTENT   — opt_out/legal intent → disqualify, <=2 steps
  CRM_HIT_SKIP_ENRICH   — CRM has complete profile → skip enrichment
  LOW_CONFIDENCE_GATE    — low-confidence seniority downgraded before scoring
  CLEAN_FULL_PATH        — high-confidence signals → full pipeline
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

_SYSTEM_PROMPT = """You are a GTM lead-triage agent. Given a new lead and pre-computed signals, you triage it step by step. You reason about each step, pick ONE tool, observe the result, then decide next.

DECISION CRITERIA (use these to choose your path — do NOT follow a fixed sequence):

- If pre-signals show INVALID or DISPOSABLE email: finalize as disqualified immediately.
- If pre-signals show OPT_OUT or LEGAL intent: finalize as disqualified immediately.
- If CRM lookup returns found=true with complete profile (industry, company_size, seniority all present): skip enrichment, go directly to scoring.
- If enrichment returns LOW confidence (<0.50) on key fields: note the uncertainty — do not assume high seniority or intent from weak signals.
- Otherwise: enrich, score, and draft outreach only for hot/warm tiers.

RULES:
- Output a single AgentAction as strict JSON. No markdown fences. No extra text.
- Pick exactly one tool per response.
- Set is_final=true ONLY when triage is complete.
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
  Score the lead 0-100. Returns tier (hot/warm/cold/disqualified) and route.

draft_outreach
  {"email": "<email>", "name": "<name>", "company": "<company>", "enrichment": {<enrichment dict>}, "tier": "<tier>"}
  Draft an outreach email. ONLY for hot or warm tiers. Status is always "draft".

OUTPUT FORMAT — strict JSON, no markdown fences:
{
  "reasoning": "<why this action now — reference observations>",
  "tool": "<tool name or empty when is_final>",
  "tool_args": {},
  "is_final": false
}"""

_MAX_STEPS = 10

# Confidence threshold below which seniority/intent are downgraded
_CONFIDENCE_GATE = 0.50


def _build_user_prompt(lead: Lead, steps: list[LoopStep], pre_signals: dict | None = None) -> str:
    lead_block = (
        f"LEAD:\n"
        f"  email: {lead.email}\n"
        f"  name: {lead.name}\n"
        f"  company: {lead.company}\n"
        f"  message: {lead.message}\n"
        f"  source: {lead.source}\n"
    )

    signals_block = ""
    if pre_signals:
        lines = ["PRE-SIGNALS:"]
        for k, v in pre_signals.items():
            lines.append(f"  {k}: {v}")
        signals_block = "\n".join(lines) + "\n"

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

    return f"{lead_block}\n{signals_block}{history}\nWhat is the SINGLE next action? Output AgentAction JSON only."


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


def _compute_pre_signals(lead: Lead) -> dict:
    """Compute pre-loop signals from the lead's input fields.

    These are cheap, deterministic checks that run before any tool calls.
    They inform the loop's branching decisions.
    """
    from gtm_triage.enrichment.email_signal import check_email
    from gtm_triage.enrichment.extraction import extract_lead_signals

    signals: dict = {}

    # Email validity
    email_signal = check_email(lead.email, skip_dns=True)
    signals["email_verdict"] = email_signal.verdict
    signals["email_is_free"] = email_signal.is_free
    signals["email_is_disposable"] = email_signal.is_disposable

    # Extraction (seniority + intent from the lead's own words)
    extraction = extract_lead_signals(
        name=lead.name, message=lead.message, email=lead.email,
    )
    signals["extracted_intent"] = extraction.intent
    signals["extracted_intent_confidence"] = extraction.intent_confidence
    signals["extracted_seniority"] = extraction.seniority
    signals["extracted_seniority_confidence"] = extraction.seniority_confidence
    signals["extracted_role"] = extraction.role

    return signals


def _determine_trace_path(pre_signals: dict, crm_found: bool = False, crm_complete: bool = False) -> str:
    """Determine which trace path this lead should follow based on signals."""
    # Priority order: short-circuits first, then CRM, then confidence, then clean
    verdict = pre_signals.get("email_verdict", "")
    if verdict in ("invalid", "disposable"):
        return "SHORT_CIRCUIT_INVALID"

    intent = pre_signals.get("extracted_intent", "")
    intent_conf = pre_signals.get("extracted_intent_confidence", 0.0)
    if intent in ("opt_out", "legal_or_compliance") and intent_conf >= _CONFIDENCE_GATE:
        return "SHORT_CIRCUIT_INTENT"

    if crm_found and crm_complete:
        return "CRM_HIT_SKIP_ENRICH"

    sen_conf = pre_signals.get("extracted_seniority_confidence", 0.0)
    seniority = pre_signals.get("extracted_seniority", "")
    if seniority and sen_conf < _CONFIDENCE_GATE:
        return "LOW_CONFIDENCE_GATE"

    return "CLEAN_FULL_PATH"


def _apply_confidence_gate(enrichment_output: dict, pre_signals: dict) -> dict:
    """Downgrade low-confidence seniority/intent before scoring.

    If extraction confidence is below the gate threshold, set the field
    to 'unknown' so it doesn't contribute points on shaky evidence.
    """
    output = dict(enrichment_output)

    sen_conf = pre_signals.get("extracted_seniority_confidence", 0.0)
    if sen_conf < _CONFIDENCE_GATE and output.get("seniority", "unknown") != "unknown":
        output["seniority"] = "unknown"
        output["seniority_gated"] = True

    intent_conf = pre_signals.get("extracted_intent_confidence", 0.0)
    if intent_conf < _CONFIDENCE_GATE and output.get("extracted_intent", "unknown") != "unknown":
        output["extracted_intent"] = "unknown"
        output["intent_gated"] = True

    return output


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

    # ── Pre-loop signal checks ───────────────────────────────────────────
    pre_signals = _compute_pre_signals(lead)

    trace.write(
        run_id=run_id,
        event_type="run_start",
        agent="loop_agent",
        payload={"lead": lead.model_dump(), "pre_signals": pre_signals},
    )

    steps: list[LoopStep] = []
    result = TriageResult(run_id=run_id, lead_email=lead.email)

    # ── Short-circuit: invalid/disposable email ──────────────────────────
    if pre_signals["email_verdict"] in ("invalid", "disposable"):
        result.final_tier = "disqualified"
        result.final_route = "drop"
        result.trace_path = "SHORT_CIRCUIT_INVALID"
        steps.append(LoopStep(
            step_index=0,
            action=AgentAction(
                reasoning=f"Email verdict is {pre_signals['email_verdict']}. Disqualifying immediately.",
                tool="",
                is_final=True,
            ),
            observation=Observation(),
        ))
        result.steps = steps
        _finalize_trace(trace, run_id, result, lead, pre_signals)
        return result

    # ── Short-circuit: opt_out / legal intent ────────────────────────────
    intent = pre_signals.get("extracted_intent", "")
    intent_conf = pre_signals.get("extracted_intent_confidence", 0.0)
    if intent in ("opt_out", "legal_or_compliance") and intent_conf >= _CONFIDENCE_GATE:
        result.final_tier = "disqualified"
        result.final_route = "drop"
        result.trace_path = "SHORT_CIRCUIT_INTENT"
        steps.append(LoopStep(
            step_index=0,
            action=AgentAction(
                reasoning=f"Extracted intent is '{intent}' (confidence {intent_conf:.2f}). Disqualifying — compliance stop.",
                tool="",
                is_final=True,
            ),
            observation=Observation(),
        ))
        result.steps = steps
        _finalize_trace(trace, run_id, result, lead, pre_signals)
        return result

    # ── Full agent loop ──────────────────────────────────────────────────
    # Trace path will be refined after CRM lookup and enrichment.
    trace_path = "CLEAN_FULL_PATH"

    for step_idx in range(_MAX_STEPS):
        step_id = f"step_{step_idx}"
        user_prompt = _build_user_prompt(lead, steps, pre_signals)

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

            # ── Post-tool branching decisions ────────────────────────
            # CRM hit → check if we should skip enrichment
            if action.tool == "crm_lookup" and output and output.get("found"):
                has_industry = bool(output.get("industry"))
                has_size = bool(output.get("company_size"))
                has_seniority = bool(output.get("seniority"))
                if has_industry and has_size and has_seniority:
                    trace_path = "CRM_HIT_SKIP_ENRICH"

            # Enrichment → apply confidence gate
            if action.tool == "enrich_lead" and output:
                output = _apply_confidence_gate(output, pre_signals)
                if output.get("seniority_gated"):
                    trace_path = "LOW_CONFIDENCE_GATE"

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
    result.trace_path = trace_path
    _finalize_trace(trace, run_id, result, lead, pre_signals)
    return result


def _finalize_trace(
    trace: TraceStore,
    run_id: str,
    result: TriageResult,
    lead: Lead,
    pre_signals: dict,
) -> None:
    """Write the run_end trace event and close Langfuse."""
    trace.write(
        run_id=run_id,
        event_type="run_end",
        agent="loop_agent",
        payload={
            "lead_email": lead.email,
            "final_tier": result.final_tier,
            "final_route": result.final_route,
            "trace_path": result.trace_path,
            "steps_taken": len(result.steps),
            "pre_signals": pre_signals,
        },
    )

    end_trace(run_id, metadata={
        "lead_email": lead.email,
        "final_tier": result.final_tier,
        "final_route": result.final_route,
        "trace_path": result.trace_path,
    })
