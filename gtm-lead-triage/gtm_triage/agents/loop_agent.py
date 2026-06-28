"""RAO (Reason-Act-Observe) loop agent for GTM lead triage.

Phase D: signal-driven branching. Pre-loop checks (email validity, extraction)
determine the trace path. The loop branches on observations — different leads
produce different trace shapes.

Trace paths:
  SHORT_CIRCUIT_INVALID  — invalid/disposable email → disqualify, <=2 steps
  SHORT_CIRCUIT_INTENT   — opt_out/legal intent → disqualify, <=2 steps
  CRM_HIT_SKIP_ENRICH   — CRM has complete profile → skip enrichment
  LOW_CONFIDENCE_GATE    — low-confidence seniority downgraded before scoring
  DIG_DEEPER             — low-confidence enrichment → extra step before scoring
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
from gtm_triage.resilience import retry_with_backoff
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
            # Strip bulky fields (atomic_signals) from observation for prompt brevity
            obs_data = s.observation.output
            if obs_data and "atomic_signals" in obs_data:
                obs_data = {k: v for k, v in obs_data.items() if k != "atomic_signals"}
            obs_str = json.dumps(obs_data)[:400] if obs_data else "(empty)"
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
    They inform the loop's branching decisions. Uses the atomic signal
    extractor with attribution-aware conversion.
    """
    from gtm_triage.enrichment.email_signal import check_email
    from gtm_triage.enrichment.signals import extract_signals_mock, signals_to_extraction_result

    signals: dict = {}

    # Email validity
    email_signal = check_email(lead.email, skip_dns=True)
    signals["email_verdict"] = email_signal.verdict
    signals["email_is_free"] = email_signal.is_free
    signals["email_is_disposable"] = email_signal.is_disposable

    # Atomic signal extraction → attribution-aware conversion
    sig_extraction = extract_signals_mock(
        name=lead.name, message=lead.message, email=lead.email,
    )
    extraction = signals_to_extraction_result(sig_extraction, email=lead.email)
    signals["extracted_intent"] = extraction.intent
    signals["extracted_intent_confidence"] = extraction.intent_confidence
    signals["extracted_seniority"] = extraction.seniority
    signals["extracted_seniority_confidence"] = extraction.seniority_confidence
    signals["extracted_role"] = extraction.role

    return signals


def _determine_trace_path(
    pre_signals: dict,
    crm_found: bool = False,
    crm_complete: bool = False,
    enrichment_low_conf: bool = False,
) -> str:
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

    if enrichment_low_conf:
        return "DIG_DEEPER"

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


def _dig_deeper_enrich(
    lead: Lead,
    first_output: dict,
    run_id: str,
    trace: TraceStore,
) -> dict | None:
    """Attempt a second enrichment source when the first pass left gaps.

    Tries a website-fetch + LLM extraction on the email domain. If it
    produces new data, merges it into the first output (first pass wins
    on conflicts). Returns the merged dict, or None if nothing new.

    This is the EXTRA STEP that makes DIG_DEEPER a real branch — the
    trace shows an additional tool-level action between enrichment and
    scoring that other paths don't have.
    """
    email = lead.email.strip().lower()
    if "@" not in email:
        return None

    domain = email.rsplit("@", 1)[1]

    # Skip for free/disposable domains — no useful website to fetch
    from gtm_triage.enrichment.email_signal import FREE_DOMAINS, DISPOSABLE_DOMAINS
    if domain in FREE_DOMAINS or domain in DISPOSABLE_DOMAINS:
        trace.write(
            run_id=run_id,
            event_type="dig_deeper",
            agent="loop_agent",
            payload={"domain": domain, "skipped": True, "reason": "free_or_disposable_domain"},
        )
        return None

    # Attempt website fallback
    try:
        from gtm_triage.enrichment.waterfall import WebsiteFallback
        fallback = WebsiteFallback()
        website_result = fallback.fetch_and_extract(domain)
        fallback.close()

        # Check if we got anything new
        flat = website_result.to_flat_dict() if hasattr(website_result, "to_flat_dict") else {}
        new_industry = flat.get("industry", "unknown")
        new_size = flat.get("company_size", "unknown")

        got_new_data = (new_industry != "unknown") or (new_size != "unknown")

        trace.write(
            run_id=run_id,
            event_type="dig_deeper",
            agent="loop_agent",
            payload={
                "domain": domain,
                "skipped": False,
                "got_new_data": got_new_data,
                "website_industry": new_industry,
                "website_company_size": new_size,
            },
        )

        if got_new_data:
            # Merge: website fills gaps, first-pass data wins on conflicts
            merged = dict(first_output)
            if merged.get("industry", "unknown") == "unknown" and new_industry != "unknown":
                merged["industry"] = new_industry
                fs = merged.get("field_sources", {})
                fs["industry"] = "website"
                merged["field_sources"] = fs
            if merged.get("company_size", "unknown") == "unknown" and new_size != "unknown":
                merged["company_size"] = new_size
                fs = merged.get("field_sources", {})
                fs["company_size"] = "website"
                merged["field_sources"] = fs
            return merged

    except Exception as exc:
        logger.debug("DIG_DEEPER website fallback failed for %s: %s", domain, exc)
        trace.write(
            run_id=run_id,
            event_type="dig_deeper",
            agent="loop_agent",
            payload={"domain": domain, "skipped": False, "error": str(exc)},
        )

    return None


def _degrade_to_mock(
    lead: Lead,
    result: TriageResult,
    steps: list[LoopStep],
    executor: Executor,
    run_id: str,
    trace: TraceStore,
) -> TriageResult:
    """Fall back to mock-provider scoring when the LLM is unavailable.

    Uses whatever enrichment was already gathered (from prior steps) and
    runs the deterministic scorer (provider=mock, llm_adjustment=0) to
    produce a valid tier/route instead of crashing with a 500.
    """
    from gtm_triage.tools.score_lead import ScoreLeadTool

    enrichment = result.enrichment or {}
    scorer = ScoreLeadTool(provider="mock")
    score_result = scorer.run({
        "email": lead.email,
        "name": lead.name,
        "company": lead.company,
        "message": lead.message,
        "enrichment": enrichment,
    }, run_id=run_id)

    result.score = score_result
    result.final_tier = score_result.get("tier")
    result.final_route = score_result.get("route")

    steps.append(LoopStep(
        step_index=len(steps),
        action=AgentAction(
            reasoning="LLM unavailable after retries — degraded to mock-provider scoring.",
            tool="score_lead",
            tool_args={"degraded": True},
            is_final=True,
        ),
        observation=Observation(output=score_result),
    ))

    trace.write(
        run_id=run_id,
        event_type="tool_call",
        agent="loop_agent",
        payload={"tool": "score_lead", "degraded": True, "tier": result.final_tier},
    )

    return result


def run_triage(
    lead: Lead,
    executor: Executor,
    trace: TraceStore,
    provider: str = "mock",
    model: str = "gpt-4o-mini",
) -> TriageResult:
    """Run the full RAO triage loop on a single lead."""
    run_id = str(uuid.uuid4())

    # Inject run_id into logging context for correlation (K2)
    from gtm_triage.observability.logging import run_id_var
    run_id_token = run_id_var.set(run_id)

    # Initialize Langfuse trace (no-op if keys absent)
    get_trace_span(run_id, metadata={
        "lead_email": lead.email,
        "provider": provider,
        "model": model,
    })

    logger.info(
        "run_start",
        extra={"run_id": run_id, "source": lead.source, "provider": provider},
    )

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
        try:
            chat_result = retry_with_backoff(
                chat,
                provider=provider,
                model=model,
                system=_SYSTEM_PROMPT,
                user=user_prompt,
                max_tokens=1024,
                run_id=run_id,
                generation_name=f"decide-step-{step_idx}",
            )
        except Exception as exc:
            # LLM failed after retries — degrade gracefully instead of 500.
            # Fall back to mock-provider scoring with whatever enrichment we have.
            duration_ms = int((time.time() - t0) * 1000)
            logger.warning("LLM failed after retries at step %d: %s", step_idx, exc)
            trace.write(
                run_id=run_id,
                event_type="llm_call",
                agent="loop_agent",
                payload={"llm_error": str(exc), "step": step_idx, "degraded": True},
                error=str(exc),
                duration_ms=duration_ms,
            )
            result = _degrade_to_mock(lead, result, steps, executor, run_id, trace)
            result.trace_path = trace_path
            result.steps = steps
            _finalize_trace(trace, run_id, result, lead, pre_signals)
            return result
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

            # Enrichment → apply confidence gate + check for DIG_DEEPER
            if action.tool == "enrich_lead" and output:
                output = _apply_confidence_gate(output, pre_signals)
                if output.get("seniority_gated"):
                    trace_path = "LOW_CONFIDENCE_GATE"

                # DIG_DEEPER: enrichment returned but key fields are missing.
                # When BOTH industry and company_size are unknown, take an
                # EXTRA STEP — attempt a second enrichment source (website
                # fetch + LLM read) before proceeding to scoring.
                industry_val = output.get("industry", "unknown")
                size_val = output.get("company_size", "unknown")
                if industry_val == "unknown" and size_val == "unknown":
                    if trace_path == "CLEAN_FULL_PATH":
                        trace_path = "DIG_DEEPER"

                    # Attempt website-fallback enrichment as a second source
                    second_output = _dig_deeper_enrich(lead, output, run_id, trace)
                    if second_output is not None:
                        output = second_output
                        result.enrichment = output

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
    """Write the run_end trace event, emit structured log, and close Langfuse."""
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

    # Structured run_end log — NO PII (no email, name, company, message)
    logger.info(
        "run_end",
        extra={
            "run_id": run_id,
            "final_tier": result.final_tier,
            "final_route": result.final_route,
            "trace_path": result.trace_path,
            "steps_taken": len(result.steps),
        },
    )

    # Reset run_id context var
    from gtm_triage.observability.logging import run_id_var
    run_id_var.set("")

    end_trace(run_id, metadata={
        "lead_email": lead.email,
        "final_tier": result.final_tier,
        "final_route": result.final_route,
        "trace_path": result.trace_path,
    })
