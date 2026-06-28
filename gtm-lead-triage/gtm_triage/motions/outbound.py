"""OutboundMotion — outbound campaign triage: company research → fit-score → draft.

Trace paths:
  OUTBOUND_DISQUALIFIED — no domain → disqualify immediately
  OUTBOUND_NO_DRAFT     — fit-score cold/disqualified → finalize without drafting
  OUTBOUND_DRAFTED      — hot/warm → two A/B drafts produced
"""

from __future__ import annotations

import logging
from typing import Any

from gtm_triage.models.action import AgentAction, LoopStep, Observation, TriageResult
from gtm_triage.models.campaign import OutboundTarget
from gtm_triage.models.signal import Signal
from gtm_triage.motions.base import Motion
from gtm_triage.trace.store import TraceStore

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a GTM outbound-campaign agent. Given a target company and campaign ICP, you research the company, score fit, and draft outreach. You reason about each step, pick ONE tool, observe the result, then decide next.

DECISION CRITERIA (use these to choose your path — do NOT follow a fixed sequence):

- First: research the target company to build a grounded brief.
- After research: score the company's fit against the campaign ICP.
- If fit_score returns hot or warm: draft two outbound email variants.
- If fit_score returns cold or disqualified: finalize without drafting.
- NEVER draft outreach for cold or disqualified companies.

RULES:
- Output a single AgentAction as strict JSON. No markdown fences. No extra text.
- Pick exactly one tool per response.
- Set is_final=true ONLY when triage is complete.
- Do not repeat a tool call with the same arguments.

AVAILABLE TOOLS:

research_company
  {"domain": "<domain>", "role": "<persona_role>"}
  Research the target company. Returns a grounded brief with what_they_do, industry, size, recent_signals, tech_stack.

fit_score
  {"brief": {<brief dict>}, "campaign": {<campaign dict>}}
  Score the company's ICP fit. Returns tier (hot/warm/cold/disqualified) and reason_codes.

draft_outbound
  {"brief": {<brief dict>}, "campaign": {<campaign dict>}, "persona_role": "<role>", "company": "<company>", "tier": "<tier>"}
  Draft two grounded A/B outbound email variants. ONLY for hot or warm tiers.

OUTPUT FORMAT — strict JSON, no markdown fences:
{
  "reasoning": "<why this action now — reference observations>",
  "tool": "<tool name or empty when is_final>",
  "tool_args": {},
  "is_final": false
}"""


class OutboundMotion(Motion):
    """Outbound campaign triage motion."""

    @property
    def name(self) -> str:
        return "outbound"

    @property
    def input_model(self) -> type:
        return OutboundTarget

    def system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def tool_names(self) -> list[str]:
        return ["research_company", "fit_score", "draft_outbound"]

    def compute_pre_signals(self, signal: Signal) -> dict[str, Any]:
        """Outbound pre-signals: extract campaign/domain info."""
        signals: dict[str, Any] = {}
        if hasattr(signal, "domain"):
            signals["domain"] = signal.domain
        if hasattr(signal, "persona_role"):
            signals["persona_role"] = signal.persona_role
        if hasattr(signal, "campaign"):
            signals["campaign_name"] = signal.campaign.name
        return signals

    def pre_loop_result(
        self,
        signal: Signal,
        pre_signals: dict[str, Any],
    ) -> TriageResult | None:
        # No domain → disqualify
        domain = pre_signals.get("domain", "")
        if not domain:
            result = TriageResult(run_id="", lead_email=signal.email)
            result.final_tier = "disqualified"
            result.final_route = "skip"
            result.trace_path = "OUTBOUND_DISQUALIFIED"
            result.steps = [LoopStep(
                step_index=0,
                action=AgentAction(
                    reasoning="No domain provided. Cannot research company. Disqualifying.",
                    tool="",
                    is_final=True,
                ),
                observation=Observation(),
            )]
            return result
        return None

    def inject_context(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        signal: Signal,
        steps: list[LoopStep],
    ) -> dict[str, Any]:
        args = dict(tool_args)

        if tool_name == "research_company":
            args["domain"] = signal.domain if hasattr(signal, "domain") else ""
            args["role"] = signal.persona_role if hasattr(signal, "persona_role") else ""
            # Don't inject the target's synthetic email — let the researcher
            # use its domain-based fallback (user@domain) for PDL lookup.

        if tool_name == "fit_score":
            # Inject brief from research_company step
            for s in steps:
                if s.action.tool == "research_company" and s.observation.output:
                    args["brief"] = s.observation.output
                    break
            # Inject campaign
            if hasattr(signal, "campaign"):
                args["campaign"] = signal.campaign.model_dump()

        if tool_name == "draft_outbound":
            # Inject brief
            for s in steps:
                if s.action.tool == "research_company" and s.observation.output:
                    args["brief"] = s.observation.output
                    break
            # Inject campaign
            if hasattr(signal, "campaign"):
                args["campaign"] = signal.campaign.model_dump()
            args["persona_role"] = signal.persona_role if hasattr(signal, "persona_role") else ""
            args["company"] = signal.company
            # Inject tier from fit_score
            for s in steps:
                if s.action.tool == "fit_score" and s.observation.output:
                    args["tier"] = s.observation.output.get("tier", "cold")
                    break

        return args

    def post_tool(
        self,
        tool_name: str,
        output: dict[str, Any] | None,
        signal: Signal,
        steps: list[LoopStep],
        pre_signals: dict[str, Any],
        trace_path: str,
        run_id: str,
        trace: TraceStore,
        result: TriageResult,
    ) -> tuple[dict[str, Any] | None, str]:
        if tool_name == "research_company" and output:
            result.enrichment = output

        if tool_name == "fit_score" and output:
            result.score = output
            result.final_tier = output.get("tier")
            result.final_route = output.get("route")
            if result.final_tier in ("cold", "disqualified"):
                trace_path = "OUTBOUND_NO_DRAFT"

        if tool_name == "draft_outbound" and output:
            result.outreach = output
            trace_path = "OUTBOUND_DRAFTED"

        return output, trace_path

    def default_trace_path(self) -> str:
        return "OUTBOUND_DRAFTED"
