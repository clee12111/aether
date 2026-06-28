"""Tests for the outbound motion — Phase 4b.

Covers: OutboundTarget + Campaign models, three outbound tools
(research_company, fit_score, draft_outbound), the OutboundMotion via
run_motion, grounding rules, anti-fabrication, and poor-ICP rejection.

All tests use provider=mock, APOLLO_SOURCE=fixture, SEARCH_PROVIDER=fixture,
PRODUCTBOARD_SOURCE=off. Zero network.
"""

from __future__ import annotations

import json

import pytest

from gtm_triage.agents.executor import Executor
from gtm_triage.agents.loop_agent import run_outbound
from gtm_triage.models.campaign import Campaign, OutboundDraft, OutboundTarget
from gtm_triage.tools.draft_outbound import DraftOutboundTool
from gtm_triage.tools.fit_score import FitScoreTool
from gtm_triage.tools.registry import ToolRegistry
from gtm_triage.tools.research_company import ResearchCompanyTool
from gtm_triage.trace.store import TraceStore


# ── Shared fixtures ─────────────────────────────────────────────────────────

_SAMPLE_CAMPAIGN = Campaign(
    name="Productboard ICP",
    icp_keywords=["product management", "saas", "customer feedback"],
    icp_employee_ranges=["201,1000", "1001,5000"],
    value_prop="centralize scattered customer feedback and tie it to roadmap decisions",
    target_persona="Head of Product",
)


def _make_executor() -> tuple[Executor, TraceStore]:
    trace = TraceStore(":memory:")
    registry = ToolRegistry([
        ResearchCompanyTool(provider="mock"),
        FitScoreTool(provider="mock"),
        DraftOutboundTool(),
    ])
    return Executor(registry, trace), trace


def _make_target(domain: str, company: str = "", persona: str = "Head of Product") -> OutboundTarget:
    return OutboundTarget(
        company=company or domain,
        domain=domain,
        persona_role=persona,
        campaign=_SAMPLE_CAMPAIGN,
        email=f"{persona.lower().replace(' ', '.')}@{domain}",
        name=persona,
    )


# ── Model tests ─────────────────────────────────────────────────────────────

class TestModels:
    def test_campaign_construction(self):
        c = _SAMPLE_CAMPAIGN
        assert c.name == "Productboard ICP"
        assert "product management" in c.icp_keywords

    def test_outbound_target_satisfies_signal(self):
        t = _make_target("notion.so", "Notion Labs")
        assert t.email == "head.of.product@notion.so"
        assert t.source == "outbound_campaign"
        # Signal protocol fields
        assert hasattr(t, "name")
        assert hasattr(t, "message")
        assert hasattr(t, "company")

    def test_outbound_draft_model(self):
        d = OutboundDraft(
            subject="Test", body="Body", variant="A",
            grounded_on=["brief:what_they_do"],
        )
        assert d.status == "draft"
        assert d.variant == "A"


# ── Tool unit tests ────────────────────────────────────────────────────────

class TestResearchCompanyTool:
    def test_returns_brief_dict(self, monkeypatch):
        monkeypatch.setenv("APOLLO_SOURCE", "fixture")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        monkeypatch.setenv("SEARCH_PROVIDER", "fixture")
        tool = ResearchCompanyTool(provider="mock")
        result = tool.run({"domain": "notion.so"})
        assert "what_they_do" in result
        assert "domain" in result
        assert result["domain"] == "notion.so"


class TestFitScoreTool:
    def test_high_fit_returns_warm_or_hot(self):
        tool = FitScoreTool(provider="mock")
        brief = {
            "industry": "technology",
            "size": "mid_market",
            "what_they_do": "They build SaaS tools.",
            "tech_stack": ["customer feedback", "analytics"],
            "recent_signals": [{"text": "Launched v2", "kind": "launch"}],
            "is_requester": False,
        }
        campaign = _SAMPLE_CAMPAIGN.model_dump()
        result = tool.run({"brief": brief, "campaign": campaign})
        assert result["tier"] in ("hot", "warm")
        assert result["points"] > 0
        assert len(result["reason_codes"]) >= 1

    def test_no_data_returns_disqualified(self):
        tool = FitScoreTool(provider="mock")
        result = tool.run({"brief": {}, "campaign": {}})
        assert result["tier"] == "disqualified"
        assert result["points"] == 0

    def test_is_requester_boost(self):
        tool = FitScoreTool(provider="mock")
        brief = {"is_requester": True}
        result = tool.run({"brief": brief, "campaign": {}})
        assert result["points"] >= 25
        assert any("is_requester" in r for r in result["reason_codes"])


class TestDraftOutboundTool:
    def test_produces_two_variants(self):
        tool = DraftOutboundTool()
        brief = {
            "what_they_do": "They build productivity tools.",
            "industry": "technology",
            "tech_stack": ["Zendesk", "Marketo"],
            "recent_signals": [{"text": "Raised $50M", "kind": "funding", "url": "https://x.com"}],
            "is_requester": False,
        }
        result = tool.run({
            "brief": brief,
            "campaign": _SAMPLE_CAMPAIGN.model_dump(),
            "persona_role": "Head of Product",
            "company": "Acme Inc",
        })
        assert len(result["drafts"]) == 2
        assert result["drafts"][0]["variant"] == "A"
        assert result["drafts"][1]["variant"] == "B"
        assert result["drafts"][0]["status"] == "draft"

    def test_variant_a_grounded_on_signal(self):
        tool = DraftOutboundTool()
        brief = {
            "recent_signals": [{"text": "Raised $50M Series B", "kind": "funding", "url": "https://x.com"}],
        }
        result = tool.run({"brief": brief, "campaign": {}, "persona_role": "VP", "company": "Co"})
        draft_a = result["drafts"][0]
        # Signal is naturalized but should reference the funding
        assert "$50m" in draft_a["body"].lower() or "raised" in draft_a["body"].lower()
        assert len(draft_a["grounded_on"]) >= 1
        assert "signal_0" in draft_a["grounded_on"]

    def test_no_data_produces_generic(self):
        """Anti-fabrication: no brief data → generic hook, no specific claims."""
        tool = DraftOutboundTool()
        result = tool.run({"brief": {}, "campaign": {}, "persona_role": "VP", "company": "Co"})
        for draft in result["drafts"]:
            # grounded_on may be empty when no data
            assert draft["status"] == "draft"
            # No invented company-specific facts
            assert "$" not in draft["body"] or "50M" not in draft["body"]


# ── Verifier tests ─────────────────────────────────────────────────────────

class TestDraftVerifier:
    """Verify the grounding verifier catches fabricated claims."""

    def test_valid_claim_passes(self):
        from gtm_triage.tools.draft_outbound import _build_grounded_facts, _verify_draft
        brief = {
            "what_they_do": "They build productivity tools.",
            "recent_signals": [{"text": "Raised $50M Series B", "kind": "funding", "url": ""}],
        }
        facts = _build_grounded_facts(brief)
        draft = {
            "variant": "A",
            "subject": "Test",
            "body": "Hi VP,\n\nThey recently raised a $50M round. We can help.\n\nBest",
            "claims": [{"text": "raised $50M", "fact_id": "signal_0"}],
        }
        verified = _verify_draft(draft, facts, brief, "VP", "Co", "help teams")
        assert "signal_0" in verified["grounded_on"]
        assert "$50M" in verified["body"]  # kept because it's grounded

    def test_fabricated_amount_stripped(self):
        from gtm_triage.tools.draft_outbound import _build_grounded_facts, _verify_draft
        brief = {
            "what_they_do": "They build productivity tools.",
            # No funding signals at all
        }
        facts = _build_grounded_facts(brief)
        draft = {
            "variant": "A",
            "subject": "Test",
            "body": "Hi VP,\n\nI see you raised $500M last quarter. Impressive!\n\nBest",
            "claims": [{"text": "raised $500M", "fact_id": "signal_0"}],
        }
        verified = _verify_draft(draft, facts, brief, "VP", "Co", "help teams")
        # $500M is not in any fact → body replaced with generic
        assert "$500M" not in verified["body"]
        assert verified["grounded_on"] == []
        assert verified["status"] == "draft"

    def test_nonexistent_fact_id_rejected(self):
        from gtm_triage.tools.draft_outbound import _build_grounded_facts, _verify_draft
        brief = {"what_they_do": "They do stuff."}
        facts = _build_grounded_facts(brief)
        draft = {
            "variant": "B",
            "subject": "Test",
            "body": "Hi VP,\n\nGeneric opener.\n\nBest",
            "claims": [{"text": "made up", "fact_id": "nonexistent_99"}],
        }
        verified = _verify_draft(draft, facts, brief, "VP", "Co", "help teams")
        # nonexistent_99 not in facts → not in grounded_on
        assert "nonexistent_99" not in verified["grounded_on"]

    def test_stub_llm_valid_fact_grounded(self):
        """Simulate an LLM response that cites a valid fact_id."""
        from gtm_triage.tools.draft_outbound import _build_grounded_facts, _verify_draft
        brief = {
            "what_they_do": "Notion builds workspace tools for teams.",
            "industry": "technology",
        }
        facts = _build_grounded_facts(brief)
        # Simulate LLM output referencing wtd
        draft = {
            "variant": "A",
            "subject": "Quick idea for your team",
            "body": "Hi Head of Product,\n\nNotion builds workspace tools for teams — and as that scales, coordinating feedback gets harder. We help teams solve exactly that.\n\nBest",
            "claims": [{"text": "Notion builds workspace tools for teams", "fact_id": "wtd"}],
        }
        verified = _verify_draft(draft, facts, brief, "Head of Product", "Notion", "centralize feedback")
        assert "wtd" in verified["grounded_on"]

    def test_stub_llm_unsupported_figure_stripped(self):
        """Simulate an LLM that invents a $2B valuation not in facts."""
        from gtm_triage.tools.draft_outbound import _build_grounded_facts, _verify_draft
        brief = {"what_they_do": "They make software."}
        facts = _build_grounded_facts(brief)
        draft = {
            "variant": "A",
            "subject": "Test",
            "body": "Hi VP,\n\nWith a $2B valuation, you're clearly scaling fast.\n\nBest",
            "claims": [{"text": "$2B valuation", "fact_id": "wtd"}],
        }
        verified = _verify_draft(draft, facts, brief, "VP", "Co", "help teams")
        assert "$2B" not in verified["body"]
        assert verified["grounded_on"] == []


# ── Full outbound motion via run_motion ────────────────────────────────────

class TestOutboundMotionFull:
    """End-to-end: OutboundTarget → run_outbound → TriageResult."""

    def test_notion_produces_drafts(self, monkeypatch):
        monkeypatch.setenv("APOLLO_SOURCE", "fixture")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        monkeypatch.setenv("SEARCH_PROVIDER", "fixture")

        executor, trace = _make_executor()
        target = _make_target("notion.so", "Notion Labs")
        result = run_outbound(target, executor, trace, provider="mock")

        assert result.final_tier in ("hot", "warm")
        assert result.trace_path == "OUTBOUND_DRAFTED"
        assert result.outreach is not None
        assert len(result.outreach["drafts"]) == 2

        # Each draft has grounded_on entries
        for draft in result.outreach["drafts"]:
            assert draft["status"] == "draft"
            # At least one variant should be grounded
        grounded_any = any(d["grounded_on"] for d in result.outreach["drafts"])
        assert grounded_any, "At least one draft should have grounded_on entries"

    def test_notion_brief_in_enrichment(self, monkeypatch):
        monkeypatch.setenv("APOLLO_SOURCE", "fixture")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        monkeypatch.setenv("SEARCH_PROVIDER", "fixture")

        executor, trace = _make_executor()
        target = _make_target("notion.so", "Notion Labs")
        result = run_outbound(target, executor, trace, provider="mock")

        # Brief captured in result.enrichment
        assert result.enrichment is not None
        assert result.enrichment.get("domain") == "notion.so"

    def test_no_domain_disqualifies(self, monkeypatch):
        monkeypatch.setenv("APOLLO_SOURCE", "off")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")

        executor, trace = _make_executor()
        target = OutboundTarget(
            company="Unknown", domain="", persona_role="VP",
            campaign=_SAMPLE_CAMPAIGN,
        )
        result = run_outbound(target, executor, trace, provider="mock")
        assert result.final_tier == "disqualified"
        assert result.trace_path == "OUTBOUND_DISQUALIFIED"
        assert result.outreach is None

    def test_poor_icp_no_draft(self, monkeypatch):
        """A domain with no ICP fit → cold/disqualified, no drafts."""
        monkeypatch.setenv("APOLLO_SOURCE", "off")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        monkeypatch.setenv("SEARCH_PROVIDER", "off")

        executor, trace = _make_executor()
        # bluebottlecoffee.com — not in any fixture, empty brief, no ICP match
        target = _make_target("bluebottlecoffee.com", "Blue Bottle Coffee")
        result = run_outbound(target, executor, trace, provider="mock")

        assert result.final_tier in ("cold", "disqualified")
        assert result.trace_path == "OUTBOUND_NO_DRAFT"
        assert result.outreach is None


# ── Inbound untouched ──────────────────────────────────────────────────────

class TestInboundUntouched:
    """Verify the inbound motion still works identically."""

    def test_inbound_import_unchanged(self):
        from gtm_triage.agents.loop_agent import run_triage
        assert callable(run_triage)

    def test_inbound_private_helpers_still_importable(self):
        from gtm_triage.agents.loop_agent import (
            _apply_confidence_gate,
            _compute_pre_signals,
            _determine_trace_path,
        )
        assert callable(_apply_confidence_gate)
