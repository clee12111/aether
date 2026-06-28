"""RAO (Reason-Act-Observe) loop agent for GTM lead triage.

Generic motion driver: the loop itself is domain-agnostic. All inbound-
specific logic (system prompt, pre-signals, short-circuits, context
injection, post-tool branching) lives in InboundMotion. A second motion
(outbound) or a new inbound channel becomes a small addition — the loop
doesn't change.

Trace paths (inbound motion):
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
from gtm_triage.models.signal import Signal
from gtm_triage.motions.base import Motion
from gtm_triage.motions.inbound import InboundMotion
from gtm_triage.resilience import retry_with_backoff
from gtm_triage.trace.store import TraceStore

# Re-export inbound helpers so existing imports from loop_agent keep working.
# tests/test_agency.py imports these three by name.
from gtm_triage.motions.inbound import (          # noqa: F401
    _apply_confidence_gate,
    _compute_pre_signals,
    _determine_trace_path,
)

logger = logging.getLogger(__name__)

_MAX_STEPS = 10


def _build_user_prompt(lead: Signal, steps: list[LoopStep], pre_signals: dict | None = None) -> str:
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


def _degrade_to_mock(
    lead: Signal,
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


def _finalize_trace(
    trace: TraceStore,
    run_id: str,
    result: TriageResult,
    lead: Signal,
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


def run_motion(
    signal: Signal,
    motion: Motion,
    executor: Executor,
    trace: TraceStore,
    provider: str = "mock",
    model: str = "gpt-4o-mini",
) -> TriageResult:
    """Run the generic RAO loop driven by a Motion."""
    run_id = str(uuid.uuid4())

    # Inject run_id into logging context for correlation (K2)
    from gtm_triage.observability.logging import run_id_var
    run_id_token = run_id_var.set(run_id)

    # Initialize Langfuse trace (no-op if keys absent)
    get_trace_span(run_id, metadata={
        "lead_email": signal.email,
        "provider": provider,
        "model": model,
    })

    logger.info(
        "run_start",
        extra={"run_id": run_id, "source": signal.source, "provider": provider},
    )

    # ── Pre-loop signal checks ───────────────────────────────────────────
    pre_signals = motion.compute_pre_signals(signal)

    trace.write(
        run_id=run_id,
        event_type="run_start",
        agent="loop_agent",
        payload={"lead": signal.model_dump() if hasattr(signal, "model_dump") else {"email": signal.email}, "pre_signals": pre_signals},
    )

    steps: list[LoopStep] = []
    result = TriageResult(run_id=run_id, lead_email=signal.email)

    # ── Motion short-circuits ───────────────────────────────────────────
    short = motion.pre_loop_result(signal, pre_signals)
    if short is not None:
        short.run_id = run_id
        _finalize_trace(trace, run_id, short, signal, pre_signals)
        return short

    # ── Full agent loop ──────────────────────────────────────────────────
    system_prompt = motion.system_prompt()
    trace_path = motion.default_trace_path()

    # Track tools already called to prevent repeats
    tools_called: set[str] = set()
    non_advancing = 0
    _MAX_NON_ADVANCING = 2

    for step_idx in range(_MAX_STEPS):
        step_id = f"step_{step_idx}"
        user_prompt = _build_user_prompt(signal, steps, pre_signals)

        t0 = time.time()
        try:
            chat_result = retry_with_backoff(
                chat,
                provider=provider,
                model=model,
                system=system_prompt,
                user=user_prompt,
                max_tokens=1024,
                run_id=run_id,
                generation_name=f"decide-step-{step_idx}",
            )
        except Exception as exc:
            # LLM failed after retries — degrade gracefully instead of 500.
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
            result = _degrade_to_mock(signal, result, steps, executor, run_id, trace)
            result.trace_path = trace_path
            result.steps = steps
            _finalize_trace(trace, run_id, result, signal, pre_signals)
            return result
        duration_ms = int((time.time() - t0) * 1000)

        trace.write(
            run_id=run_id,
            event_type="llm_call",
            agent="loop_agent",
            payload={"system_len": len(system_prompt), "user_len": len(user_prompt)},
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

        # Dispatch tool (with dedup guard)
        if action.tool.strip():
            tool_name = action.tool.strip()

            # Guard: if this tool was already called, feed back its prior result
            # and force finalize to prevent runaway loops
            if tool_name in tools_called:
                non_advancing += 1
                logger.info("Tool %s already called (step %d), non_advancing=%d", tool_name, step_idx, non_advancing)
                if non_advancing >= _MAX_NON_ADVANCING:
                    logger.warning("Max non-advancing steps reached at step %d, finalizing", step_idx)
                    steps.append(LoopStep(
                        step_index=step_idx,
                        action=AgentAction(
                            reasoning=f"Tool {tool_name} already called. Finalizing with gathered data.",
                            tool="", is_final=True,
                        ),
                        observation=Observation(),
                    ))
                    break
                # Feed back the prior result as observation without re-calling
                prior_obs = next(
                    (s.observation for s in reversed(steps) if s.action.tool == tool_name),
                    Observation(error=f"{tool_name} already called"),
                )
                steps.append(LoopStep(step_index=step_idx, action=action, observation=prior_obs))
                continue

            tools_called.add(tool_name)
            injected_args = motion.inject_context(action.tool, action.tool_args, signal, steps)
            t_tool = time.time()
            output, error = executor.dispatch(action.tool, injected_args, run_id, step_id)
            tool_duration = int((time.time() - t_tool) * 1000)

            # ── Post-tool branching (delegated to motion) ──────────
            output, trace_path = motion.post_tool(
                action.tool, output, signal, steps, pre_signals,
                trace_path, run_id, trace, result,
            )

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
    _finalize_trace(trace, run_id, result, signal, pre_signals)
    return result


def run_triage(
    lead: Lead,
    executor: Executor,
    trace: TraceStore,
    provider: str = "mock",
    model: str = "gpt-4o-mini",
) -> TriageResult:
    """Run the full RAO triage loop on a single lead (inbound motion)."""
    return run_motion(lead, InboundMotion(), executor, trace, provider, model)


def run_outbound(
    target: Signal,
    executor: Executor,
    trace: TraceStore,
    provider: str = "mock",
    model: str = "gpt-4o-mini",
) -> TriageResult:
    """Run the outbound campaign motion on a target company."""
    from gtm_triage.motions.outbound import OutboundMotion
    return run_motion(target, OutboundMotion(), executor, trace, provider, model)
