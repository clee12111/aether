"""InboundMotion — the original inbound-signup triage logic, extracted
verbatim from loop_agent.py into the Motion seam.

Every function body is a character-for-character move; the only change is
that standalone helpers became methods, and `lead` parameters became
`signal` (same duck-typed fields).
"""

from __future__ import annotations

import logging
from typing import Any

from gtm_triage.models.action import AgentAction, LoopStep, Observation, TriageResult
from gtm_triage.models.lead import Lead
from gtm_triage.models.signal import Signal
from gtm_triage.motions.base import Motion
from gtm_triage.trace.store import TraceStore

logger = logging.getLogger(__name__)

# Confidence threshold below which seniority/intent are downgraded
_CONFIDENCE_GATE = 0.50

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


def _compute_pre_signals(lead: Signal) -> dict:
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
    lead: Signal,
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


def _inject_context(
    tool_name: str,
    tool_args: dict,
    lead: Signal,
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


class InboundMotion(Motion):
    """Inbound-signup triage motion — the original hardcoded path."""

    @property
    def name(self) -> str:
        return "inbound"

    @property
    def input_model(self) -> type:
        return Lead

    def system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def tool_names(self) -> list[str]:
        return ["crm_lookup", "enrich_lead", "score_lead", "draft_outreach"]

    def compute_pre_signals(self, signal: Signal) -> dict[str, Any]:
        return _compute_pre_signals(signal)

    def pre_loop_result(
        self,
        signal: Signal,
        pre_signals: dict[str, Any],
    ) -> TriageResult | None:
        # Short-circuit: invalid/disposable email
        if pre_signals["email_verdict"] in ("invalid", "disposable"):
            result = TriageResult(run_id="", lead_email=signal.email)
            result.final_tier = "disqualified"
            result.final_route = "drop"
            result.trace_path = "SHORT_CIRCUIT_INVALID"
            result.steps = [LoopStep(
                step_index=0,
                action=AgentAction(
                    reasoning=f"Email verdict is {pre_signals['email_verdict']}. Disqualifying immediately.",
                    tool="",
                    is_final=True,
                ),
                observation=Observation(),
            )]
            return result

        # Short-circuit: opt_out / legal intent
        intent = pre_signals.get("extracted_intent", "")
        intent_conf = pre_signals.get("extracted_intent_confidence", 0.0)
        if intent in ("opt_out", "legal_or_compliance") and intent_conf >= _CONFIDENCE_GATE:
            result = TriageResult(run_id="", lead_email=signal.email)
            result.final_tier = "disqualified"
            result.final_route = "drop"
            result.trace_path = "SHORT_CIRCUIT_INTENT"
            result.steps = [LoopStep(
                step_index=0,
                action=AgentAction(
                    reasoning=f"Extracted intent is '{intent}' (confidence {intent_conf:.2f}). Disqualifying — compliance stop.",
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
        return _inject_context(tool_name, tool_args, signal, steps)

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
        # CRM hit → check if we should skip enrichment
        if tool_name == "crm_lookup" and output and output.get("found"):
            has_industry = bool(output.get("industry"))
            has_size = bool(output.get("company_size"))
            has_seniority = bool(output.get("seniority"))
            if has_industry and has_size and has_seniority:
                trace_path = "CRM_HIT_SKIP_ENRICH"

        # Enrichment → apply confidence gate + check for DIG_DEEPER
        if tool_name == "enrich_lead" and output:
            output = _apply_confidence_gate(output, pre_signals)
            if output.get("seniority_gated"):
                trace_path = "LOW_CONFIDENCE_GATE"

            # DIG_DEEPER: enrichment returned but key fields are missing.
            industry_val = output.get("industry", "unknown")
            size_val = output.get("company_size", "unknown")
            if industry_val == "unknown" and size_val == "unknown":
                if trace_path == "CLEAN_FULL_PATH":
                    trace_path = "DIG_DEEPER"

                # Attempt website-fallback enrichment as a second source
                second_output = _dig_deeper_enrich(signal, output, run_id, trace)
                if second_output is not None:
                    output = second_output
                    result.enrichment = output

        # Capture results onto TriageResult
        if tool_name == "enrich_lead" and output:
            result.enrichment = output
        elif tool_name == "score_lead" and output:
            result.score = output
            result.final_tier = output.get("tier")
            result.final_route = output.get("route")
        elif tool_name == "draft_outreach" and output:
            result.outreach = output
        elif tool_name == "crm_lookup" and output and output.get("found"):
            result.enrichment = output

        return output, trace_path

    def default_trace_path(self) -> str:
        return "CLEAN_FULL_PATH"
