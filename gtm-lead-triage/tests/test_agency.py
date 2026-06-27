"""Unit tests for Phase D: signal-driven loop branching.

Tests trace path assignment, confidence gating, and short-circuit behavior.
All offline — zero network calls, provider=mock.
"""

from __future__ import annotations

import pytest

from gtm_triage.agents.executor import Executor
from gtm_triage.agents.loop_agent import (
    _apply_confidence_gate,
    _compute_pre_signals,
    _determine_trace_path,
    run_triage,
)
from gtm_triage.crm.sqlite_crm import SQLiteCRM
from gtm_triage.models.lead import Lead
from gtm_triage.tools.crm_lookup import CRMLookupTool
from gtm_triage.tools.draft_outreach import DraftOutreachTool
from gtm_triage.tools.enrich_lead import EnrichLeadTool
from gtm_triage.tools.registry import ToolRegistry
from gtm_triage.tools.score_lead import ScoreLeadTool
from gtm_triage.trace.store import TraceStore


def _make_executor(crm=None):
    crm = crm or SQLiteCRM(":memory:")
    trace = TraceStore(":memory:")
    registry = ToolRegistry([
        CRMLookupTool(crm),
        EnrichLeadTool(provider="mock"),
        ScoreLeadTool(provider="mock"),
        DraftOutreachTool(),
    ])
    return Executor(registry, trace), trace


# ── Pre-signal computation ─────────────────────────────────────────────────────


class TestPreSignals:
    def test_disposable_email(self):
        lead = Lead(email="x@yopmail.com")
        signals = _compute_pre_signals(lead)
        assert signals["email_verdict"] == "disposable"
        assert signals["email_is_disposable"] is True

    def test_business_email(self):
        lead = Lead(email="test@stripe.com", name="", company="Stripe", message="hi")
        signals = _compute_pre_signals(lead)
        assert signals["email_verdict"] == "deliverable"

    def test_opt_out_intent(self):
        lead = Lead(email="hr@nvidia.com", message="Please take me off your list.")
        signals = _compute_pre_signals(lead)
        assert signals["extracted_intent"] == "opt_out"
        assert signals["extracted_intent_confidence"] >= 0.90

    def test_legal_intent(self):
        lead = Lead(
            email="compliance@jpmorgan.com",
            message="This is a data subject access request under GDPR Article 15.",
        )
        signals = _compute_pre_signals(lead)
        assert signals["extracted_intent"] == "legal_or_compliance"

    def test_seniority_from_message(self):
        lead = Lead(
            email="r.okafor@deloitte.com",
            name="Remi Okafor",
            message="I'm the engagement partner.",
        )
        signals = _compute_pre_signals(lead)
        assert signals["extracted_seniority"] == "c_level"
        assert signals["extracted_seniority_confidence"] >= 0.70


# ── Trace path determination ──────────────────────────────────────────────────


class TestTracePath:
    def test_invalid_email(self):
        signals = {"email_verdict": "invalid"}
        assert _determine_trace_path(signals) == "SHORT_CIRCUIT_INVALID"

    def test_disposable_email(self):
        signals = {"email_verdict": "disposable"}
        assert _determine_trace_path(signals) == "SHORT_CIRCUIT_INVALID"

    def test_opt_out_intent(self):
        signals = {
            "email_verdict": "deliverable",
            "extracted_intent": "opt_out",
            "extracted_intent_confidence": 0.90,
            "extracted_seniority": "",
            "extracted_seniority_confidence": 0.0,
        }
        assert _determine_trace_path(signals) == "SHORT_CIRCUIT_INTENT"

    def test_legal_intent(self):
        signals = {
            "email_verdict": "deliverable",
            "extracted_intent": "legal_or_compliance",
            "extracted_intent_confidence": 0.90,
            "extracted_seniority": "",
            "extracted_seniority_confidence": 0.0,
        }
        assert _determine_trace_path(signals) == "SHORT_CIRCUIT_INTENT"

    def test_crm_hit_complete(self):
        signals = {
            "email_verdict": "deliverable",
            "extracted_intent": "high",
            "extracted_intent_confidence": 0.70,
            "extracted_seniority": "",
            "extracted_seniority_confidence": 0.0,
        }
        assert _determine_trace_path(signals, crm_found=True, crm_complete=True) == "CRM_HIT_SKIP_ENRICH"

    def test_low_confidence_seniority(self):
        signals = {
            "email_verdict": "deliverable",
            "extracted_intent": "medium",
            "extracted_intent_confidence": 0.65,
            "extracted_seniority": "manager",
            "extracted_seniority_confidence": 0.40,
        }
        assert _determine_trace_path(signals) == "LOW_CONFIDENCE_GATE"

    def test_dig_deeper(self):
        signals = {
            "email_verdict": "deliverable",
            "extracted_intent": "medium",
            "extracted_intent_confidence": 0.65,
            "extracted_seniority": "",
            "extracted_seniority_confidence": 0.0,
        }
        assert _determine_trace_path(signals, enrichment_low_conf=True) == "DIG_DEEPER"

    def test_clean_path(self):
        signals = {
            "email_verdict": "deliverable",
            "extracted_intent": "high",
            "extracted_intent_confidence": 0.70,
            "extracted_seniority": "vp",
            "extracted_seniority_confidence": 0.75,
        }
        assert _determine_trace_path(signals) == "CLEAN_FULL_PATH"


# ── Confidence gating ──────────────────────────────────────────────────────────


class TestConfidenceGate:
    def test_low_confidence_seniority_gated(self):
        enrichment = {"seniority": "c_level", "extracted_intent": "high"}
        signals = {
            "extracted_seniority_confidence": 0.40,
            "extracted_intent_confidence": 0.70,
        }
        result = _apply_confidence_gate(enrichment, signals)
        assert result["seniority"] == "unknown"
        assert result["seniority_gated"] is True
        assert result["extracted_intent"] == "high"  # intent NOT gated

    def test_high_confidence_not_gated(self):
        enrichment = {"seniority": "vp", "extracted_intent": "high"}
        signals = {
            "extracted_seniority_confidence": 0.75,
            "extracted_intent_confidence": 0.70,
        }
        result = _apply_confidence_gate(enrichment, signals)
        assert result["seniority"] == "vp"
        assert "seniority_gated" not in result

    def test_unknown_seniority_not_gated(self):
        """If seniority is already unknown, gating is a no-op."""
        enrichment = {"seniority": "unknown"}
        signals = {"extracted_seniority_confidence": 0.0, "extracted_intent_confidence": 0.0}
        result = _apply_confidence_gate(enrichment, signals)
        assert result["seniority"] == "unknown"
        assert "seniority_gated" not in result

    def test_low_confidence_intent_gated(self):
        enrichment = {"seniority": "unknown", "extracted_intent": "medium"}
        signals = {
            "extracted_seniority_confidence": 0.0,
            "extracted_intent_confidence": 0.30,
        }
        result = _apply_confidence_gate(enrichment, signals)
        assert result["extracted_intent"] == "unknown"
        assert result["intent_gated"] is True


# ── End-to-end trace path tests ───────────────────────────────────────────────


class TestEndToEnd:
    def test_disposable_email_short_circuits(self):
        """Disposable email → disqualified in <=2 steps, SHORT_CIRCUIT_INVALID."""
        executor, trace = _make_executor()
        lead = Lead(email="x9z@yopmail.com")
        result = run_triage(lead, executor, trace, provider="mock")

        assert result.final_tier == "disqualified"
        assert result.final_route == "drop"
        assert result.trace_path == "SHORT_CIRCUIT_INVALID"
        assert len(result.steps) <= 2

    def test_invalid_email_short_circuits(self):
        executor, trace = _make_executor()
        lead = Lead(email="not-an-email")
        result = run_triage(lead, executor, trace, provider="mock")

        assert result.final_tier == "disqualified"
        assert result.trace_path == "SHORT_CIRCUIT_INVALID"
        assert len(result.steps) <= 2

    def test_opt_out_short_circuits(self):
        """Opt-out intent → disqualified, SHORT_CIRCUIT_INTENT."""
        executor, trace = _make_executor()
        lead = Lead(
            email="hr@nvidia.com",
            name="Tara Lin",
            company="NVIDIA",
            message="Please take me off your list. I've asked three times already.",
        )
        result = run_triage(lead, executor, trace, provider="mock")

        assert result.final_tier == "disqualified"
        assert result.trace_path == "SHORT_CIRCUIT_INTENT"
        assert len(result.steps) <= 2

    def test_legal_short_circuits(self):
        """Legal/DSAR intent → disqualified, SHORT_CIRCUIT_INTENT."""
        executor, trace = _make_executor()
        lead = Lead(
            email="compliance@jpmorgan.com",
            name="",
            company="JPMorgan Chase",
            message="This is a data subject access request under GDPR Article 15.",
        )
        result = run_triage(lead, executor, trace, provider="mock")

        assert result.final_tier == "disqualified"
        assert result.trace_path == "SHORT_CIRCUIT_INTENT"

    def test_crm_hit_skip_enrich(self):
        """CRM hit with complete profile → skip enrichment."""
        crm = SQLiteCRM(":memory:")
        crm.upsert("carlos@meridian.com", {
            "email": "carlos@meridian.com",
            "company": "Meridian Financial",
            "industry": "financial_services",
            "company_size": "enterprise",
            "seniority": "director",
            "is_business_email": True,
            "is_customer": True,
            "found": True,
        })
        executor, trace = _make_executor(crm)
        lead = Lead(
            email="carlos@meridian.com",
            name="Carlos Reyes",
            company="Meridian Financial",
            message="We need to upgrade our plan.",
        )
        result = run_triage(lead, executor, trace, provider="mock")

        assert result.trace_path == "CRM_HIT_SKIP_ENRICH"
        # enrich_lead should NOT appear in the steps
        tool_sequence = [s.action.tool for s in result.steps if s.action.tool]
        assert "enrich_lead" not in tool_sequence
        assert "crm_lookup" in tool_sequence
        assert "score_lead" in tool_sequence

    def test_crm_hit_routes_on_history(self):
        """CRM hit with existing customer + open opp → routes on history.

        Core GTM path: known customer asking to upgrade should route to AE
        immediately, using the CRM profile (not re-enriching from scratch).
        """
        crm = SQLiteCRM(":memory:")
        crm.upsert("renewal@bigclient.com", {
            "email": "renewal@bigclient.com",
            "company": "Big Client Corp",
            "industry": "financial_services",
            "company_size": "enterprise",
            "seniority": "director",
            "is_business_email": True,
            "is_customer": True,
            "plan": "enterprise",
            "found": True,
        })
        executor, trace = _make_executor(crm)
        lead = Lead(
            email="renewal@bigclient.com",
            name="Dana Rivera, Director of IT",
            company="Big Client Corp",
            message="We need to renew our enterprise contract and add 50 seats.",
        )
        result = run_triage(lead, executor, trace, provider="mock")

        assert result.trace_path == "CRM_HIT_SKIP_ENRICH"
        tool_sequence = [s.action.tool for s in result.steps if s.action.tool]
        assert "enrich_lead" not in tool_sequence
        # Should score hot — existing customer + upgrade intent + director
        assert result.final_tier in ("hot", "warm"), f"Expected hot/warm, got {result.final_tier}"
        # CRM data was used for scoring (enrichment comes from CRM, not enrich_lead)
        assert result.enrichment is not None
        assert result.enrichment.get("is_customer") is True

    def test_dig_deeper_on_low_confidence_enrichment(self):
        """Low-confidence enrichment (unknown industry+size) → DIG_DEEPER trace."""
        executor, trace = _make_executor()
        # A lead at an unknown domain where enrichment returns mostly unknown
        lead = Lead(
            email="contact@obscurefirm.xyz",
            name="Sam Nolan",
            company="Obscure Firm",
            message="We'd like to explore your platform for our team.",
        )
        result = run_triage(lead, executor, trace, provider="mock")

        # The regex enrichment won't know industry or size for "obscurefirm.xyz"
        # so enrichment confidence will be low → DIG_DEEPER
        assert result.trace_path == "DIG_DEEPER", f"Expected DIG_DEEPER, got {result.trace_path}"
        tool_sequence = [s.action.tool for s in result.steps if s.action.tool]
        assert "enrich_lead" in tool_sequence
        assert "score_lead" in tool_sequence

    def test_clean_full_path(self):
        """Normal lead → full path with all tools."""
        executor, trace = _make_executor()
        lead = Lead(
            email="j.martinez@acmefintech.com",
            name="Julia Martinez, VP of Sales",
            company="Acme Fintech International",
            message="We'd like to schedule a demo for our trading desk. Urgent need.",
        )
        result = run_triage(lead, executor, trace, provider="mock")

        assert result.trace_path in ("CLEAN_FULL_PATH", "LOW_CONFIDENCE_GATE")
        tool_sequence = [s.action.tool for s in result.steps if s.action.tool]
        assert "crm_lookup" in tool_sequence
        assert "enrich_lead" in tool_sequence
        assert "score_lead" in tool_sequence

    def test_trace_path_always_populated(self):
        """Every triage result has a non-empty trace_path."""
        executor, trace = _make_executor()
        for email in ["x@yopmail.com", "test@stripe.com", "hr@nvidia.com"]:
            lead = Lead(email=email, message="Please take me off your list." if "nvidia" in email else "hi")
            result = run_triage(lead, executor, trace, provider="mock")
            assert result.trace_path != "", f"Empty trace_path for {email}"


# ── Trace shape diversity ─────────────────────────────────────────────────────


class TestTraceShapeDiversity:
    def test_at_least_five_distinct_paths(self):
        """Run a mix of leads and assert >=5 distinct trace paths."""
        crm = SQLiteCRM(":memory:")
        crm.upsert("known@bigclient.com", {
            "email": "known@bigclient.com",
            "company": "Big Client",
            "industry": "financial_services",
            "company_size": "enterprise",
            "seniority": "director",
            "is_business_email": True,
            "is_customer": True,
            "found": True,
        })
        executor, trace = _make_executor(crm)

        leads = [
            Lead(email="x@yopmail.com"),                                    # SHORT_CIRCUIT_INVALID
            Lead(email="hr@nvidia.com", message="Unsubscribe me."),         # SHORT_CIRCUIT_INTENT
            Lead(email="known@bigclient.com",                               # CRM_HIT_SKIP_ENRICH
                 name="Known Person", company="Big Client",
                 message="Renew our plan."),
            Lead(email="someone@obscurefirm.xyz",                           # DIG_DEEPER
                 message="Exploring your platform."),
            Lead(email="e.brook@lemonade.com",                              # LOW_CONFIDENCE_GATE
                 message="Product on shortlist our CTO shared."),
            Lead(email="j.martinez@acmefintech.com",                        # CLEAN_FULL_PATH
                 name="Julia Martinez, VP of Sales",
                 company="Acme Fintech International",
                 message="Demo please."),
        ]

        paths = set()
        for lead in leads:
            result = run_triage(lead, executor, trace, provider="mock")
            paths.add(result.trace_path)

        assert len(paths) >= 5, f"Only {len(paths)} distinct paths: {paths}"
